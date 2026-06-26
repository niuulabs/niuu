"""Beads resident work adapter."""

from __future__ import annotations

from pathlib import Path

from ravn.adapters.process_runner import run_command
from ravn.adapters.resident_work.local import LocalResidentWorkItemBackend
from ravn.domain.resident_portfolio import ResidentObjective


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
            await run_command(
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
        await run_command(
            [self._command, "create", title, "--stdin", "--json"],
            timeout_seconds=self._timeout_seconds,
            cwd=self._project_dir,
            input_text=body,
            check=False,
        )
