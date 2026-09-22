"""Atomic directory-backed projection for local and Docker session runtimes."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from volundr.domain.models import PodSpecAdditions
from volundr.ports.workflow_execution_credentials import (
    ExecutionCredentialProjection,
    ExecutionCredentialProjectionPort,
)

DEFAULT_CONTAINER_DIRECTORY = "/var/run/secrets/niuu-workflow-execution"


class FileExecutionCredentialProjection(ExecutionCredentialProjectionPort):
    """Rotate a token with ``os.replace`` inside a directory-mounted path."""

    def __init__(
        self,
        *,
        base_dir: str,
        runtime_backend: str,
        container_directory: str = DEFAULT_CONTAINER_DIRECTORY,
        owner_uid: int | None = None,
        owner_gid: int | None = None,
        **_extra: object,
    ) -> None:
        if runtime_backend not in {"local", "process", "docker"}:
            raise ValueError(
                "FileExecutionCredentialProjection supports only local, process, and docker"
            )
        if not base_dir.strip():
            raise ValueError("developer credential file projection requires base_dir")
        self._base_dir = Path(base_dir).expanduser().resolve()
        self._runtime_backend = runtime_backend
        self._container_directory = container_directory.rstrip("/")
        self._owner_uid = os.getuid() if owner_uid is None else owner_uid
        self._owner_gid = os.getgid() if owner_gid is None else owner_gid

    def supports(self, runtime_backend: str) -> bool:
        return runtime_backend == self._runtime_backend

    def _directory(self, session_id: UUID) -> Path:
        return self._base_dir / str(session_id)

    async def project(
        self,
        *,
        session_id: UUID,
        token: str,
        runtime_backend: str,
    ) -> ExecutionCredentialProjection:
        if not self.supports(runtime_backend):
            raise ValueError(f"file credential projection cannot serve {runtime_backend!r}")
        if not token.strip():
            raise ValueError("developer credential token must not be empty")
        directory = self._directory(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, self._owner_uid, self._owner_gid)
        directory.chmod(0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".token-", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(token)
                handle.flush()
                os.fsync(handle.fileno())
            os.chown(temporary, self._owner_uid, self._owner_gid)
            temporary.chmod(0o600)
            os.replace(temporary, directory / "token")
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

        if runtime_backend != "docker":
            return ExecutionCredentialProjection(token_file=str(directory / "token"))
        volume_name = f"execution-credential-{session_id.hex[:12]}"
        return ExecutionCredentialProjection(
            token_file=f"{self._container_directory}/token",
            pod_spec=PodSpecAdditions(
                volumes=(
                    {
                        "name": volume_name,
                        "hostPath": {"path": str(directory), "type": "Directory"},
                    },
                ),
                volume_mounts=(
                    {
                        "name": volume_name,
                        "mountPath": self._container_directory,
                        "readOnly": True,
                    },
                ),
            ),
        )

    async def remove(self, session_id: UUID) -> None:
        directory = self._directory(session_id)
        if directory.exists():
            shutil.rmtree(directory)
