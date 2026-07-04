# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The basic study loop (PRD §5, minimal form).

One weak topic at a time: study its Anki cards (native reviewer/FSRS, reused),
then answer 2-3 IRT-selected practice problems to re-estimate the topic's θ and
update the score range. Mistake review, subsumption, ranking, quizzes and
interleaving are later phases; this is the bare `topic → cards → problems →
update` cycle.

The per-topic problem phase reuses the diagnostic ``CatSession`` over the
topic's temp problem pool, so θ re-estimation and item selection come for free.
"""

from __future__ import annotations

import time

from anki.ankidote import problems, scores, topics
from anki.ankidote.engine import CatSession, Stopper
from anki.ankidote.item_bank import Item

# Problems served per topic phase, keyed by the chosen problems/hr pace. A
# faster committed pace means a longer estimate-adjustment set; a relaxed pace
# keeps it short. (min_items keeps the estimate meaningful.)
_PROBLEMS_PER_PACE = {"relaxed": 2, "focused": 3, "intense": 4}

# Partial credit awarded when the correct answer lands at each rank in the
# student's top-3 (answer-choice ranking). Missing entirely scores 0.
_RANK_CREDIT = [1.0, 0.6, 0.3]

# How many misses of the same problem before Note-to-self is offered.
_NOTE_MISS_THRESHOLD = 2


def problems_for_pace(pace: str) -> int:
    return _PROBLEMS_PER_PACE.get(pace, _PROBLEMS_PER_PACE["focused"])


def grade_ranking(item: Item, ranking: list[int]) -> float:
    """Graded score ``g ∈ [0, 1]`` for a top-3 answer-choice ranking.

    Credit is driven by where the student placed the correct answer. When the
    item carries a canonical ``choice_order`` (future authored items), the
    overlap of the student's ordering with it refines the score; otherwise we
    fall back to the position of the correct choice alone.
    """
    if not ranking:
        return 0.0
    correct = item.correct
    if correct in ranking:
        pos = ranking.index(correct)
        base = _RANK_CREDIT[pos] if pos < len(_RANK_CREDIT) else 0.0
    else:
        base = 0.0

    order = getattr(item, "choice_order", None)
    if isinstance(order, list) and order:
        # Reward agreement with the canonical ordering of the ranked choices.
        agree = sum(
            1
            for i, choice in enumerate(ranking)
            if i < len(order) and choice == order[i]
        )
        base = max(base, agree / max(len(_RANK_CREDIT), 1))
    return round(min(1.0, base), 3)


class LoopSession:
    """Live state of one topic's problem phase."""

    def __init__(
        self,
        topic: str,
        section: str,
        theta0: float = 0.0,
        *,
        pace: str = "focused",
    ) -> None:
        self.topic = topic
        self.section = section
        max_items = problems_for_pace(pace)
        self.session = CatSession(
            topic=topic,
            section=section,
            pool=problems.get_pool(topic),
            theta=theta0,
            stopper=Stopper(
                target_se=0.5, min_items=min(2, max_items), max_items=max_items
            ),
        )
        self._pending: str | None = None
        # The last graded item, so the endpoint can build reveal/mistake data.
        self.last_item: Item | None = None
        self.last_verdict: dict | None = None

    def next_problem(self) -> Item | None:
        if self.session.stopped:
            return None
        item = self.session.next_item()
        self._pending = item.id if item else None
        return item

    def answer(
        self,
        problem_id: str,
        chosen_choice: int | None = None,
        *,
        ranking: list[int] | None = None,
        revealed: bool = False,
    ) -> dict:
        """Grade a response, fold it into the estimate, and return a verdict.

        Supports single-choice (``chosen_choice``) and top-3 answer-choice
        ranking (``ranking``). Returns a dict with the graded score, whether it
        counts as correct, and the reveal payload (correct choice + rationale).

        Give-up rule (PRD §4): when ``revealed`` is set (the learner revealed the
        answer or marked "don't know"), the response is scored as not known and
        cannot lift the ability estimate, whatever was submitted.
        """
        item = problems.get(problem_id)
        if item is None:
            raise ValueError(f"unknown problem: {problem_id}")

        if ranking:
            score = grade_ranking(item, ranking)
            correct = bool(ranking and ranking[0] == item.correct)
        else:
            if chosen_choice is None:
                raise ValueError("either chosen_choice or ranking is required")
            correct = item.is_correct(chosen_choice)
            score = 1.0 if correct else 0.0

        if revealed:
            correct = False
            score = 0.0

        self.session.record_response(item, score, revealed=revealed)
        self._pending = None
        self.last_item = item
        # Canonical best->worst ordering for the reveal's side-by-side. Authored
        # items may ship a full ``choice_order``; single-answer items only know
        # the correct choice, so the ideal ranking is just [correct].
        order = getattr(item, "choice_order", None)
        correct_ranking = (
            list(order) if isinstance(order, list) and order else [item.correct]
        )
        verdict = {
            "problemId": item.id,
            "topic": item.topic,
            "correct": correct,
            "score": score,
            "revealed": revealed,
            "chosenChoice": chosen_choice,
            "ranking": list(ranking) if ranking else None,
            "correctChoice": item.correct,
            "correctRanking": correct_ranking,
            "choices": list(item.choices),
            "stem": item.stem,
            "explanation": item.explanation,
        }
        self.last_verdict = verdict
        return verdict

    @property
    def finished(self) -> bool:
        return self.session.stopped

    def result(self) -> dict:
        rng = scores.score_range(self.session.theta, self.session.se)
        return {
            "topic": self.topic,
            "section": self.section,
            "theta": self.session.theta,
            "standardError": self.session.se,
            "score": {"low": rng.low, "high": rng.high},
            "questionsAnswered": self.session.answered,
            "questionsCorrect": self.session.correct,
        }


def _problem_to_wire(item: Item) -> dict:
    return {
        "id": item.id,
        "section": item.section_id,
        "topic": item.topic,
        "subtopic": item.subtopic,
        "stem": item.stem,
        "choices": list(item.choices),
    }


def apply_result(state: dict, result: dict) -> dict:
    """Fold a finished topic result into the persisted ``ankidote`` blob.

    Updates ``diagnostic.topicScores`` for the topic and recomputes the overall
    score range so the dashboard and topic selection immediately reflect the new
    evidence. Returns the mutated ``state``.
    """
    diagnostic = state.get("diagnostic")
    if not isinstance(diagnostic, dict):
        diagnostic = {}
        state["diagnostic"] = diagnostic
    topic_scores = diagnostic.get("topicScores")
    if not isinstance(topic_scores, list):
        topic_scores = []
        diagnostic["topicScores"] = topic_scores

    # Replace or append this topic's entry.
    replaced = False
    for i, entry in enumerate(topic_scores):
        if entry.get("topic") == result["topic"]:
            topic_scores[i] = result
            replaced = True
            break
    if not replaced:
        topic_scores.append(result)

    # Recompute the overall range from all measured topics, weighted by the
    # topic's contribution to the total score.
    estimates = [
        (
            e.get("theta", 0.0),
            e.get("standardError", 1.0),
            topics.topic_weight(e.get("topic", "")),
        )
        for e in topic_scores
        if e.get("questionsAnswered", 0) > 0
    ]
    if estimates:
        theta, se = scores.combine_topics(estimates)
        rng = scores.score_range(theta, se)
        diagnostic["baseline"] = round((rng.low + rng.high) / 2)
        diagnostic["low"] = rng.low
        diagnostic["high"] = rng.high

    # Accumulate real practice tallies so the dashboard's Performance metric and
    # practice history reflect problems actually completed (not a mock).
    progress = state.get("progress")
    if not isinstance(progress, dict):
        progress = {}
        state["progress"] = progress
    answered = int(result.get("questionsAnswered", 0))
    correct = int(result.get("questionsCorrect", 0))
    progress["problemsAnswered"] = int(progress.get("problemsAnswered", 0)) + answered
    progress["problemsCorrect"] = int(progress.get("problemsCorrect", 0)) + correct
    sessions = progress.get("sessions")
    if not isinstance(sessions, list):
        sessions = []
        progress["sessions"] = sessions
    sessions.append(
        {
            "ts": int(time.time() * 1000),
            "topic": result.get("topic", ""),
            "count": answered,
            "correct": correct,
        }
    )
    # Keep the log bounded (it lives in the synced config blob).
    del sessions[:-30]
    return state


# --- Mistake review + note-to-self state (PRD §5.2 / §5.3) ----------------
#
# These live in the persisted ``ankidote`` blob so they sync across devices.
# The loop endpoints call them; keeping them here keeps the problem-phase data
# model in one place.

_MAX_MISTAKES = 100


def record_mistake(state: dict, record: dict) -> dict:
    """Append a mistake to the (bounded, synced) error log."""
    mistakes = state.get("mistakes")
    if not isinstance(mistakes, list):
        mistakes = []
        state["mistakes"] = mistakes
    mistakes.append(record)
    del mistakes[:-_MAX_MISTAKES]
    return state


def build_mistake_record(verdict: dict, why: str, grade: dict, attempts: int) -> dict:
    """A single entry for ``state["mistakes"]`` (the reviewable error log)."""
    return {
        "ts": int(time.time() * 1000),
        "problemId": verdict.get("problemId"),
        "topic": verdict.get("topic"),
        "stem": verdict.get("stem"),
        "chosenChoice": verdict.get("chosenChoice"),
        "ranking": verdict.get("ranking"),
        "correctChoice": verdict.get("correctChoice"),
        "why": why,
        "score": grade.get("score"),
        "aiGraded": bool(grade.get("aiGraded")),
        "attempts": attempts,
    }


def bump_miss(state: dict, key: str) -> int:
    """Increment and return the miss count for a problem (note-to-self gate)."""
    counts = state.get("missCounts")
    if not isinstance(counts, dict):
        counts = {}
        state["missCounts"] = counts
    counts[key] = int(counts.get(key, 0)) + 1
    return counts[key]


def miss_count(state: dict, key: str) -> int:
    counts = state.get("missCounts")
    if not isinstance(counts, dict):
        return 0
    return int(counts.get(key, 0))


# A failed attempt at a GMAT item plus the relearning it triggers costs roughly
# this many minutes; correctly explaining the error now avoids several of those
# future cycles (retrieval-based error correction).
_RELEARN_MINUTES = 3.0


def time_saved_seconds(state: dict, key: str, difficulty: float = 0.0) -> int:
    """Estimated future practice time saved by resolving this mistake now.

    Credits a correct explanation with the relearning cycles it prevents: a
    baseline of a few avoided reps, more when the error has been sticky (missed
    repeatedly) and when the item is harder (higher IRT ``b``).
    """
    misses = max(1, miss_count(state, key))
    difficulty_bonus = max(0, min(4, round(difficulty + 1)))
    reps_avoided = 3 + (misses - 1) + difficulty_bonus
    return int(reps_avoided * _RELEARN_MINUTES * 60)


def note_prompt_due(state: dict, key: str) -> bool:
    """Whether a card/problem has been missed enough to prompt a note-to-self."""
    return miss_count(state, key) >= _NOTE_MISS_THRESHOLD


def save_note(state: dict, key: str, text: str) -> dict:
    notes = state.get("notesToSelf")
    if not isinstance(notes, dict):
        notes = {}
        state["notesToSelf"] = notes
    notes[key] = {"text": text, "ts": int(time.time() * 1000)}
    return state


def get_note(state: dict, key: str) -> dict | None:
    notes = state.get("notesToSelf")
    if not isinstance(notes, dict):
        return None
    note = notes.get(key)
    return note if isinstance(note, dict) else None
