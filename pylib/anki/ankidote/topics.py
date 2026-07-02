# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""GMAT topic tree, section weights, and topic selection (PRD §5.1).

The taxonomy is data (derived from the shipped item bank today) so it can grow
without code changes. ``select_topic`` implements the outer-loop rule: pick the
topic whose section-weighted score range sits furthest below the target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki.ankidote.item_bank import SECTION_IDS, get_bank

if TYPE_CHECKING:
    from anki.collection import Collection

# Section contribution toward the GMAT total. Even thirds for v1 (matches the
# diagnostic runner); the calibrated table replaces this later.
SECTION_WEIGHT = {"quant": 1 / 3, "verbal": 1 / 3, "data_insights": 1 / 3}

SECTION_LABELS = {"quant": "Quant", "verbal": "Verbal", "data_insights": "Data Insights"}

# When we have no measured score for a topic we still want the loop to make
# progress; treat unknown topics as sitting at this score so they get picked up.
_UNKNOWN_SCORE = 405


@dataclass
class TopicInfo:
    topic: str
    section: str
    weight: float  # share of the GMAT total this topic carries


def _topics_by_section() -> dict[str, list[str]]:
    bank = get_bank()
    by_section: dict[str, list[str]] = {}
    for topic in bank.topics():
        section = bank.section_for_topic(topic)
        by_section.setdefault(section, []).append(topic)
    return by_section


def topic_tree() -> list[TopicInfo]:
    """All known topics with their weight toward the total score."""
    by_section = _topics_by_section()
    out: list[TopicInfo] = []
    for section, topics in by_section.items():
        share = SECTION_WEIGHT.get(section, 0.0) / max(len(topics), 1)
        for topic in topics:
            out.append(TopicInfo(topic=topic, section=section, weight=share))
    return out


def section_for_topic(topic: str) -> str:
    return get_bank().section_for_topic(topic)


def deck_name(topic: str) -> str:
    """The Anki deck that holds a topic's flashcards (one deck per topic).

    A single flat, colon-free deck per topic (e.g. "Ankidote Arithmetic") so the
    decks read cleanly in the deck list without nested ``::`` separators.
    """
    return f"Ankidote {topic}"


def legacy_deck_names(topic: str) -> list[str]:
    """Older ``Ankidote::Section::Topic`` names, for one-time migration."""
    section = section_for_topic(topic)
    label = SECTION_LABELS.get(section, section.title() or "General")
    return [f"Ankidote::{label}::{topic}"]


def migrate_deck_names(col: Collection) -> None:
    """Rename any legacy nested topic decks to the flat colon-free names."""
    migrated = False
    for info in topic_tree():
        target = deck_name(info.topic)
        if col.decks.id_for_name(target) is not None:
            continue
        for old in legacy_deck_names(info.topic):
            old_id = col.decks.id_for_name(old)
            if old_id is not None:
                col.decks.rename(old_id, target)
                migrated = True
                break
    # Drop the now-empty legacy ``Ankidote`` parent tree if everything moved.
    if migrated:
        root = col.decks.id_for_name("Ankidote")
        if root is not None and not col.find_cards('deck:"Ankidote"'):
            col.decks.remove([root])


def section_id(section: str) -> int:
    return SECTION_IDS.get(section, 0)


def topic_weight(topic: str) -> float:
    for info in topic_tree():
        if info.topic == topic:
            return info.weight
    return 0.0


def _topic_scores(diagnostic: dict | None) -> dict[str, int]:
    """Map topic -> measured midpoint score from the persisted diagnostic."""
    scores: dict[str, int] = {}
    if not diagnostic:
        return scores
    for entry in diagnostic.get("topicScores", []) or []:
        rng = entry.get("score") or {}
        low = rng.get("low")
        high = rng.get("high")
        if low is not None and high is not None:
            scores[entry["topic"]] = round((low + high) / 2)
    return scores


def select_topic(
    diagnostic: dict | None,
    target_score: int,
    *,
    exclude: set[str] | None = None,
) -> TopicInfo | None:
    """Topic whose weighted gap to target is largest (PRD §5.1).

    ``gap = weight × max(0, target − topic_score)``. Untested topics use a
    neutral score so the loop still surfaces them. Ties break toward the
    heavier (more valuable) topic.
    """
    exclude = exclude or set()
    measured = _topic_scores(diagnostic)
    best: TopicInfo | None = None
    best_gap = -1.0
    for info in topic_tree():
        if info.topic in exclude:
            continue
        score = measured.get(info.topic, _UNKNOWN_SCORE)
        gap = info.weight * max(0, target_score - score)
        if gap > best_gap or (gap == best_gap and best and info.weight > best.weight):
            best_gap = gap
            best = info
    return best
