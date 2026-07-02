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


class LoopSession:
    """Live state of one topic's problem phase."""

    def __init__(self, topic: str, section: str, theta0: float = 0.0) -> None:
        self.topic = topic
        self.section = section
        self.session = CatSession(
            topic=topic,
            section=section,
            pool=problems.get_pool(topic),
            theta=theta0,
            # 2-3 problems per the PRD's estimate-adjustment cadence.
            stopper=Stopper(target_se=0.5, min_items=2, max_items=3),
        )
        self._pending: str | None = None

    def next_problem(self) -> Item | None:
        if self.session.stopped:
            return None
        item = self.session.next_item()
        self._pending = item.id if item else None
        return item

    def answer(self, problem_id: str, chosen_choice: int) -> None:
        item = problems.get(problem_id)
        if item is None:
            raise ValueError(f"unknown problem: {problem_id}")
        self.session.record_response(item, item.is_correct(chosen_choice))
        self._pending = None

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
