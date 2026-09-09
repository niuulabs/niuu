"""Provider selection after Bifrost resolves rules and model aliases."""

from abc import ABC, abstractmethod


class SelectionPort(ABC):
    """Choose one configured provider/model pair without executing the request."""

    @abstractmethod
    async def select(self, candidates: list[tuple[str, str]]) -> tuple[str, str]:
        """Return one of the supplied candidates or raise."""
