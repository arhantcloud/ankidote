# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Temp flashcards for a topic's Anki deck (PRD §5.3).

Real cards come from the user's own Anki decks. Until those exist, an empty
``Ankidote <Topic>`` deck sends the reviewer straight to the congrats screen, so we
seed a small deterministic set of sample cards per topic the first time it's
studied. These are clearly labelled samples and are added through the normal
note/card pipeline (FSRS + scheduler unchanged); real cards simply replace them.
"""

from __future__ import annotations

import random

_CARDS_PER_TOPIC = 8


def temp_cards(topic: str) -> list[tuple[str, str]]:
    """Deterministic (front, back) pairs for a topic's starter deck."""
    rng = random.Random(f"ankidote-cards::{topic}")
    cards: list[tuple[str, str]] = []
    for i in range(_CARDS_PER_TOPIC):
        x = rng.randint(3, 19)
        y = rng.randint(2, 12)
        cards.append(
            (
                f"[{topic}] Sample fact #{i + 1}<br>What is {x} × {y}?",
                f"{x * y}",
            )
        )
    return cards
