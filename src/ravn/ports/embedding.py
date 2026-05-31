"""Embedding port — interface for text embedding backends."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingPort(ABC):
    """Abstract interface for generating text embeddings.

    Implementations provide vector representations of text that can be used
    for semantic similarity search in episodic memory retrieval.
    """

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Generate a single embedding vector for *text*."""
        raise NotImplementedError

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of texts.

        Implementations should prefer batched inference where possible
        for efficiency.  Returns a list in the same order as *texts*.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of embedding vectors produced by this adapter."""
        raise NotImplementedError
