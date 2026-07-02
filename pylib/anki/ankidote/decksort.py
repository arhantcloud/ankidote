# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Sort the collection's existing cards into one deck per GMAT topic.

The study loop studies a single per-topic deck at a time, so we distribute the
user's current cards across the flat ``Ankidote <Topic>`` decks, aiming
for roughly ``per_topic`` cards each. It is intentionally approximate ("around
50 each") — cards are dealt round-robin under a per-topic cap, so a small
collection spreads evenly and a large one fills each topic to the cap and leaves
the remainder untouched. Real topic tagging replaces this heuristic later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anki.ankidote import topics

if TYPE_CHECKING:
    from anki.collection import Collection


def sort_into_topics(col: Collection, per_topic: int = 50) -> dict[str, int]:
    """Deal existing cards into the per-topic decks; return per-topic counts."""
    topic_infos = topics.topic_tree()
    topic_names = [info.topic for info in topic_infos]

    # Fold any legacy nested decks into the flat names before (re)creating.
    topics.migrate_deck_names(col)
    deck_ids = {t: col.decks.id(topics.deck_name(t)) for t in topic_names}

    # Source = every card not already in one of our topic decks, in a stable
    # order (each flat deck is excluded explicitly since they're top-level now).
    exclusion = " ".join(f'-deck:"{topics.deck_name(t)}"' for t in topic_names)
    source = sorted(col.find_cards(exclusion))

    buckets: dict[str, list] = {t: [] for t in topic_names}
    n = len(topic_names)
    cursor = 0
    for cid in source:
        # Find the next topic still under the cap (round-robin for balance).
        placed = False
        for _ in range(n):
            topic = topic_names[cursor % n]
            cursor += 1
            if len(buckets[topic]) < per_topic:
                buckets[topic].append(cid)
                placed = True
                break
        if not placed:
            break  # every topic is at the cap

    counts: dict[str, int] = {}
    for topic, cids in buckets.items():
        did = deck_ids[topic]
        if cids and did is not None:
            col.set_deck(cids, did)
        counts[topic] = len(cids)
    return counts
