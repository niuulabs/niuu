"""Official CLI enrollment in a short-lived process on this host (local mini mode).

The Kubernetes and Docker runners run ``login_worker.py`` in a Job or a sibling
container. Local mini mode runs sessions as host processes, with the provider CLIs
installed on the host, so this adapter runs the same worker as a host process: in a
private (0700) temporary directory of its own, with none of the platform's environment.
The worker writes ``status.json``, ``credential.json`` and reads ``code.json`` there,
exactly as in the container runners; the directory is removed with the process as soon
as the enrollment service has read the credential or the login is cancelled.

Uses the dynamic adapter pattern (plain ``**kwargs`` constructor).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
)
from volundr.domain.ports import CredentialEnrollmentRunnerPort

logger = logging.getLogger(__name__)

#: Which CLI each enrollment method signs in with, by its executable's usual name.
METHOD_CLIS = {"claude_setup": "claude", "codex_device": "codex", "grok_device": "grok"}
WORKER_MODULE = "volundr.adapters.outbound.login_worker"
WORKER_LOG = "worker.log"
DEFAULT_ROOT_PREFIX = "niuu-login-"


@dataclass
class _Login:
    process: asyncio.subprocess.Process
    root: Path


class LocalLoginRunner(CredentialEnrollmentRunnerPort):
    """Run ``login_worker.py`` as a host process and read its status from its directory.

    Args:
        claude_executable / codex_executable / grok_executable: The CLI each login runs.
            A path must exist; a command name (the default is the CLI's usual one) is
            looked up on ``PATH``, and a login whose CLI is not installed is not offered.
        worker_python: The interpreter that runs the worker (this platform's own).
        temporary_dir: Where the private login directories are made; empty uses the
            system's temporary directory.
        worker_interval: Poll interval passed to the worker.
        log_chars: How much of a failed worker's own output is logged (it never
            carries secrets).
        shutdown_timeout: How long a cancelled worker gets to stop its CLI before it is
            killed. The worker's own CLI shutdown takes up to its ``--shutdown-timeout``.
    """

    def __init__(
        self,
        *,
        claude_executable: str = "",
        codex_executable: str = "",
        grok_executable: str = "",
        worker_python: str = "",
        temporary_dir: str = "",
        worker_interval: float = 0.1,
        log_chars: int = 4000,
        shutdown_timeout: float = 5.0,
        **_extra: object,
    ) -> None:
        configured = {
            "claude_setup": str(claude_executable),
            "codex_device": str(codex_executable),
            "grok_device": str(grok_executable),
        }
        self._executables = {
            method: path
            for method, path in (
                (method, _resolve(METHOD_CLIS[method], value))
                for method, value in configured.items()
            )
            if path
        }
        self._worker_python = str(worker_python) or sys.executable
        self._temporary_dir = str(temporary_dir) or None
        self._worker_interval = float(worker_interval)
        self._log_chars = int(log_chars)
        self._shutdown_timeout = float(shutdown_timeout)
        self._logins: dict[UUID, _Login] = {}
        logger.info(
            "Local login runner: signs in with %s",
            ", ".join(f"{m}={p}" for m, p in sorted(self._executables.items())) or "nothing",
        )

    def supports_enrollment(self, method: str) -> bool:
        return method in self._executables

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        executable = self._executables.get(enrollment.method)
        if executable is None:
            raise ValueError(
                f"{enrollment.method} needs the {METHOD_CLIS.get(enrollment.method, 'provider')} "
                "CLI on this host; install it, or set its executable on the login runner"
            )
        root = Path(tempfile.mkdtemp(prefix=DEFAULT_ROOT_PREFIX, dir=self._temporary_dir))
        ttl = max(1, int((enrollment.expires_at - datetime.now(UTC)).total_seconds()))
        log = (root / WORKER_LOG).open("wb")
        try:
            process = await asyncio.create_subprocess_exec(
                self._worker_python,
                "-m",
                WORKER_MODULE,
                "--root",
                str(root / "login"),
                "--method",
                enrollment.method,
                "--executable",
                executable,
                "--search-path",
                _search_path(executable),
                "--ttl",
                str(ttl),
                "--interval",
                str(self._worker_interval),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                # Nothing of the platform's environment: no tokens, no database URL.
                env={"PATH": os.defpath},
                start_new_session=True,
            )
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        finally:
            log.close()
        self._logins[enrollment.id] = _Login(process=process, root=root)
        return replace(enrollment, runner_ref={"root": str(root)})

    async def poll_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollmentPoll:
        login = self._logins.get(enrollment.id)
        if login is None:
            # Started by a platform process that has since stopped: its worker went with it.
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code="login_worker_missing"
            )
        status = _read_json(login.root / "login" / "status.json")
        if login.process.returncode is not None and status.get("state") != "complete":
            # The worker only exits on a crash or a kill; it keeps a failure readable.
            logger.error(
                "Login worker for enrollment %s exited with %s:\n%s",
                enrollment.id,
                login.process.returncode,
                _tail(login.root / WORKER_LOG, self._log_chars),
            )
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code="login_worker_failed"
            )
        state = CredentialEnrollmentState(status.get("state", "pending"))
        credential = (
            _read_json(login.root / "login" / "credential.json")
            if state == CredentialEnrollmentState.COMPLETE
            else {}
        )
        return CredentialEnrollmentPoll(
            state=state,
            credential_data=credential,
            verification_uri=status.get("verification_uri", ""),
            user_code=status.get("user_code", ""),
            error_code=status.get("error_code", ""),
        )

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        login = self._logins.pop(enrollment.id, None)
        if login is not None:
            await _stop(login.process, self._shutdown_timeout)
        root = login.root if login is not None else enrollment.runner_ref.get("root", "")
        if root:
            shutil.rmtree(root, ignore_errors=True)

    async def submit_code(self, enrollment: CredentialEnrollment, code: str) -> None:
        if enrollment.method != "claude_setup":
            raise ValueError("This login does not accept an authorization code")
        login = self._logins.get(enrollment.id)
        if login is None or login.process.returncode is not None:
            raise ValueError("Login worker is not running")
        directory = login.root / "login"
        pending = directory / "code.tmp"
        pending.write_text(json.dumps({"code": code}))
        pending.replace(directory / "code.json")


def _resolve(name: str, configured: str) -> str:
    """The CLI's executable, or ``""`` when it is not installed.

    A configured path (one with a directory in it) must exist. A bare command, the
    configured one or the CLI's usual name, is looked up on PATH.
    """
    command = configured or name
    path = shutil.which(command)
    if path is None and os.sep in command:
        raise ValueError(
            f"login runner executable {configured!r} for {name} does not exist or is not "
            "executable; fix the path, or set a command name to look it up on PATH"
        )
    return path or ""


def _search_path(executable: str) -> str:
    """The CLI's own directory first, so a CLI run by an interpreter (node) finds it there."""
    return os.pathsep.join([str(Path(executable).parent), os.defpath])


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _tail(path: Path, chars: int) -> str:
    try:
        return path.read_text(errors="replace").strip()[-chars:]
    except FileNotFoundError:
        return ""


async def _stop(process: asyncio.subprocess.Process, timeout: float) -> None:
    """SIGTERM, which the worker turns into stopping its CLI; killed if it does not exit."""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        logger.error("Login worker %s did not stop in %ss; killing it", process.pid, timeout)
        process.kill()
        await process.wait()
