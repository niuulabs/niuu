"""Real Docker directory-mount probe for atomic coordinator token rotation."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from niuu.config_models import WorkloadIdentityConfig
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.domain.services.workload_identity import WorkloadIdentityService
from volundr.adapters.outbound.execution_credential_file import (
    FileExecutionCredentialProjection,
)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


def _token(issuer: WorkloadIdentityService, session_id: str) -> str:
    return issuer.issue_token(
        principal=Principal(
            user_id="docker-proof-owner",
            email="",
            tenant_id="docker-proof-tenant",
            roles=["volundr:developer"],
        ),
        workload_subject="workflow:execution-docker-proof",
        workload_name="developer-coordinator",
        audiences=[],
        token_use=VALKYRIE_BUILD_TOKEN_USE,
        claims={
            "scopes": ["ting:workflow:coordinate"],
            "forge_session_id": session_id,
        },
    ).token


@pytest.mark.asyncio
@pytest.mark.skipif(not _docker_available(), reason="local Docker daemon is unavailable")
async def test_read_only_directory_mount_observes_atomic_token_replacement(
    tmp_path: Path,
) -> None:
    del tmp_path
    session_id = uuid4()
    issuer = WorkloadIdentityService(
        WorkloadIdentityConfig(enabled=True, issuer="niuu-docker-proof")
    )
    shared_base = Path(tempfile.mkdtemp(prefix=".credential-probe-", dir=Path.cwd()))
    projection = FileExecutionCredentialProjection(
        base_dir=str(shared_base),
        runtime_backend="docker",
        owner_uid=os.getuid(),
        owner_gid=os.getgid(),
    )
    first = _token(issuer, str(session_id))
    second = _token(issuer, str(session_id))
    await projection.project(session_id=session_id, token=first, runtime_backend="docker")
    mounted_directory = shared_base / str(session_id)

    process = subprocess.Popen(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--volume",
            f"{mounted_directory}:/credential:ro",
            "python:3.14-slim-trixie",
            "sh",
            "-c",
            (
                "first=$(cat /credential/token); printf '%s\\n' \"$first\"; "
                "current=$first; "
                'while [ -z "$current" ] || [ "$current" = "$first" ]; do '
                "sleep 0.05; current=$(cat /credential/token 2>/dev/null || true); done; "
                "printf '%s\\n' \"$current\""
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        first_read = bytearray()
        while not first_read.endswith(b"\n"):
            first_read.extend(os.read(process.stdout.fileno(), 1))
        assert first_read.decode().strip() == first
        await projection.project(session_id=session_id, token=second, runtime_backend="docker")
        rotated = ""
        while not rotated:
            second_read = bytearray()
            while not second_read.endswith(b"\n"):
                chunk = os.read(process.stdout.fileno(), 1)
                if not chunk:
                    break
                second_read.extend(chunk)
            rotated = second_read.decode().strip()
            if not second_read:
                break
        process.wait(timeout=15)
        stderr = process.stderr.read().decode()
        assert process.returncode == 0, stderr
        assert rotated == second
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        process.stdout.close()
        process.stderr.close()
        shutil.rmtree(shared_base, ignore_errors=True)
