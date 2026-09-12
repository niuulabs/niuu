"""Setup service: wizard progress, host facts and platform checks."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.domain.setup import (
    KNOWN_SETUP_STEPS,
    SetupState,
    SetupStepRecord,
    SystemCheck,
    SystemReport,
)
from niuu.ports.setup_state import SetupStateStore

logger = logging.getLogger(__name__)

DatabaseProbe = Callable[[], Awaitable[bool]]


class SetupService:
    """Records wizard progress and answers "what does this host look like".

    ``enabled`` is an operator decision (``NIUU_SETUP_ENABLED``): when it is
    off the web app never shows the wizard, so shared-cluster deployments are
    unaffected by a missing state file.
    """

    def __init__(
        self,
        store: SetupStateStore,
        *,
        enabled: bool,
        mode: str,
        host_facts_file: str = "",
        docker_socket_path: str = "/var/run/docker.sock",
        database_probe: DatabaseProbe | None = None,
        git_binary: str = "git",
    ) -> None:
        self._store = store
        self._enabled = bool(enabled)
        self._mode = str(mode)
        self._host_facts_file = Path(host_facts_file).expanduser() if host_facts_file else None
        self._docker_socket_path = Path(docker_socket_path)
        self._database_probe = database_probe
        self._git_binary = str(git_binary)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def mode(self) -> str:
        return self._mode

    async def state(self) -> SetupState:
        return await self._store.load()

    async def complete_step(self, step: str, data: dict[str, Any] | None = None) -> SetupState:
        """Record *step* as done. Unknown steps are rejected."""
        if step not in KNOWN_SETUP_STEPS:
            raise ValueError(
                f"Unknown setup step {step!r}; expected one of {', '.join(KNOWN_SETUP_STEPS)}"
            )
        current = await self._store.load()
        record = SetupStepRecord(step=step, completed_at=datetime.now(UTC), data=dict(data or {}))
        updated = current.with_step(record)
        await self._store.save(updated)
        return updated

    async def complete(self) -> SetupState:
        """Mark the wizard finished so the web app stops showing it."""
        current = await self._store.load()
        updated = current.mark_completed()
        await self._store.save(updated)
        return updated

    async def reset(self) -> SetupState:
        """Forget all progress (the wizard shows again on next load)."""
        updated = SetupState()
        await self._store.save(updated)
        return updated

    def host_facts(self) -> dict[str, Any] | None:
        """Facts written by ``niuu up``; ``None`` when this host was not started that way."""
        if self._host_facts_file is None or not self._host_facts_file.exists():
            return None
        raw = json.loads(self._host_facts_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Host facts file {self._host_facts_file} must contain a JSON object")
        return raw

    async def system(self) -> SystemReport:
        """Host facts plus live checks the platform can make from inside."""
        checks: list[SystemCheck] = []
        host = self.host_facts()
        checks.append(
            SystemCheck(
                name="host facts",
                passed=host is not None,
                warn_only=True,
                message=(
                    "Host facts recorded by `niuu up`."
                    if host is not None
                    else "No host facts; start the platform with `niuu up` to record them."
                ),
            )
        )
        checks.append(await self._database_check())
        checks.append(self._docker_socket_check())
        checks.append(self._git_check())
        return SystemReport(host=host, checks=checks)

    async def _database_check(self) -> SystemCheck:
        if self._database_probe is None:
            return SystemCheck(
                name="database",
                passed=True,
                warn_only=True,
                message="Database probe not configured.",
            )
        try:
            ok = await self._database_probe()
        except Exception as exc:  # the probe's failure is the finding
            logger.warning("Database probe failed: %s", exc)
            return SystemCheck(
                name="database", passed=False, message=f"Database unreachable: {exc}"
            )
        if not ok:
            return SystemCheck(
                name="database", passed=False, message="Database probe returned false."
            )
        return SystemCheck(name="database", passed=True, message="PostgreSQL reachable.")

    def _docker_socket_check(self) -> SystemCheck:
        if self._docker_socket_path.exists():
            return SystemCheck(
                name="docker socket",
                passed=True,
                message=f"Docker socket available at {self._docker_socket_path}.",
            )
        return SystemCheck(
            name="docker socket",
            passed=True,
            warn_only=True,
            message=(
                f"No Docker socket at {self._docker_socket_path}; sessions cannot run as "
                "containers on this host."
            ),
        )

    def _git_check(self) -> SystemCheck:
        resolved = shutil.which(self._git_binary)
        if resolved:
            return SystemCheck(name="git", passed=True, message=f"git found: {resolved}")
        return SystemCheck(
            name="git",
            passed=False,
            message="git is not installed in the platform image; repositories cannot be cloned.",
        )
