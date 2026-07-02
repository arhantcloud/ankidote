# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ankidote — GMAT diagnostic (CAT/IRT) engine.

Runtime engine described in AntiPlan/diagnostic-cat-plan.md. Pure Python, no
heavyweight dependencies for the temp-question milestone: the IRT primitives
live in :mod:`anki.ankidote.irt` behind the same surface catsim would expose,
so the plan's catsim swap stays localized to :class:`~anki.ankidote.engine.CatSession`.
"""

from anki.ankidote.item_bank import Item, ItemBank, get_bank
from anki.ankidote.runner import DiagnosticRunner

__all__ = ["Item", "ItemBank", "get_bank", "DiagnosticRunner"]
