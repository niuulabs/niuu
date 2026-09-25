"""Resident-side adapters for :class:`ravn.ports.resident_budget.ResidentBudgetPort`."""

from __future__ import annotations

from ravn.adapters.resident_budget.local import LocalBudgetReporter
from ravn.adapters.resident_budget.platform import PlatformBudgetReporter

__all__ = ["LocalBudgetReporter", "PlatformBudgetReporter"]
