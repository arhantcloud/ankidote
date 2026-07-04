# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Item Response Theory primitives for the Ankidote diagnostic.

This is the mathematical core the CAT engine is built on: the 3PL item
characteristic curve, Fisher information (for max-information item
selection), a maximum-likelihood ability estimator, and the standard error
of that estimate.

It is a deliberately small, dependency-free implementation for the
temp-question milestone. The diagnostic-cat-plan calls for `catsim` at
runtime; the functions here mirror the pieces of `catsim.irt` we rely on
(`icc`, `inf`, an estimator, and `see`) so that swapping in `catsim` later is
a localized change behind ``CatSession`` — nothing else in the app needs to
know which implementation produced theta/SE.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Logistic scaling constant that puts the logistic model on (approximately)
# the normal-ogive scale, matching catsim's default.
D = 1.702

# Ability search bounds. GMAT abilities are mapped onto a standard-normal
# theta scale, so [-4, 4] covers virtually the whole population.
THETA_MIN = -4.0
THETA_MAX = 4.0

# Standard error reported before any information has been collected, and the
# ceiling we clamp SE to so an empty/near-empty response set does not produce
# an absurd score band.
MAX_SE = 1.5


def p_correct(theta: float, a: float, b: float, c: float) -> float:
    """3PL probability of a correct response at the given ability."""
    return c + (1.0 - c) / (1.0 + math.exp(-D * a * (theta - b)))


def information(theta: float, a: float, b: float, c: float) -> float:
    """Fisher information contributed by an item at ``theta`` (3PL)."""
    p = p_correct(theta, a, b, c)
    if p <= c or p >= 1.0:
        return 0.0
    quotient = (p - c) / (1.0 - c)
    return (D * a) ** 2 * (quotient**2) * (1.0 - p) / p


def _log_likelihood(
    theta: float,
    params: Sequence[tuple[float, float, float]],
    responses: Sequence[float],
) -> float:
    """Log-likelihood for graded responses ``g ∈ [0, 1]``.

    A dichotomous response (``0``/``1`` or ``bool``) reduces to the usual
    ``log P`` / ``log(1−P)``. Partial credit ``g`` (e.g. from answer-choice
    ranking) contributes ``g·log P + (1−g)·log(1−P)`` — the cross-entropy of the
    graded score against the item characteristic curve.
    """
    total = 0.0
    for (a, b, c), g in zip(params, responses):
        p = min(max(p_correct(theta, a, b, c), 1e-9), 1.0 - 1e-9)
        g = min(max(float(g), 0.0), 1.0)
        total += g * math.log(p) + (1.0 - g) * math.log(1.0 - p)
    return total


def estimate_theta(
    params: Sequence[tuple[float, float, float]],
    responses: Sequence[float],
    *,
    prior: float = 0.0,
) -> float:
    """Maximum-likelihood ability estimate via a coarse-then-fine grid search.

    ``responses`` may be dichotomous (``bool``/``0``/``1``) or graded floats in
    ``[0, 1]`` (partial credit). ``prior`` (the survey-seeded warm start) is
    returned unchanged when there is no response yet, and breaks ties for
    all-extreme response patterns, whose MLE is otherwise unbounded.
    """
    if not responses:
        return prior

    numeric = [min(max(float(g), 0.0), 1.0) for g in responses]
    all_extreme = all(g <= 1e-9 for g in numeric) or all(
        g >= 1.0 - 1e-9 for g in numeric
    )
    if all_extreme:
        # Likelihood is monotonic; the MLE runs off to ±infinity. Pin the
        # estimate to a sensible bound in the direction of the evidence,
        # nudged by the prior, rather than the grid extreme.
        direction = 1.0 if numeric[0] >= 0.5 else -1.0
        return max(THETA_MIN, min(THETA_MAX, prior + direction * 1.5))
    responses = numeric

    best_theta = prior
    best_ll = -math.inf
    # Coarse pass over the whole range, then refine around the best point.
    for scale, center, span in ((0.05, 0.0, None), (0.005, None, 0.1)):
        lo = THETA_MIN if span is None else max(THETA_MIN, best_theta - span)
        hi = THETA_MAX if span is None else min(THETA_MAX, best_theta + span)
        steps = int(round((hi - lo) / scale))
        for i in range(steps + 1):
            theta = lo + i * scale
            ll = _log_likelihood(theta, params, responses)
            if ll > best_ll:
                best_ll = ll
                best_theta = theta
    return best_theta


def standard_error(theta: float, params: Sequence[tuple[float, float, float]]) -> float:
    """Standard error of the ability estimate = 1/sqrt(test information)."""
    total_info = sum(information(theta, a, b, c) for a, b, c in params)
    if total_info <= 0.0:
        return MAX_SE
    return min(1.0 / math.sqrt(total_info), MAX_SE)
