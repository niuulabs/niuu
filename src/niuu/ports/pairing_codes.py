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
    structurally valid until it expires. Actually *consuming* a code happens
    only through ``GuildJoinRepository.consume_and_register`` (atomic with
    node creation and instance registration) — this port covers minting and
    the read-only pre-check ``join()`` needs before attempting that.
    """

    @abstractmethod
    async def create(
        self,
        *,
        code_hash: str,
        created_by: str,
        tenant_id: str,
        allow_plaintext: bool,
        allow_untrusted_node_auth: bool,
        expires_at: datetime,
    ) -> PairingCode:
        """Persist a newly minted pairing code."""

    @abstractmethod
    async def peek(self, code_hash: str) -> PairingCode | None:
        """Read-only lookup, ignoring expiry/consumed state.

        Used to validate a join request (transport policy, tenant) *before*
        attempting the atomic consume — never treated as authorization to
        proceed; the atomic consume re-checks consumed/expired for real.
        """
