"""Where residents serve their own memory, for this host to mount it."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ServedResidentMemory:
    """One store a resident's gateway serves.

    ``name`` is the mount this host lists it under (``resident-<id>`` unless the
    resident's operator named it); ``url`` is the gateway's base URL.
    """

    valkyrie_id: str
    name: str
    role: str
    url: str


class ResidentMemoryDirectoryPort(ABC):
    @abstractmethod
    async def served(self) -> list[ServedResidentMemory]:
        """Every resident store currently reported as served."""
