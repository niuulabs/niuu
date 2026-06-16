"""Ports for Valkyrie evolution review composition."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.valkyrie_evolution.models import BuildResult, EvolutionRequest, ReviewResult


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
