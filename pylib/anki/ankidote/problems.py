# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Per-topic exam-problem storage (PRD §5.5).

Practice problems are a **separate store** from both the Anki flashcard decks
(cards measure recall) and the diagnostic item bank (problems measure
application). For now problems are **generated temporarily** — deterministic,
seeded arithmetic MCQs tagged with the topic — so the loop is fully functional
before authored/calibrated problems are ingested. Real problems drop in behind
the same ``get_pool`` / ``get`` interface with no caller changes.

Each problem carries 3PL IRT parameters and reuses the diagnostic ``Item``
dataclass so the existing ``CatSession``/``irt`` code can re-estimate θ from a
handful of them.
"""

from __future__ import annotations

import random

from anki.ankidote.item_bank import Item
from anki.ankidote.topics import section_for_topic

# Size of the deterministic pool generated per topic. A loop iteration draws
# 2-3 of these; the pool is large enough to avoid immediate repeats.
_POOL_SIZE = 12

_pools: dict[str, list[Item]] = {}


def _difficulty_for(index: int) -> float:
    """Spread problem difficulty across the pool (b in roughly [-1.5, 1.5])."""
    if _POOL_SIZE <= 1:
        return 0.0
    return -1.5 + 3.0 * (index / (_POOL_SIZE - 1))


def _generate_pool(topic: str) -> list[Item]:
    """A stable, deterministic pool of temp problems for a topic."""
    section = section_for_topic(topic) or "quant"
    # Seed off the topic name so the pool is identical across runs/devices.
    rng = random.Random(f"ankidote-problems::{topic}")
    pool: list[Item] = []
    for i in range(_POOL_SIZE):
        x = rng.randint(6, 49)
        y = rng.randint(2, 19)
        op = rng.choice(["+", "-", "×"])
        if op == "+":
            answer = x + y
        elif op == "-":
            answer = x - y
        else:
            answer = x * y
        # Build four plausible distractors around the answer.
        offsets = {answer}
        choices = [answer]
        while len(choices) < 5:
            delta = rng.choice([-9, -5, -3, -2, -1, 1, 2, 3, 5, 9])
            cand = answer + delta
            if cand not in offsets:
                offsets.add(cand)
                choices.append(cand)
        rng.shuffle(choices)
        correct = choices.index(answer)
        pool.append(
            Item(
                id=f"prob::{topic}::{i}",
                section=section,
                topic=topic,
                subtopic="Practice",
                stem=(
                    f"[{topic}] Sample practice problem #{i + 1}. "
                    f"Compute {x} {op} {y}."
                ),
                choices=[str(c) for c in choices],
                correct=correct,
                explanation=f"{x} {op} {y} = {answer}.",
                a=1.0,
                b=_difficulty_for(i),
                c=0.2,
            )
        )
    return pool


def get_pool(topic: str) -> list[Item]:
    """Return (and memoize) the temp problem pool for a topic."""
    if topic not in _pools:
        _pools[topic] = _generate_pool(topic)
    return list(_pools[topic])


def get(problem_id: str) -> Item | None:
    """Look up a single problem by id (e.g. ``prob::Algebra::3``)."""
    try:
        _, topic, _idx = problem_id.split("::")
    except ValueError:
        return None
    for item in get_pool(topic):
        if item.id == problem_id:
            return item
    return None
