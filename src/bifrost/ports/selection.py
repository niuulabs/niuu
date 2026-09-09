"""Provider selection after Bifrost resolves rules and model aliases."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from bifrost.translation.models import AnthropicRequest, AnthropicResponse

ModelCall = Callable[[str, str, AnthropicRequest], Awaitable[AnthropicResponse]]


class SelectionPort(ABC):
    """Choose a provider/model pair; the router executes the final answer."""

    @abstractmethod
    async def select(self, candidates: list[tuple[str, str]]) -> tuple[str, str]:
        """Return one of the supplied candidates or raise."""

    def configured_targets(self) -> list[tuple[str, str]]:
        """Explicit model targets requiring startup validation."""
        return []

    async def route(
        self, request: AnthropicRequest, call_model: ModelCall
    ) -> tuple[str, str] | None:
        """Select an explicit model target, or return None for an ordinary model."""
        return None
