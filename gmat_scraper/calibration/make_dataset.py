#!/usr/bin/env python3
"""Turn scraped GMAT questions into a py-irt training dataset.

py-irt calibrates item parameters from a *response matrix* (who got which item
right). The scraped questions do not ship with real response data yet, so this
script BOOTSTRAPS a synthetic response set seeded from each question's scraped
difficulty label ("600 level" -> b, etc.).

Because Ankidote grades **answer-choice rankings** (the learner ranks the top
choices and earns partial credit), the simulation here draws a *full ranking of
the answer choices* per examinee using a Plackett-Luce process, not just a
coin-flip correct/incorrect. The correct answer's pull grows with ability
(3PL-style), distractors compete according to a mock "attractiveness" order, and
the examinee is scored right iff they rank the true answer first. Those mock
rankings drive the responses fed to py-irt, and the canonical ``choice_order``
they imply is exported so the live CAT can grade real rankings with partial
credit.

When real response logs exist, replace ``responses.jsonlines`` with the real one
(same format) and keep the rest of the pipeline unchanged (PRD §7.4).

Outputs (into --outdir):
  * responses.jsonlines  -- py-irt input: {"subject_id", "responses": {item_id: 0/1}}
  * items.json           -- bridge metadata: per-item id, mapped topic, parsed
                            correct index, mock choice_order, question content,
                            and the seed parameters used to simulate.

py-irt jsonlines format:
  {"subject_id": "sim_0", "responses": {"algebra_1": 1, "algebra_2": 0, ...}}
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import unicodedata

# Map a GMAT "NNN level" difficulty label onto the IRT theta scale.
# 600 -> -1.0, 650 -> 0.0, 700 -> +1.0, 750 -> +2.0 ... then clamped.
DIFF_CENTER = 650.0
DIFF_SCALE = 50.0
B_CLAMP = 3.0

# Scraped section label -> (app section slug, app topic, subtopic). Topics are
# chosen to match the ones the app already ships (pylib/.../item_bank.json) so
# decks/scheduling need no new topics; subtopics keep the finer grain.
SECTION_MAP: dict[str, tuple[str, str, str]] = {
    "GMAT Algebra": ("quant", "Algebra", "Algebra"),
    "GMAT Inequalities": ("quant", "Algebra", "Inequalities"),
    "Number Properties": ("quant", "Number Properties", "Number Properties"),
    "Set Theory": ("quant", "Number Properties", "Set Theory"),
    "Permutation Probability": (
        "quant",
        "Combinatorics & Probability",
        "Permutations & Probability",
    ),
    "Statistics & Average": ("quant", "Statistics", "Statistics & Averages"),
    "Rates Work, Speed": ("quant", "Rates & Work", "Rates, Work & Speed"),
    "Ratio & Percent": ("quant", "Arithmetic", "Ratios & Percents"),
    "Critical Reasoning": ("verbal", "Critical Reasoning", "Critical Reasoning"),
    "EA Sentence Correction": (
        "verbal",
        "Reading Comprehension",
        "Sentence Correction",
    ),
    "Data Sufficiency": ("data_insights", "Data Sufficiency", "Data Sufficiency"),
}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "section"


def map_section(scraped_section: str | None) -> tuple[str, str, str]:
    """Resolve a scraped section label to (app_section, topic, subtopic)."""
    if scraped_section and scraped_section in SECTION_MAP:
        return SECTION_MAP[scraped_section]
    # Unknown label: keep it in quant under a topic named after the label so it
    # is still usable rather than silently dropped.
    topic = (scraped_section or "General").replace("GMAT ", "").strip() or "General"
    return ("quant", topic, topic)


def label_to_b(difficulty: str | None, rng: random.Random) -> float:
    """Seed difficulty (b) from a scraped label; fall back to a mild prior."""
    if difficulty:
        m = re.search(r"\d{3}", difficulty)
        if m:
            b = (float(m.group(0)) - DIFF_CENTER) / DIFF_SCALE
            return max(-B_CLAMP, min(B_CLAMP, b))
    # No label: draw from a modest prior centered at the mean difficulty.
    return max(-B_CLAMP, min(B_CLAMP, rng.gauss(0.0, 0.8)))


def answer_list(question: dict) -> list[str]:
    return [str(question[k]) for k in question if k.startswith("answer")]


def num_choices(question: dict) -> int:
    return max(len(answer_list(question)), 2)


def _numeric(value: str) -> float | None:
    m = re.search(r"-?\d[\d,]*\.?\d*", str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_correct_index(question: dict) -> int:
    """Resolve the 0-based index of the correct choice.

    Scraped ``correct_answer`` looks like ``"C: 60"`` (letter + value) but can
    also be a bare letter or a bare value. Prefer the leading letter, then fall
    back to matching the value against the answer texts.
    """
    answers = answer_list(question)
    if not answers:
        return 0
    raw = str(question.get("correct_answer", "")).strip()

    m = re.match(r"\s*([A-Ea-e])\b", raw)
    if m:
        idx = ord(m.group(1).upper()) - ord("A")
        if 0 <= idx < len(answers):
            return idx

    value = raw.split(":", 1)[1].strip() if ":" in raw else raw
    for i, ans in enumerate(answers):
        if ans.strip() == value:
            return i
    # Numeric match as a last resort (e.g. "60" vs "60.0").
    target = _numeric(value)
    if target is not None:
        for i, ans in enumerate(answers):
            av = _numeric(ans)
            if av is not None and abs(av - target) < 1e-9:
                return i
    return 0


def build_choice_order(answers: list[str], correct_index: int) -> list[int]:
    """Mock canonical best->worst ordering (correct first).

    Distractors follow the correct answer; when the choices are numeric the
    closest wrong values are treated as the most attractive distractors and rank
    just below the correct answer (a plausible ordering for partial-credit
    ranking). Non-numeric choices keep their original order.
    """
    others = [i for i in range(len(answers)) if i != correct_index]
    correct_val = _numeric(answers[correct_index]) if answers else None
    if correct_val is not None and all(_numeric(answers[i]) is not None for i in others):
        others.sort(key=lambda i: abs(_numeric(answers[i]) - correct_val))
    return [correct_index, *others]


def build_items(questions: list[dict], rng: random.Random) -> list[dict]:
    """Assign a stable item_id, mapped topic, and seed IRT parameters."""
    items: list[dict] = []
    per_topic_counter: dict[str, int] = {}
    for q in questions:
        section, topic, subtopic = map_section(q.get("section"))
        topic_slug = slugify(topic)
        idx = per_topic_counter.get(topic_slug, 0) + 1
        per_topic_counter[topic_slug] = idx
        item_id = f"{topic_slug}_{idx}"

        answers = answer_list(q)
        k = max(len(answers), 2)
        correct_index = parse_correct_index(q)
        choice_order = build_choice_order(answers, correct_index)
        seed_b = label_to_b(q.get("difficulty"), rng)
        # Discrimination ~ lognormal around 1.0 (kept positive, as IRT requires).
        seed_a = round(math.exp(rng.gauss(0.0, 0.3)), 4)
        # Guessing = 1 / (number of answer choices) for a multiple-choice item.
        seed_c = round(1.0 / k, 4)

        items.append(
            {
                "item_id": item_id,
                "scraped_section": q.get("section"),
                "section": section,
                "topic": topic,
                "subtopic": subtopic,
                "question_name": q.get("question_name"),
                "difficulty_label": q.get("difficulty"),
                "num_choices": k,
                "correct_answer": q.get("correct_answer"),
                "correct_index": correct_index,
                "choice_order": choice_order,
                "seed_a": seed_a,
                "seed_b": round(seed_b, 4),
                "seed_c": seed_c,
                # Keep the content so downstream stages emit self-contained banks.
                "question_text": q.get("question_text"),
                "answers": answers,
            }
        )
    return items


def _gumbel(rng: random.Random) -> float:
    """A Gumbel(0,1) sample (argmax of Gumbel-perturbed scores == softmax pick)."""
    u = rng.random()
    # Guard against log(0).
    u = min(max(u, 1e-12), 1.0 - 1e-12)
    return -math.log(-math.log(u))


def simulate_ranking(item: dict, theta: float, rng: random.Random) -> list[int]:
    """Draw a full answer-choice ranking for one examinee (Plackett-Luce).

    The correct choice's utility rises with ability (``a·(θ−b)``); distractors
    get a mock attractiveness from their rank in ``choice_order`` so weaker
    students are pulled toward plausible wrong answers. Adding i.i.d. Gumbel
    noise and sorting by utility yields a Plackett-Luce ranking; the top pick's
    correctness follows a 3PL-like ability curve.
    """
    a, b = item["seed_a"], item["seed_b"]
    correct = item["correct_index"]
    order = item["choice_order"]
    # Position of each choice among the distractors (0 = most attractive).
    distractor_rank = {c: r for r, c in enumerate(order[1:])}
    utilities: list[tuple[float, int]] = []
    for choice in range(item["num_choices"]):
        if choice == correct:
            base = a * (theta - b)
        else:
            # Attractive distractors sit a bit above chance; far ones sink.
            base = 0.4 - 0.5 * distractor_rank.get(choice, item["num_choices"])
        utilities.append((base + _gumbel(rng), choice))
    utilities.sort(reverse=True)
    return [choice for _, choice in utilities]


def simulate_responses(items: list[dict], n_examinees: int, rng: random.Random):
    """Yield py-irt rows: one simulated examinee per row.

    Each response is derived from a simulated *ranking* of the answer choices
    (see ``simulate_ranking``): correct iff the examinee ranks the true answer
    first. This keeps the dichotomous py-irt input while making the responses a
    faithful product of the answer-choice-ranking process the app grades.
    """
    for s in range(n_examinees):
        theta = rng.gauss(0.0, 1.0)
        responses = {}
        for it in items:
            ranking = simulate_ranking(it, theta, rng)
            responses[it["item_id"]] = int(ranking[0] == it["correct_index"])
        yield {"subject_id": f"sim_{s}", "responses": responses}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", default="../gmat_questions.json",
                        help="Path to scraped questions JSON.")
    parser.add_argument("--outdir", default="artifacts",
                        help="Directory for responses.jsonlines and items.json.")
    parser.add_argument("--examinees", type=int, default=400,
                        help="Number of synthetic examinees to simulate (default 400).")
    parser.add_argument("--seed", type=int, default=13,
                        help="RNG seed for reproducibility (default 13).")
    args = parser.parse_args()

    with open(args.questions, encoding="utf-8") as fh:
        questions = json.load(fh)
    print(f"Loaded {len(questions)} questions from {args.questions}")

    rng = random.Random(args.seed)
    items = build_items(questions, rng)

    os.makedirs(args.outdir, exist_ok=True)
    items_path = os.path.join(args.outdir, "items.json")
    with open(items_path, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)

    responses_path = os.path.join(args.outdir, "responses.jsonlines")
    with open(responses_path, "w", encoding="utf-8") as fh:
        for row in simulate_responses(items, args.examinees, rng):
            fh.write(json.dumps(row) + "\n")

    print(f"Wrote {len(items)} items -> {items_path}")
    print(f"Wrote {args.examinees} simulated examinees -> {responses_path}")
    print("\nNext: py-irt train 2pl "
          f"{responses_path} {os.path.join(args.outdir, 'pyirt_out')}/ --epochs 1000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
