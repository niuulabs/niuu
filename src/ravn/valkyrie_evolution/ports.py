"""Ports for Valkyrie evolution proof composition."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.valkyrie_evolution.models import BuildResult, EvolutionRequest, ReviewResult
from sleipnir.domain.events import SleipnirEvent


class EventLedgerPort(ABC):
    """Append-only proof ledger for Valkyrie operational events."""

    @abstractmethod
    async def record(self, event: SleipnirEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_events(self) -> list[SleipnirEvent]:
        raise NotImplementedError


class EvolutionBuilderPort(ABC):
    """Build reusable capabilities from dream-cycle evolution requests."""

    @abstractmethod
    async def build(self, request: EvolutionRequest) -> BuildResult:
        raise NotImplementedError


class EvolutionReviewPort(ABC):
    """Review generated capabilities before they become runnable."""

    @abstractmethod
    async def review(
        self,
        *,
        request: EvolutionRequest,
        build: BuildResult,
        autonomy_mode: str,
    ) -> ReviewResult:
        raise NotImplementedError
