# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""``DiagnosticRunner`` — the multi-topic onboarding diagnostic.

Runs one ``CatSession`` per topic and round-robins among them, always
advancing the topic whose ability estimate is currently least certain
(greedy max-uncertainty scheduling, plan §4.2). This gives content balancing
for free and keeps every per-topic theta a clean unidimensional estimate.
The diagnostic stops when every topic's stopper has fired or a global item
cap (a proxy for the ~time budget) is reached.
"""

from __future__ import annotations

from anki.ankidote import scores
from anki.ankidote.engine import CatSession, Stopper
from anki.ankidote.item_bank import Item, ItemBank, get_bank

# Confidence slider (1-5) -> starting theta warm start (plan §4.3).
_CONFIDENCE_THETA = {1: -1.5, 2: -0.75, 3: 0.0, 4: 0.75, 5: 1.5}

# Section weights toward the GMAT total. Even thirds for v1; the calibrated
# table replaces this later.
_SECTION_WEIGHT = {"quant": 1 / 3, "verbal": 1 / 3, "data_insights": 1 / 3}


def _seed_theta(confidence: int | None) -> float:
    if confidence is None:
        return 0.0
    return _CONFIDENCE_THETA.get(int(confidence), 0.0)


class DiagnosticRunner:
    """Holds the live state of one onboarding diagnostic attempt."""

    def __init__(
        self,
        bank: ItemBank | None = None,
        *,
        confidence: dict[str, int] | None = None,
        max_questions: int | None = None,
    ) -> None:
        self.bank = bank or get_bank()
        confidence = confidence or {}

        self.sessions: dict[str, CatSession] = {}
        for topic in self.bank.topics():
            pool = self.bank.items_for_topic(topic)
            section = pool[0].section if pool else ""
            # Confidence may be keyed by topic or, more coarsely, by section.
            conf = confidence.get(topic, confidence.get(section))
            self.sessions[topic] = CatSession(
                topic=topic,
                section=section,
                pool=pool,
                theta=_seed_theta(conf),
                stopper=Stopper(target_se=0.45, min_items=1, max_items=3),
            )

        # Precompute topic weights (section weight split evenly across its
        # topics) for the overall-score aggregation.
        topics_per_section: dict[str, int] = {}
        for topic in self.bank.topics():
            section = self.bank.section_for_topic(topic)
            topics_per_section[section] = topics_per_section.get(section, 0) + 1
        self._topic_weight = {
            topic: _SECTION_WEIGHT.get(self.bank.section_for_topic(topic), 0.0)
            / max(topics_per_section[self.bank.section_for_topic(topic)], 1)
            for topic in self.bank.topics()
        }

        total_items = len(self.bank.items)
        self.max_questions = (
            max_questions if max_questions is not None else min(total_items, 16)
        )
        # Maps an outstanding item back to the session it was drawn from.
        self._pending: dict[str, str] = {}

    # -- progress -----------------------------------------------------------

    @property
    def answered(self) -> int:
        return sum(s.answered for s in self.sessions.values())

    @property
    def finished(self) -> bool:
        if self.answered >= self.max_questions:
            return True
        return all(s.stopped for s in self.sessions.values())

    # -- driving the test ---------------------------------------------------

    def next_item(self) -> Item | None:
        """The next question to present, or None if the diagnostic is done."""
        if self.finished:
            return None
        open_sessions = [s for s in self.sessions.values() if not s.stopped]
        if not open_sessions:
            return None
        # Highest uncertainty first; break ties toward the least-sampled topic
        # so early questions spread across topics.
        session = max(open_sessions, key=lambda s: (s.se, -s.answered))
        item = session.next_item()
        if item is None:
            return None
        self._pending[item.id] = session.topic
        return item

    def answer(self, item_id: str, chosen_choice: int) -> None:
        """Grade a response and fold it into the right topic session."""
        item = self.bank.get(item_id)
        if item is None:
            raise ValueError(f"unknown item: {item_id}")
        topic = self._pending.pop(item_id, item.topic)
        session = self.sessions[topic]
        session.record_response(item, item.is_correct(chosen_choice))

    # -- reporting ----------------------------------------------------------

    def topic_states(self) -> list[dict]:
        out = []
        for topic, session in self.sessions.items():
            if session.answered == 0:
                continue
            rng = scores.score_range(session.theta, session.se)
            out.append(
                {
                    "topic": topic,
                    "section": session.section,
                    "theta": session.theta,
                    "standardError": session.se,
                    "score": {"low": rng.low, "high": rng.high},
                    "questionsAnswered": session.answered,
                    "questionsCorrect": session.correct,
                }
            )
        return out

    def overall(self) -> tuple[float, float]:
        estimates = [
            (s.theta, s.se, self._topic_weight.get(topic, 0.0))
            for topic, s in self.sessions.items()
            if s.answered > 0
        ]
        return scores.combine_topics(estimates)

    def state(self) -> dict:
        """Full snapshot for the wire protocol / UI."""
        theta, se = self.overall()
        rng = scores.score_range(theta, se)
        item = None
        if not self.finished:
            item = self.next_item()
        return {
            "finished": self.finished,
            "answered": self.answered,
            "maxQuestions": self.max_questions,
            "theta": theta,
            "standardError": se,
            "score": {"low": rng.low, "high": rng.high},
            "question": _item_to_wire(item) if item else None,
            "topicScores": self.topic_states(),
        }


def _item_to_wire(item: Item) -> dict:
    """Question stripped of its answer, for presentation."""
    return {
        "id": item.id,
        "section": item.section_id,
        "topic": item.topic,
        "subtopic": item.subtopic,
        "stem": item.stem,
        "choices": list(item.choices),
    }
