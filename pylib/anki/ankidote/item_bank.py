# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Loads the shipped item bank and groups it by topic.

For the temp-question milestone the bank is a hand-authored JSON file with
inline question content. The diagnostic-cat-plan (§4.5) eventually splits
this into an offline-calibrated ``item_bank.json`` of parameters plus a
content pointer; the ``Item`` dataclass here already separates IRT
parameters from content so that migration is additive.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

_BANK_PATH = Path(__file__).with_name("item_bank.json")

# Maps the bank's section slug to the numeric section used on the wire (and
# by the Svelte UI's section labels).
SECTION_IDS = {
    "quant": 1,
    "verbal": 2,
    "data_insights": 3,
}


@dataclass(frozen=True)
class Item:
    """A single calibrated diagnostic item."""

    id: str
    section: str
    topic: str
    subtopic: str
    stem: str
    choices: list[str]
    correct: int
    explanation: str
    # 3PL IRT parameters.
    a: float
    b: float
    c: float

    @property
    def section_id(self) -> int:
        return SECTION_IDS.get(self.section, 0)

    @property
    def params(self) -> tuple[float, float, float]:
        return (self.a, self.b, self.c)

    def is_correct(self, chosen_choice: int) -> bool:
        return chosen_choice == self.correct


def _load_items() -> list[Item]:
    raw = json.loads(_BANK_PATH.read_text(encoding="utf-8"))
    # Accept either a bare list (temp bank) or the calibrated
    # ``{"scale": ..., "items": [...]}`` envelope from the offline pipeline.
    records = raw["items"] if isinstance(raw, dict) else raw
    items: list[Item] = []
    for rec in records:
        items.append(
            Item(
                id=str(rec["id"]),
                section=rec["section"],
                topic=rec["topic"],
                subtopic=rec.get("subtopic", ""),
                stem=rec["stem"],
                choices=list(rec["choices"]),
                correct=int(rec["correct"]),
                explanation=rec.get("explanation", ""),
                a=float(rec.get("a", 1.0)),
                b=float(rec.get("b", 0.0)),
                c=float(rec.get("c", 1.0 / max(len(rec["choices"]), 1))),
            )
        )
    return items


class ItemBank:
    """The full item bank, with lookups by id and by topic."""

    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self._by_id = {item.id: item for item in items}
        # Preserve authoring order so topic scheduling is deterministic.
        by_topic: OrderedDict[str, list[Item]] = OrderedDict()
        for item in items:
            by_topic.setdefault(item.topic, []).append(item)
        self._by_topic = by_topic

    def get(self, item_id: str) -> Item | None:
        return self._by_id.get(item_id)

    def topics(self) -> list[str]:
        return list(self._by_topic.keys())

    def items_for_topic(self, topic: str) -> list[Item]:
        return list(self._by_topic.get(topic, []))

    def section_for_topic(self, topic: str) -> str:
        items = self._by_topic.get(topic)
        return items[0].section if items else ""


_BANK: ItemBank | None = None


def get_bank() -> ItemBank:
    """Return the process-wide item bank, loading it on first use."""
    global _BANK
    if _BANK is None:
        _BANK = ItemBank(_load_items())
    return _BANK
