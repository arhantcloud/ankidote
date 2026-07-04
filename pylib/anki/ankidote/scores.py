# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""theta/SE -> GMAT score-range mapping and per-topic aggregation.

Kept isolated (per diagnostic-cat-plan §7) so the linear theta->score map used
for v1 can be replaced by a calibrated lookup table without touching the
engine or the wire protocol.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

_DAY_MS = 24 * 3600 * 1000

# Linear theta -> GMAT total-score mapping: theta in [-3, 3] maps onto the
# 205-805 scale, i.e. score = SCORE_MID + theta * SCORE_PER_THETA. Matches the
# diagnostic-cat-plan's verified sanity run.
SCORE_MID = 505.0
SCORE_PER_THETA = 100.0
SCORE_LOW = 205
SCORE_HIGH = 805
SCORE_STEP = 10

# 95% band half-width in SE units.
Z = 1.96


@dataclass(frozen=True)
class ScoreRange:
    low: int
    high: int


def theta_to_score(theta: float) -> int:
    """Map an ability estimate to a GMAT total score (205-805, ending in 5)."""
    raw = SCORE_MID + theta * SCORE_PER_THETA
    clamped = min(max(raw, SCORE_LOW), SCORE_HIGH)
    steps = round((clamped - SCORE_LOW) / SCORE_STEP)
    max_steps = (SCORE_HIGH - SCORE_LOW) // SCORE_STEP
    return SCORE_LOW + min(steps, max_steps) * SCORE_STEP


def score_range(theta: float, se: float) -> ScoreRange:
    """95% score band from a theta estimate and its standard error."""
    return ScoreRange(
        low=theta_to_score(theta - Z * se),
        high=theta_to_score(theta + Z * se),
    )


def stalest_topics(
    diagnostic: dict | None,
    k: int = 3,
    *,
    now_ms: float | None = None,
) -> list[str]:
    """Topics whose estimate is least trustworthy right now.

    Staleness = ``standardError × (daysSinceMeasured + 1)`` — a wide *or* old
    estimate rises to the top, so a check-in spends its handful of items where
    uncertainty is highest (PRD §6.5). Falls back to the widest estimates when
    no ``measuredAt`` stamps exist yet.
    """
    if not diagnostic:
        return []
    now = now_ms if now_ms is not None else time.time() * 1000
    scored: list[tuple[float, str]] = []
    for entry in diagnostic.get("topicScores", []) or []:
        topic = entry.get("topic")
        if not topic:
            continue
        se = float(entry.get("standardError", 1.0))
        measured_at = entry.get("measuredAt") or diagnostic.get("takenAt") or now
        days = max(0.0, (now - measured_at) / _DAY_MS)
        scored.append((se * (days + 1.0), topic))
    scored.sort(reverse=True)
    return [topic for _, topic in scored[:k]]


def combine_topics(
    estimates: list[tuple[float, float, float]],
) -> tuple[float, float]:
    """Combine per-topic ``(theta, se, weight)`` into an overall (theta, se).

    Overall theta is the weight-weighted mean of topic thetas; overall SE is
    the standard error of that weighted mean, treating topics as independent
    (a reasonable v1 approximation — the plan notes true cross-topic ability
    correlation is future work).
    """
    weighted = [(t, se, w) for (t, se, w) in estimates if w > 0]
    if not weighted:
        return 0.0, 1.5
    total_w = sum(w for _, _, w in weighted)
    theta = sum(t * w for t, _, w in weighted) / total_w
    var = sum((w / total_w) ** 2 * se**2 for _, se, w in weighted)
    return theta, math.sqrt(var)
