"""LLM port — interface for spec decomposition."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ting.domain.models import SagaStructure


class LLMPort(ABC):
    """Abstract interface for LLM-based spec decomposition."""

    @abstractmethod
    async def decompose_spec(self, spec: str, repo: str, *, model: str) -> SagaStructure:
        raise NotImplementedError
