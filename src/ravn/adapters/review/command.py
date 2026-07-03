"""Command-backed resident verification adapter."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ravn.domain.resident_review import (
    ResidentVerificationCheck,
    ResidentVerificationEvidence,
)
from ravn.ports.resident_review import ResidentVerificationPort


class CommandResidentVerificationAdapter(ResidentVerificationPort):
    """Run configured verification checks as argv subprocesses."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 12000,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    async def verify(self, check: ResidentVerificationCheck) -> ResidentVerificationEvidence:
        if not check.command:
            return ResidentVerificationEvidence(
                check_id=check.id,
                description=check.description,
                command=(),
                status="failed",
                exit_code=-1,
                summary="verification check has no command",
            )
        cwd = _working_dir(check.working_dir)
        proc = await asyncio.create_subprocess_exec(
            *check.command,
            cwd=cwd or None,
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
            return ResidentVerificationEvidence(
                check_id=check.id,
                description=check.description,
                command=check.command,
                status="failed",
                exit_code=-1,
                summary=f"verification timed out after {self._timeout_seconds:g}s",
                stderr="timeout",
            )
        exit_code = int(proc.returncode if proc.returncode is not None else -1)
        stdout_text = _decode(stdout[: max(0, self._max_output_bytes)])
        stderr_text = _decode(stderr[: max(0, self._max_output_bytes)])
        passed = exit_code == check.expected_exit_code
        summary = _first_line(stdout_text) or _first_line(stderr_text)
        if not summary:
            summary = "verification passed" if passed else f"verification exited {exit_code}"
        return ResidentVerificationEvidence(
            check_id=check.id,
            description=check.description,
            command=check.command,
            status="passed" if passed else "failed",
            exit_code=exit_code,
            summary=summary,
            stdout=stdout_text,
            stderr=stderr_text,
        )


def _working_dir(value: str) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"working_dir must be an existing directory: {path}")
    return str(path)


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _first_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), "")[:240]
