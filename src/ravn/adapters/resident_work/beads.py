"""Beads resident work adapter."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ravn.domain.resident_portfolio import ResidentObjective
from ravn.resident_portfolio import LocalResidentWorkItemBackend


class BeadsResidentWorkAdapter(LocalResidentWorkItemBackend):
    """Resident work adapter that projects objectives into the ``bd`` CLI."""

    def __init__(
        self,
        root: Path | str,
        *,
        command: str = "bd",
        timeout_seconds: float = 30.0,
        project_dir: Path | str | None = None,
        projection_enabled: bool = True,
    ) -> None:
        super().__init__(Path(root))
        self._command = command
        self._timeout_seconds = float(timeout_seconds)
        self._project_dir = Path(project_dir).expanduser() if project_dir else None
        self._projection_enabled = projection_enabled

    async def write_objective(self, objective: ResidentObjective) -> str:
        ref = await super().write_objective(objective)
        if self._projection_enabled and objective.status in {"candidate", "active", "blocked"}:
            await self._project_objective(objective)
        return ref

    async def append_decision(self, mandate: str, entry: str) -> str:
        ref = await super().append_decision(mandate, entry)
        if self._projection_enabled:
            await _run_bd(
                [self._command, "remember", entry],
                timeout_seconds=self._timeout_seconds,
                cwd=self._project_dir,
                check=False,
            )
        return ref

    async def _project_objective(self, objective: ResidentObjective) -> None:
        title = f"{objective.title} [{objective.id}]"
        body = "\n".join(
            item
            for item in (
                objective.purpose,
                f"Expected outcome: {objective.expected_outcome}",
                f"Proof: {', '.join(objective.proof_criteria)}",
                f"Reason: {objective.reasoning or objective.serves_mandate_because}",
            )
            if item
        )
        await _run_bd(
            [self._command, "create", title, "--stdin", "--json"],
            timeout_seconds=self._timeout_seconds,
            cwd=self._project_dir,
            input_text=body,
            check=False,
        )


async def _run_bd(
    argv: list[str],
    *,
    timeout_seconds: float,
    cwd: Path | None,
    input_text: str = "",
    check: bool = True,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd) if cwd else None,
        stdin=asyncio.subprocess.PIPE if input_text else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input_text.encode("utf-8") if input_text else None),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"command timed out: {argv[0]}") from None
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(argv)}\n{stderr}")
    return stdout
