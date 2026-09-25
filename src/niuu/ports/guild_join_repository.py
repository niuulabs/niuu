"""Port for the atomic "consume pairing code + create node + register
instances" operation `niuu join` needs.

Three separate repositories (pairing codes, nodes, instances) each acquire
their own connection per call, which cannot give the cross-table atomicity
this operation requires: if instance registration fails after the code was
consumed and the node created, the code must not be burned for nothing. A
dedicated port lets one adapter run all three writes in a single database
transaction — see ``PostgresGuildJoinRepository``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    PairingCode,
    RegisteredInstance,
    RegisteredNode,
)


class NodeConflictError(Exception):
    """Raised when a node name or public key is already taken.

    The whole transaction is rolled back when this is raised — including the
    pairing-code consumption — so the presented code remains valid for a
    legitimate retry with a different name/key.
    """


@dataclass(frozen=True)
class InstanceWrite:
    """One instance row to create, already validated and ready to persist."""

    kind: InstanceKind
    slug: str
    name: str
    base_url: str
    visibility: InstanceVisibility
    tenant_id: str | None
    config: dict


class GuildJoinRepository(ABC):
    """Persistence port for the atomic node-join transaction."""

    @abstractmethod
    async def consume_and_register(
        self,
        *,
        code_hash: str,
        node_name: str,
        public_key: str,
        instances: list[InstanceWrite],
    ) -> tuple[PairingCode, RegisteredNode, list[RegisteredInstance]] | None:
        """Atomically consume the code, create the node, and write its instances.

        Returns ``None`` (nothing written) when the code cannot be consumed
        (unknown, expired, or already used). Raises :class:`NodeConflictError`
        (everything rolled back, code left unconsumed) when ``node_name`` or
        ``public_key`` collides with an existing node.
        """
