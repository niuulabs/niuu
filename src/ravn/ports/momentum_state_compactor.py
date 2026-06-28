"""Momentum current-state compaction port."""

from __future__ import annotations

from typing import Protocol

from ravn.momentum.models import MomentumResidentState


class MomentumStateCompactorPort(Protocol):
    """Compacts current Momentum state for prompt-sized reuse."""

    def compact(self, state: MomentumResidentState) -> MomentumResidentState: ...
