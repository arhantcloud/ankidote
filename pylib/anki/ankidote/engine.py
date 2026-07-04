# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""``CatSession`` — a single-topic adaptive test.

Mirrors the four catsim components the diagnostic-cat-plan drives manually
(initializer -> selector -> estimator -> stopper), but over one topic's item
slice so each theta stays a clean unidimensional estimate. The
``DiagnosticRunner`` composes several of these; the same class also backs the
weekly mini-CAT, the estimate-adjustment problems, and sub-topic quizzes by
varying only the stopper settings (plan §4.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from anki.ankidote import irt
from anki.ankidote.item_bank import Item


@dataclass
class Stopper:
    """Minimum-standard-error stopper with item-count bounds."""

    target_se: float = 0.45
    min_items: int = 1
    max_items: int = 5

    def should_stop(self, answered: int, se: float, remaining: int) -> bool:
        if remaining <= 0:
            return True
        if answered >= self.max_items:
            return True
        return answered >= self.min_items and se <= self.target_se


@dataclass
class CatSession:
    """Adaptive session over a fixed pool of items for one topic."""

    topic: str
    section: str
    pool: list[Item]
    theta: float = 0.0
    stopper: Stopper = field(default_factory=Stopper)
    _administered: list[Item] = field(default_factory=list)
    # Graded responses in [0, 1]; dichotomous items store 0.0 / 1.0.
    _responses: list[float] = field(default_factory=list)
    _se: float = irt.MAX_SE

    def __post_init__(self) -> None:
        # Warm start: theta0 seeds the estimate and the standard error.
        self._prior = self.theta

    @property
    def answered(self) -> int:
        return len(self._administered)

    @property
    def correct(self) -> int:
        # Rounded so partial-credit (graded) responses still report a sensible
        # "questions correct" tally; dichotomous responses are unaffected.
        return int(round(sum(self._responses)))

    @property
    def se(self) -> float:
        return self._se

    @property
    def remaining(self) -> int:
        return len(self.pool) - len(self._administered)

    @property
    def stopped(self) -> bool:
        return self.stopper.should_stop(self.answered, self._se, self.remaining)

    def _unadministered(self) -> list[Item]:
        seen = {item.id for item in self._administered}
        return [item for item in self.pool if item.id not in seen]

    def next_item(self) -> Item | None:
        """Most-informative unseen item at the current theta, or None."""
        if self.stopped:
            return None
        candidates = self._unadministered()
        if not candidates:
            return None
        return max(candidates, key=lambda it: irt.information(self.theta, *it.params))

    def record_response(
        self, item: Item, correct: bool | float, *, revealed: bool = False
    ) -> None:
        """Record a response and re-estimate theta and SE.

        ``correct`` is dichotomous (``bool``) for single-choice answers or a
        graded score ``g ∈ [0, 1]`` for answer-choice ranking (partial credit).

        Give-up rule (PRD §4): if the answer was reached only after the card was
        revealed or explicitly marked "don't know" (``revealed=True``), it is
        recorded as **not known** (0.0) no matter which choice was submitted, so
        a revealed card can never lift the ability estimate.
        """
        graded = 0.0 if revealed else float(correct)
        self._administered.append(item)
        self._responses.append(graded)
        params = [it.params for it in self._administered]
        self.theta = irt.estimate_theta(params, self._responses, prior=self._prior)
        self._se = irt.standard_error(self.theta, params)
