# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Per-topic exam-problem storage (PRD §5.5).

Practice problems reuse the calibrated item bank (real GMAT questions with
py-irt parameters and a canonical ``choice_order`` for answer-choice ranking),
so the loop's per-topic ``CatSession`` re-estimates θ from real, application-
style items. When a topic has no calibrated items yet, we fall back to a
deterministic, seeded arithmetic pool so the loop still runs end-to-end.

Real problems and the diagnostic share the ``Item`` dataclass, so the existing
``CatSession``/``irt`` code works unchanged.
"""

from __future__ import annotations

import random

from anki.ankidote.item_bank import Item, get_bank
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
                    f"[{topic}] Sample practice problem #{i + 1}. Compute {x} {op} {y}."
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
    """Return the problem pool for a topic.

    Prefers the calibrated item bank (real GMAT items); falls back to a
    deterministic generated pool for topics with no calibrated items.
    """
    real = get_bank().items_for_topic(topic)
    if real:
        return list(real)
    if topic not in _pools:
        _pools[topic] = _generate_pool(topic)
    return list(_pools[topic])


def get(problem_id: str) -> Item | None:
    """Look up a single problem by id (calibrated bank id or ``prob::...``)."""
    item = get_bank().get(problem_id)
    if item is not None:
        return item
    try:
        _, topic, _idx = problem_id.split("::")
    except ValueError:
        return None
    for candidate in get_pool(topic):
        if candidate.id == problem_id:
            return candidate
    return None
