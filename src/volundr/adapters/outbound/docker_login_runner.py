"""Official CLI enrollment in a short-lived sibling container (single-host Docker).

The Kubernetes runner launches ``login_worker.py`` in a bounded Job; this
adapter runs the same worker in a container started over the Docker socket,
for ``niuu up`` docker mode. The container gets a memory-backed ``/tmp`` as its
only writable path, no platform environment, and is removed as soon as the
enrollment service has read the credential or the login is cancelled.

Uses the dynamic adapter pattern (plain ``**kwargs`` constructor).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import docker
from docker.errors import NotFound

from volundr.adapters.outbound.docker_container import LABEL_MANAGED_BY, MANAGED_BY
from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
)
from volundr.domain.ports import CredentialEnrollmentRunnerPort

logger = logging.getLogger(__name__)

LOGIN_ROOT = "/tmp/login"
LOGIN_LABEL = "niuu.io/credential-enrollment"
DEFAULT_CONTAINER_PREFIX = "niuu-login-"
DEFAULT_WORKER_PYTHON = "/opt/venv/bin/python"
DEFAULT_CODE_ENV = "NIUU_LOGIN_CODE"
DEFAULT_LOG_TAIL = 40
DEFAULT_LOG_CHARS = 4000

_READ_STATUS = (
    "from pathlib import Path; import json; "
    f"p=Path('{LOGIN_ROOT}/status.json'); "
    "s=json.loads(p.read_text()) if p.exists() else {'state':'pending'}; "
    f"s.update(credential_data=json.loads(Path('{LOGIN_ROOT}/credential.json').read_text())) "
    "if s['state']=='complete' else None; print(json.dumps(s))"
)
# The authorization code travels in the exec's environment, never in its
# arguments, so it does not show up in process listings inside the container.
_WRITE_CODE = (
    "import os,json; from pathlib import Path; "
    f"value=json.loads(os.environ['{DEFAULT_CODE_ENV}']); "
    f"p=Path('{LOGIN_ROOT}/code.tmp'); p.write_text(json.dumps(value)); "
    f"p.replace('{LOGIN_ROOT}/code.json'); print('accepted',flush=True)"
)


class DockerLoginRunner(CredentialEnrollmentRunnerPort):
    """Run ``login_worker.py`` in a sibling container and read its status over exec.

    Args:
        image: Image with the official CLIs and ``/opt/venv`` (the skuld image).
        docker_base_url: Docker daemon URL; empty uses the environment.
        container_prefix: Name prefix for login containers.
        network: Docker network for the container; empty uses the default bridge
            (the CLIs need outbound HTTPS to the provider).
        user: ``uid:gid`` the worker runs as inside the container.
        codex_executable / claude_executable: CLI paths inside the image.
        worker_interval: Poll interval passed to the worker.
        memory_limit: Container memory limit (Docker size string).
        temporary_storage_limit: Size of the memory-backed ``/tmp``.
    """

    def __init__(
        self,
        *,
        image: str,
        docker_base_url: str = "",
        container_prefix: str = DEFAULT_CONTAINER_PREFIX,
        network: str = "",
        user: str = "1000:1000",
        worker_python: str = DEFAULT_WORKER_PYTHON,
        codex_executable: str = "/usr/local/bin/codex",
        claude_executable: str = "/usr/local/bin/claude",
        grok_executable: str = "/usr/local/bin/grok",
        worker_interval: float = 0.1,
        memory_limit: str = "512m",
        temporary_storage_limit: str = "32m",
        log_tail: int = DEFAULT_LOG_TAIL,
        log_chars: int = DEFAULT_LOG_CHARS,
        **_extra: object,
    ) -> None:
        self._image = str(image)
        self._container_prefix = str(container_prefix)
        self._network = str(network).strip()
        self._user = str(user)
        self._worker_python = str(worker_python)
        self._codex_executable = str(codex_executable)
        self._claude_executable = str(claude_executable)
        self._grok_executable = str(grok_executable)
        self._worker_interval = float(worker_interval)
        self._memory_limit = str(memory_limit)
        self._storage_limit = str(temporary_storage_limit)
        self._log_tail = int(log_tail)
        self._log_chars = int(log_chars)
        self._worker_source = (
            files("volundr.adapters.outbound").joinpath("login_worker.py").read_text()
        )
        self._client = (
            docker.DockerClient(base_url=str(docker_base_url))
            if str(docker_base_url).strip()
            else docker.from_env()
        )

    def supports_enrollment(self, method: str) -> bool:
        return method in {"codex_device", "claude_setup", "grok_device"}

    def _executable(self, method: str) -> str:
        if method == "codex_device":
            return self._codex_executable
        if method == "grok_device":
            return self._grok_executable
        return self._claude_executable

    def container_name(self, enrollment: CredentialEnrollment) -> str:
        return f"{self._container_prefix}{enrollment.id.hex}"

    # ------------------------------------------------------------------
    # Docker calls (synchronous SDK, always off the event loop)
    # ------------------------------------------------------------------

    def _run_kwargs(self, enrollment: CredentialEnrollment) -> dict[str, Any]:
        ttl = max(1, int((enrollment.expires_at - datetime.now(UTC)).total_seconds()))
        executable = self._executable(enrollment.method)
        kwargs: dict[str, Any] = {
            "image": self._image,
            "name": self.container_name(enrollment),
            "command": [
                self._worker_python,
                "-c",
                self._worker_source,
                "--root",
                LOGIN_ROOT,
                "--method",
                enrollment.method,
                "--executable",
                executable,
                "--ttl",
                str(ttl),
                "--interval",
                str(self._worker_interval),
            ],
            "detach": True,
            "init": True,
            "user": self._user,
            "labels": {LOGIN_LABEL: str(enrollment.id), LABEL_MANAGED_BY: MANAGED_BY},
            # The worker only ever writes under /tmp; everything else is sealed.
            "read_only": True,
            "tmpfs": {"/tmp": f"size={self._storage_limit},mode=1777"},
            "mem_limit": self._memory_limit,
            "environment": {},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
        if self._network:
            kwargs["network"] = self._network
        return kwargs

    def _get(self, enrollment: CredentialEnrollment) -> Any | None:
        try:
            return self._client.containers.get(self.container_name(enrollment))
        except NotFound:
            return None

    def _exec_json(self, container: Any, script: str, environment: dict[str, str]) -> dict:
        code, output = container.exec_run(
            [self._worker_python, "-c", script],
            stdout=True,
            stderr=False,
            environment=environment or None,
            user=self._user,
        )
        if code != 0:
            raise RuntimeError("login_worker_read_failed")
        text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
        return json.loads(text)

    def _exec_text(self, container: Any, script: str, environment: dict[str, str]) -> str:
        code, output = container.exec_run(
            [self._worker_python, "-c", script],
            stdout=True,
            stderr=False,
            environment=environment or None,
            user=self._user,
        )
        if code != 0:
            raise ValueError("Login worker could not accept the authorization code")
        return output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output

    # ------------------------------------------------------------------
    # CredentialEnrollmentRunnerPort
    # ------------------------------------------------------------------

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        if not self.supports_enrollment(enrollment.method):
            raise ValueError("Unsupported login method")
        run_kwargs = self._run_kwargs(enrollment)
        await asyncio.to_thread(self._client.containers.run, **run_kwargs)
        return replace(enrollment, runner_ref={"container_name": run_kwargs["name"]})

    async def poll_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollmentPoll:
        container = await asyncio.to_thread(self._get, enrollment)
        if container is None:
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code="login_worker_missing"
            )
        await asyncio.to_thread(container.reload)
        status = str(container.status)
        if status == "created":
            return CredentialEnrollmentPoll(state=CredentialEnrollmentState.PENDING)
        if status != "running":
            # The worker only exits on a timeout, a crash or a kill. Its stdout
            # never carries secrets, so the tail is safe to keep in the log.
            state = container.attrs.get("State") or {}
            logs = await asyncio.to_thread(container.logs, tail=self._log_tail)
            text = logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
            logger.error(
                "Login helper %s exited (status=%s exit_code=%s oom_killed=%s) "
                "for enrollment %s:\n%s",
                container.name,
                status,
                state.get("ExitCode"),
                state.get("OOMKilled"),
                enrollment.id,
                text.strip()[-self._log_chars :],
            )
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code="login_worker_failed"
            )
        result = await asyncio.to_thread(self._exec_json, container, _READ_STATUS, {})
        return CredentialEnrollmentPoll(
            state=CredentialEnrollmentState(result["state"]),
            credential_data=result.get("credential_data", {}),
            verification_uri=result.get("verification_uri", ""),
            user_code=result.get("user_code", ""),
            error_code=result.get("error_code", ""),
        )

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        container = await asyncio.to_thread(self._get, enrollment)
        if container is None:
            return
        await asyncio.to_thread(container.remove, force=True)

    async def submit_code(self, enrollment: CredentialEnrollment, code: str) -> None:
        if enrollment.method != "claude_setup":
            raise ValueError("This login does not accept an authorization code")
        container = await asyncio.to_thread(self._get, enrollment)
        if container is None:
            raise ValueError("Login worker is not running")
        await asyncio.to_thread(container.reload)
        if str(container.status) != "running":
            raise ValueError("Login worker is not running")
        output = await asyncio.to_thread(
            self._exec_text,
            container,
            _WRITE_CODE,
            {DEFAULT_CODE_ENV: json.dumps({"code": code})},
        )
        if output.strip() != "accepted":
            raise ValueError("Login worker could not accept the authorization code")
