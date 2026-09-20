from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from volundr.adapters.outbound.developer_credential_file import (
    FileDeveloperCredentialProjection,
)
from volundr.adapters.outbound.developer_credential_k8s import (
    KubernetesDeveloperCredentialProjection,
)


@pytest.mark.asyncio
async def test_file_projection_replaces_inode_inside_mounted_directory(tmp_path: Path) -> None:
    session_id = uuid4()
    adapter = FileDeveloperCredentialProjection(base_dir=str(tmp_path), runtime_backend="docker")
    first = await adapter.project(
        session_id=session_id, token="first-token", runtime_backend="docker"
    )
    token_file = tmp_path / str(session_id) / "token"
    first_inode = token_file.stat().st_ino
    assert token_file.read_text() == "first-token"
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert token_file.parent.stat().st_mode & 0o777 == 0o700
    assert first.token_file.endswith("/token")
    assert "subPath" not in first.pod_spec.volume_mounts[0]

    await adapter.project(session_id=session_id, token="second-token", runtime_backend="docker")
    assert token_file.read_text() == "second-token"
    assert token_file.stat().st_ino != first_inode

    await adapter.remove(session_id)
    assert not token_file.parent.exists()


@pytest.mark.asyncio
async def test_file_projection_local_uses_host_path_without_mount(tmp_path: Path) -> None:
    adapter = FileDeveloperCredentialProjection(
        base_dir=str(tmp_path),
        runtime_backend="local",
        owner_uid=os.getuid(),
        owner_gid=os.getgid(),
    )
    result = await adapter.project(session_id=uuid4(), token="local-token", runtime_backend="local")
    assert Path(result.token_file).read_text() == "local-token"
    assert result.pod_spec.volumes == ()


class ApiError(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class SecretApi:
    def __init__(self) -> None:
        self.exists = False
        self.created: list[dict] = []
        self.patched: list[dict] = []
        self.deleted: list[str] = []

    async def read_namespaced_secret(self, *, name, namespace):
        if not self.exists:
            raise ApiError(404)
        return {"metadata": {"name": name, "namespace": namespace}}

    async def create_namespaced_secret(self, *, namespace, body):
        self.exists = True
        self.created.append(body)

    async def patch_namespaced_secret(self, *, name, namespace, body):
        self.patched.append(body)

    async def delete_namespaced_secret(self, *, name, namespace):
        if not self.exists:
            raise ApiError(404)
        self.exists = False
        self.deleted.append(name)


@pytest.mark.asyncio
async def test_kubernetes_projection_creates_patches_mounts_directory_and_cleans() -> None:
    api = SecretApi()
    adapter = KubernetesDeveloperCredentialProjection(namespace="sessions", api=api)
    session_id = uuid4()

    initial = await adapter.project(
        session_id=session_id, token="first", runtime_backend="kubernetes"
    )
    await adapter.project(session_id=session_id, token="second", runtime_backend="kubernetes")

    assert api.created[0]["stringData"] == {"token": "first"}
    assert api.patched[0]["stringData"] == {"token": "second"}
    assert initial.pod_spec.volumes[0]["secret"]["defaultMode"] == 0o440
    assert "subPath" not in initial.pod_spec.volume_mounts[0]
    await adapter.remove(session_id)
    await adapter.remove(session_id)
    assert len(api.deleted) == 1
