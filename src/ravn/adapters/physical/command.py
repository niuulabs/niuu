"""Command-backed physical capability adapter.

The adapter executes only configured argv arrays. It does not invoke a shell,
interpret templates, or decide whether an operation is safe; resident runtime
policy must gate dangerous actions before calling ``execute``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ravn.domain.physical_device import (
    PhysicalActionKind,
    PhysicalActionRequest,
    PhysicalActionResult,
    PhysicalCapability,
)
from ravn.ports.physical_device import PhysicalDevicePort


@dataclass(frozen=True)
class _ConfiguredPhysicalCapability:
    capability: PhysicalCapability
    telemetry_command: tuple[str, ...] = ()
    dry_run_command: tuple[str, ...] = ()
    execute_command: tuple[str, ...] = ()
    working_dir: str = ""


class CommandPhysicalDeviceAdapter(PhysicalDevicePort):
    """Run configured read-only/dry-run/device commands behind the physical port."""

    def __init__(
        self,
        *,
        capabilities: list[dict[str, Any]],
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 12000,
    ) -> None:
        self._capabilities = {
            item.capability.id: item for item in (_capability_from_config(c) for c in capabilities)
        }
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    async def list_capabilities(self) -> list[PhysicalCapability]:
        return [item.capability for item in self._capabilities.values()]

    async def read_telemetry(self, capability_id: str) -> PhysicalActionResult:
        configured = self._get(capability_id)
        return await self._run(
            configured,
            action="read telemetry",
            kind=PhysicalActionKind.READ_ONLY.value,
            command=configured.telemetry_command,
        )

    async def dry_run(self, request: PhysicalActionRequest) -> PhysicalActionResult:
        configured = self._get(request.capability_id)
        return await self._run(
            configured,
            action=request.action,
            kind=PhysicalActionKind.DRY_RUN.value,
            command=configured.dry_run_command,
            request=request,
        )

    async def execute(self, request: PhysicalActionRequest) -> PhysicalActionResult:
        configured = self._get(request.capability_id)
        return await self._run(
            configured,
            action=request.action,
            kind=PhysicalActionKind.PHYSICAL_OPERATION.value,
            command=configured.execute_command,
            request=request,
        )

    def _get(self, capability_id: str) -> _ConfiguredPhysicalCapability:
        try:
            return self._capabilities[capability_id]
        except KeyError as exc:
            raise ValueError(f"unknown physical capability: {capability_id}") from exc

    async def _run(
        self,
        configured: _ConfiguredPhysicalCapability,
        *,
        action: str,
        kind: str,
        command: tuple[str, ...],
        request: PhysicalActionRequest | None = None,
    ) -> PhysicalActionResult:
        if not command:
            return PhysicalActionResult(
                capability_id=configured.capability.id,
                action=action,
                kind=kind,
                status="unavailable",
                summary=f"capability {configured.capability.id} has no configured {kind} command",
                risk_boundaries=_risk_boundaries(configured, request),
                blocked_reason=f"no configured {kind} command",
            )
        cwd = configured.working_dir or None
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return PhysicalActionResult(
                capability_id=configured.capability.id,
                action=action,
                kind=kind,
                status="timeout",
                summary=f"{kind} command timed out after {self._timeout_seconds:g}s",
                risk_boundaries=_risk_boundaries(configured, request),
                blocked_reason="command timed out",
            )
        telemetry = _telemetry_from_process(
            command=command,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            max_output_bytes=self._max_output_bytes,
        )
        return PhysicalActionResult(
            capability_id=configured.capability.id,
            action=action,
            kind=kind,
            status="completed" if proc.returncode == 0 else "failed",
            summary=_summary(kind, telemetry),
            telemetry=telemetry,
            risk_boundaries=_risk_boundaries(configured, request),
            blocked_reason="" if proc.returncode == 0 else telemetry.get("stderr", ""),
        )


def _capability_from_config(raw: dict[str, Any]) -> _ConfiguredPhysicalCapability:
    capability_id = _text(raw.get("id"))
    if not capability_id:
        raise ValueError("physical capability config requires id")
    telemetry_command = _argv(raw.get("telemetry_command"))
    dry_run_command = _argv(raw.get("dry_run_command"))
    execute_command = _argv(raw.get("execute_command"))
    action_kinds = []
    if telemetry_command:
        action_kinds.append(PhysicalActionKind.READ_ONLY.value)
    if dry_run_command:
        action_kinds.append(PhysicalActionKind.DRY_RUN.value)
    if execute_command:
        action_kinds.append(PhysicalActionKind.PHYSICAL_OPERATION.value)
    metadata = raw.get("metadata")
    return _ConfiguredPhysicalCapability(
        capability=PhysicalCapability(
            id=capability_id,
            name=_text(raw.get("name")) or capability_id,
            description=_text(raw.get("description")),
            action_kinds=tuple(action_kinds),
            telemetry_supported=bool(telemetry_command),
            dry_run_supported=bool(dry_run_command),
            risk_boundaries=tuple(_texts(raw.get("risk_boundaries"))),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        ),
        telemetry_command=telemetry_command,
        dry_run_command=dry_run_command,
        execute_command=execute_command,
        working_dir=_working_dir(raw.get("working_dir")),
    )


def _argv(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or not value:
        raise ValueError("physical commands must be non-empty argv lists")
    argv = tuple(_text(item) for item in value)
    if any(not item for item in argv):
        raise ValueError("physical command argv entries must be non-empty strings")
    return argv


def _working_dir(value: Any) -> str:
    if value in (None, ""):
        return ""
    path = Path(_text(value)).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"working_dir must be an existing directory: {path}")
    return str(path)


def _telemetry_from_process(
    *,
    command: tuple[str, ...],
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    max_output_bytes: int,
) -> dict[str, Any]:
    stdout_text = _decode(stdout[:max(0, max_output_bytes)])
    stderr_text = _decode(stderr[:max(0, max_output_bytes)])
    telemetry: dict[str, Any] = {
        "command": list(command),
        "returncode": int(returncode if returncode is not None else -1),
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_truncated": len(stdout) > max_output_bytes,
        "stderr_truncated": len(stderr) > max_output_bytes,
    }
    parsed = _json(stdout_text)
    if isinstance(parsed, dict):
        telemetry["json"] = parsed
    return telemetry


def _summary(kind: str, telemetry: dict[str, Any]) -> str:
    parsed = telemetry.get("json")
    if isinstance(parsed, dict):
        for key in ("summary", "status", "message"):
            value = parsed.get(key)
            if str(value or "").strip():
                return str(value)
    stdout = str(telemetry.get("stdout") or "").strip()
    if stdout:
        return stdout.splitlines()[0][:240]
    stderr = str(telemetry.get("stderr") or "").strip()
    if stderr:
        return stderr.splitlines()[0][:240]
    return f"{kind} command completed"


def _risk_boundaries(
    configured: _ConfiguredPhysicalCapability,
    request: PhysicalActionRequest | None,
) -> tuple[str, ...]:
    seen = dict.fromkeys(configured.capability.risk_boundaries)
    if request is not None:
        seen.update(dict.fromkeys(request.risk_boundaries))
    return tuple(seen)


def _texts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None

