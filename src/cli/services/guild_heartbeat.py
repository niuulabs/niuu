"""Periodic signed heartbeat for a host joined to a Guild (`niuu guild heartbeat`).

Run this under a supervisor (systemd, docker's own restart policy, ...) on a
joined host. It is what keeps ``niuu_nodes.last_seen_at`` current and
re-syncs the instances this node offers — without it, a joined node's
registration goes stale (still present, but Guild has no signal the host is
actually still there).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from cli.api.guild import GuildAPIError, heartbeat
from cli.auth.node_key import DEFAULT_NODE_KEY_FILENAME, NodeIdentity, NodeKeyMissingError
from cli.commands.node_instances import UnreachableHostError, offered_instances_for_host
from cli.config import DEFAULT_CONFIG_DIR, CLISettings

logger = logging.getLogger(__name__)


class NotJoinedError(ValueError):
    """Raised when `niuu guild heartbeat` runs on a host that never joined."""


async def run_heartbeat_loop(settings: CLISettings, *, iterations: int | None = None) -> None:
    """Send a signed heartbeat every ``guild.heartbeat_interval_seconds``.

    ``iterations`` bounds the loop (used by tests and ``--once``); the
    default ``None`` runs until cancelled. A single failed heartbeat is
    logged and retried on the next tick — the same "report, don't crash the
    monitor" pattern ``InstanceHealthChecker`` uses for its periodic sweep —
    rather than exiting the whole process over one transient network blip.
    """
    if not settings.guild.url or not settings.guild.node_id:
        raise NotJoinedError("This host has not joined a Guild. Run `niuu join` first.")

    identity = NodeIdentity.load(Path(DEFAULT_CONFIG_DIR) / DEFAULT_NODE_KEY_FILENAME)
    count = 0
    while True:
        try:
            offered = offered_instances_for_host(settings)
            await heartbeat(
                settings.guild.url,
                node_id=settings.guild.node_id,
                identity=identity,
                instances=offered,
            )
            logger.info("Guild heartbeat sent for node %s", settings.guild.node_id)
        except (GuildAPIError, UnreachableHostError, NodeKeyMissingError):
            logger.exception("Guild heartbeat failed; will retry next interval")

        count += 1
        if iterations is not None and count >= iterations:
            return
        await asyncio.sleep(settings.guild.heartbeat_interval_seconds)
