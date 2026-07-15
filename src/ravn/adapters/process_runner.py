"""Shared async subprocess runner for resident adapters."""

from __future__ import annotations

import asyncio
from pathlib import Path


class CommandResult:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def run_command(
    argv: list[str],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
    input_text: str = "",
    check: bool = True,
) -> CommandResult:
    """Run ``argv`` to completion, killing it on timeout and decoding output as UTF-8."""
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
    result = CommandResult(
        proc.returncode,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr}"
        )
    return result
