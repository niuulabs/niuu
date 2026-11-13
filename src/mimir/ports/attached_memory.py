"""AttachedMemories — open the store behind a session's attached layer.

An attached memory (design section 4, contract C4) is a registered store a
project or launch spec adds to a session. Forge names it by its registry entry;
the gateway opens it from its *own* registry, for the session's org, so a
session cannot point the gateway at a store by URL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from mimir.memory.models import Attachment, MemoryStoreError
from mimir.ports.memory_backend import MemoryBackend


class AttachedMemoryError(MemoryStoreError):
    """The registry cannot open this attachment for this org. The message names why."""


class AttachedMemories(ABC):
    @abstractmethod
    def open(self, tenant_id: str, attachment: Attachment, namespace: str) -> MemoryBackend:
        """The store behind *attachment* for org *tenant_id*, served as *namespace*.

        Raises:
            AttachedMemoryError: the entry is not registered for this org, is
                disabled, or has no connection.
        """

    async def close(self) -> None:  # noqa: B027 — optional hook
        """Release the stores opened so far."""
