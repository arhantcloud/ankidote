#!/usr/bin/env python3
"""Turn scraped GMAT questions into a py-irt training dataset.

py-irt calibrates item parameters from a *response matrix* (who got which item
right). The scraped questions do not ship with real response data yet, so this
script BOOTSTRAPS a synthetic response set seeded from each question's scraped
difficulty label ("600 level" -> b, etc.), simulating a population of examinees
under a 3PL response process.

This lets the full py-irt -> catsim pipeline run end-to-end today. When real
response logs exist, replace the generated `responses.jsonlines` with the real
one (same format) and keep the rest of the pipeline unchanged. See the PRD
§7.4: parameters are "shipped with the item bank and refined as response data
accumulates."

Outputs (into --outdir):
  * responses.jsonlines  -- py-irt input: {"subject_id", "responses": {item_id: 0/1}}
  * items.json           -- bridge metadata: per-item id + question content +
                            the seed parameters used to simulate.

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


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "section"


def label_to_b(difficulty: str | None, rng: random.Random) -> float:
    """Seed difficulty (b) from a scraped label; fall back to a mild prior."""
    if difficulty:
        m = re.search(r"\d{3}", difficulty)
        if m:
            b = (float(m.group(0)) - DIFF_CENTER) / DIFF_SCALE
            return max(-B_CLAMP, min(B_CLAMP, b))
    # No label: draw from a modest prior centered at the mean difficulty.
    return max(-B_CLAMP, min(B_CLAMP, rng.gauss(0.0, 0.8)))


def num_choices(question: dict) -> int:
    return sum(1 for k in question if k.startswith("answer"))


def prob_correct(theta: float, a: float, b: float, c: float) -> float:
    """3PL probability of a correct response."""
    return c + (1.0 - c) / (1.0 + math.exp(-a * (theta - b)))


def build_items(questions: list[dict], rng: random.Random) -> list[dict]:
    """Assign a stable item_id and seed IRT parameters to each question."""
    items: list[dict] = []
    per_section_counter: dict[str, int] = {}
    for q in questions:
        section_slug = slugify(q.get("section", "section"))
        idx = per_section_counter.get(section_slug, 0) + 1
        per_section_counter[section_slug] = idx
        item_id = f"{section_slug}_{idx}"

        k = max(num_choices(q), 2)
        seed_b = label_to_b(q.get("difficulty"), rng)
        # Discrimination ~ lognormal around 1.0 (kept positive, as IRT requires).
        seed_a = round(math.exp(rng.gauss(0.0, 0.3)), 4)
        # Guessing = 1 / (number of answer choices) for a multiple-choice item.
        seed_c = round(1.0 / k, 4)

        items.append(
            {
                "item_id": item_id,
                "section": q.get("section"),
                "question_name": q.get("question_name"),
                "difficulty_label": q.get("difficulty"),
                "num_choices": k,
                "correct_answer": q.get("correct_answer"),
                "seed_a": seed_a,
                "seed_b": round(seed_b, 4),
                "seed_c": seed_c,
                # Keep the content so to_catsim can emit a self-contained bank.
                "question_text": q.get("question_text"),
                "answers": [q[key] for key in q if key.startswith("answer")],
            }
        )
    return items


def simulate_responses(items: list[dict], n_examinees: int, rng: random.Random):
    """Yield py-irt rows: one simulated examinee per row."""
    for s in range(n_examinees):
        theta = rng.gauss(0.0, 1.0)
        responses = {}
        for it in items:
            p = prob_correct(theta, it["seed_a"], it["seed_b"], it["seed_c"])
            responses[it["item_id"]] = int(rng.random() < p)
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
