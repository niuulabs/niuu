"""Momentum delegation proposal target catalog port."""

from __future__ import annotations

from typing import Protocol

from ravn.momentum.models import MomentumDelegationTarget


class MomentumDelegationTargetCatalogPort(Protocol):
    """Provides available delegation targets for proposal preparation."""

    async def list_targets(self) -> list[MomentumDelegationTarget]: ...
