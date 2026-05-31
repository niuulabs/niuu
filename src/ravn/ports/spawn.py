"""SpawnPort — protocol for creating and terminating ephemeral Ravn instances (NIU-435).

Spawned instances register with DiscoveryPort on startup.  The coordinator
waits for them to appear in the peer table before delegating work.

Two adapters are provided:
- ``SubprocessSpawnAdapter`` — ``ravn daemon`` subprocess with tempfile config
- ``KubernetesJobSpawnAdapter`` — Kubernetes Job with the Ravn container image
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable


@dataclass
class SpawnConfig:
    """Configuration for a spawned Ravn instance.

    Args:
        persona:              Persona name for the spawned instance.
        caps:                 Capability filter (tool names) registered on startup.
        permission_mode:      One of ``read_only``, ``workspace_write``, ``full_access``.
        max_concurrent_tasks: Semaphore limit for initiative tasks (default 1).
        env:                  Extra environment variables injected into the process.
        ttl_minutes:          Auto-terminate after N minutes.  None = run forever.
    """

    persona: str
    caps: list[str] = field(default_factory=list)
    permission_mode: Literal["read_only", "workspace_write", "full_access"] = "workspace_write"
    max_concurrent_tasks: int = 1
    env: dict[str, str] = field(default_factory=dict)
    ttl_minutes: int | None = None


@runtime_checkable
class SpawnPort(Protocol):
    """Protocol for spawning and terminating ephemeral Ravn instances.

    Implementations must:
    1. Start the new process/Job so it registers with DiscoveryPort on startup.
    2. Block (with timeout) until the peer appears in the verified peer table.
    3. Return the peer_ids of the newly registered instances.

    Raises ``TimeoutError`` if registration does not complete in time.
    """

    async def spawn(self, count: int, config: SpawnConfig) -> list[str]:
        """Spawn *count* new Ravn instances.

        Returns peer_ids once they appear in the DiscoveryPort peer table.
        Blocks until all instances are registered or raises ``TimeoutError``.
        """
        raise NotImplementedError

    async def terminate(self, peer_id: str) -> None:
        """Gracefully terminate a single spawned instance."""
        raise NotImplementedError

    async def terminate_all(self) -> None:
        """Terminate all instances this spawner created."""
        raise NotImplementedError
