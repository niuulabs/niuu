"""NativeCompactionPort — let the provider compact the context instead of Ravn.

An LLM adapter implements this when its protocol has provider-side compaction
(OpenAI Responses ``context_management`` compaction, Anthropic's
``compact_20260112`` edit). With ``context_management.compaction: native`` for
a model, Ravn's own :class:`~ravn.compression.ContextCompressor` does not run;
the composition root enables compaction on the adapter for that model instead,
and the agent drops the history the provider's latest compaction item already
carries (see :meth:`NativeCompactionPort.compaction_window_start`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class NativeCompactionPort(ABC):
    """An LLM adapter whose provider compacts context on request."""

    @abstractmethod
    def enable_native_compaction(self, *, model: str, threshold_tokens: int) -> None:
        """Ask the provider to compact *model*'s context above *threshold_tokens*.

        Requests for other models are unaffected.

        Raises:
            ConfigurationError: The provider cannot compact at that threshold
                (for example below its minimum trigger).
        """

    @abstractmethod
    def compaction_window_start(self, messages: list[dict]) -> int:
        """Index of the first message the provider still needs.

        *messages* are api dicts (:meth:`ravn.domain.models.Message.to_api_dict`).
        Everything before the message holding the latest compaction item is
        already inside that item, so the caller may drop it. ``0`` when the
        history holds no compaction item of this adapter's protocol.
        """
