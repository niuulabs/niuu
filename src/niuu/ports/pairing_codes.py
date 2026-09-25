"""Port for single-use Guild node-pairing code persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from niuu.domain.models import PairingCode


class PairingCodeRepository(ABC):
    """Persistence port for minted node-pairing codes.

    The code itself is a scoped workload JWT (see
    ``niuu.domain.services.guild_join``); this port tracks the hash of that
    JWT so it can be consumed exactly once even though the JWT itself stays
    structurally valid until it expires.
    """

    @abstractmethod
    async def create(
        self,
        *,
        code_hash: str,
        created_by: str,
        tenant_id: str,
        expires_at: datetime,
    ) -> PairingCode:
        """Persist a newly minted pairing code."""

    @abstractmethod
    async def consume(self, code_hash: str) -> PairingCode | None:
        """Atomically mark a code consumed and return it, or ``None``.

        ``None`` covers every reason the code cannot be used: unknown hash,
        already consumed, or past ``expires_at``. Implementations must do
        this in a single statement (e.g. ``UPDATE ... WHERE consumed_at IS
        NULL AND expires_at > NOW() RETURNING ...``) so two concurrent joins
        presenting the same code can never both succeed.
        """

    @abstractmethod
    async def attach_node(self, pairing_code_id: str, node_id: str) -> None:
        """Record which node a consumed code ultimately registered, for audit."""
