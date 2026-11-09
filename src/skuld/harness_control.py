"""Stop or restart this Skuld's own harness, for a member of a workflow run's unit.

A run's members are containers of one pod (Kubernetes), peer workloads of one sandbox
(OpenShell) or processes of one unit, and each is its own Forge session. A pod's containers
and a sandbox's peers cannot be stopped or restarted one at a time from outside, so Forge
asks the member's own Skuld:

* **stop** ends the harness and everything the broker runs for it (mesh peer, event log,
  channels) and leaves the process idle and not ready, so the member takes no more offers;
* **restart** does that, then replaces the process with a fresh one, which re-reads the
  credential file Forge rewrote for the member's new launch.

Both are refused for a Skuld that is not a workflow member, and a restart is refused (409,
``credential_stale``) until the credential file carries the launch Forge asks for: the file
of a Kubernetes Secret volume reaches the container some time after the Secret changes, and
a Skuld restarted onto the old credential would run revoked.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from niuu.domain.services.forge_session_token import LAUNCH_ID_CLAIM
from niuu.forge_mcp.credentials import unverified_claims

logger = logging.getLogger("skuld.harness_control")


class HarnessControlError(Exception):
    """A stop or restart that is refused; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


class HarnessControl:
    """The broker's stop and restart of its own harness (see the module docstring)."""

    def __init__(self, broker: Callable[[], Any]) -> None:
        self._broker = broker
        self._server: Any = None
        self.restart_requested = False

    def attach(self, server: Any) -> None:
        """The uvicorn server to wind down when a restart replaces the process."""
        self._server = server

    def _member(self) -> Any:
        broker = self._broker()
        steps = broker._settings.workflow_steps
        if not steps.enabled:
            raise HarnessControlError(
                409,
                "not_hosted_member",
                "this Skuld is not a member of a workflow run's unit, so Forge does not "
                "stop or restart its harness",
            )
        return broker

    async def stop(self) -> None:
        """End the harness and the broker's work, leaving the process idle (idempotent)."""
        await self._member().shutdown()

    async def restart(self, launch_id: str) -> None:
        """Replace this process with a fresh one under the credential of ``launch_id``."""
        broker = self._member()
        if self._server is None:
            raise HarnessControlError(
                503,
                "not_restartable",
                "this Skuld was not started by skuld.broker.main, so it cannot replace itself",
            )
        self._require_credential_of(broker._settings.forge_mcp.token_file, launch_id)
        await broker.shutdown()
        self.restart_requested = True
        self._server.should_exit = True

    @staticmethod
    def _require_credential_of(token_file: str, launch_id: str) -> None:
        if not token_file:
            raise HarnessControlError(
                409,
                "no_token_file",
                "this Skuld holds its Forge credential in its environment, not in "
                "forge_mcp.token_file, so a new process cannot read a new one",
            )
        try:
            token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise HarnessControlError(
                409, "credential_unreadable", f"forge_mcp.token_file cannot be read: {exc}"
            ) from exc
        held = (unverified_claims(token) or {}).get(LAUNCH_ID_CLAIM)
        if held != launch_id:
            raise HarnessControlError(
                409,
                "credential_stale",
                f"the credential file holds launch {held!r}, not {launch_id!r}: Forge has not "
                "rewritten it yet, or the volume has not delivered the new one",
            )

    def reexec(self) -> None:
        """Replace this process with a fresh one: the same command, a new environment read."""
        logger.info("Restarting the broker process to read its new credential")
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
