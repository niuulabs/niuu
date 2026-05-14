"""Confidence port — interface for run confidence scoring."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ting.domain.models import ConfidenceEvent, Run


class ConfidencePort(ABC):
    """Abstract interface for confidence score management."""

    @abstractmethod
    async def score_initial(self, run: Run) -> float: ...

    @abstractmethod
    async def update_score(self, run_id: str, event: ConfidenceEvent) -> float: ...

    @abstractmethod
    async def get_score(self, run_id: str) -> float: ...
