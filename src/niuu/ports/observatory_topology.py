"""Port for reading topology fragments from reachable Observatory sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from niuu.domain.models import RegisteredInstance
from niuu.domain.observatory import ObservatoryFragment


class ObservatoryTopologyClientPort(ABC):
    """Fetches one instance's partial view of the topology."""

    @abstractmethod
    async def fetch_fragment(
        self,
        instance: RegisteredInstance,
        *,
        headers: Mapping[str, str],
    ) -> ObservatoryFragment:
        """Return *instance*'s fragment, raising when it cannot be reached."""
