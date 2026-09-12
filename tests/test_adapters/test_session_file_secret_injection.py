"""Tests for SessionFileSecretInjectionAdapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from volundr.adapters.outbound.file_credential_store import FileCredentialStore
from volundr.adapters.outbound.session_file_secret_injection import (
    SessionFileSecretInjectionAdapter,
    shell_export_line,
)
from volundr.domain.models import CredentialMapping, SecretType


async def _store(base: Path, key: str = "") -> FileCredentialStore:
    store = FileCredentialStore(base_dir=str(base), encryption_key=key)
    await store.store(
        "user",
        "dev-user",
        "anthropic",
        SecretType.API_KEY,
        {"api_key": "sk-ant-it's"},
    )
    await store.store(
        "user",
        "dev-user",
        "github",
        SecretType.API_KEY,
        {"token": "ghp_x", "username": "octo"},
    )
    return store


def test_shell_export_line_escapes_single_quotes() -> None:
    assert shell_export_line("K", "a'b") == "export K='a'\\''b'"


@pytest.mark.asyncio
async def test_materializes_env_and_files_with_encryption(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    await _store(tmp_path / "creds", key)
    adapter = SessionFileSecretInjectionAdapter(
        base_dir=str(tmp_path / "creds"),
        encryption_key=key,
        sessions_dir=str(tmp_path / "sessions"),
    )
    await adapter.ensure_secret_provider_class(
        "dev-user",
        [
            CredentialMapping(
                credential_name="anthropic", env_mappings={"ANTHROPIC_API_KEY": "api_key"}
            ),
            CredentialMapping(
                credential_name="github",
                env_mappings={"GITHUB_TOKEN": "token"},
                file_mappings={"/home/skuld/.git-token": "token"},
            ),
        ],
        session_id="sess-1",
    )
    session_dir = tmp_path / "sessions" / "sess-1"
    env_sh = (session_dir / "env.sh").read_text()
    assert "export ANTHROPIC_API_KEY='sk-ant-it'\\''s'" in env_sh
    assert "export GITHUB_TOKEN='ghp_x'" in env_sh
    assert (session_dir / "files" / "0").read_text() == "ghp_x"
    assert (session_dir / "env.sh").stat().st_mode & 0o777 == 0o644
    assert session_dir.stat().st_mode & 0o777 == 0o700

    additions = await adapter.pod_spec_additions("dev-user", "sess-1")
    names = [v["name"] for v in additions.volumes]
    assert names == ["secret-env", "secret-file-0"]
    assert additions.volumes[0]["hostPath"] == {"path": str(session_dir / "env.sh"), "type": "File"}
    assert additions.volume_mounts[0] == {
        "name": "secret-env",
        "mountPath": "/run/secrets/env.sh",
        "readOnly": True,
    }
    assert additions.volume_mounts[1]["mountPath"] == "/home/skuld/.git-token"
    assert additions.env == ()

    await adapter.cleanup_session("sess-1")
    assert not session_dir.exists()
    await adapter.cleanup_session("sess-1")  # idempotent


@pytest.mark.asyncio
async def test_rerender_replaces_previous_material(tmp_path: Path) -> None:
    await _store(tmp_path)
    adapter = SessionFileSecretInjectionAdapter(base_dir=str(tmp_path))
    mapping = [CredentialMapping(credential_name="github", file_mappings={"/t": "token"})]
    await adapter.ensure_secret_provider_class("dev-user", mapping, session_id="s")
    await adapter.ensure_secret_provider_class(
        "dev-user",
        [CredentialMapping(credential_name="anthropic", env_mappings={"K": "api_key"})],
        session_id="s",
    )
    session_dir = tmp_path / "sessions" / "s"
    assert not (session_dir / "mounts.json").exists()
    assert (session_dir / "env.sh").exists()
    additions = await adapter.pod_spec_additions("dev-user", "s")
    assert [v["name"] for v in additions.volumes] == ["secret-env"]


@pytest.mark.asyncio
async def test_missing_credential_or_field_is_fatal(tmp_path: Path) -> None:
    await _store(tmp_path)
    adapter = SessionFileSecretInjectionAdapter(base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="not in the credential store"):
        await adapter.ensure_secret_provider_class(
            "dev-user",
            [CredentialMapping(credential_name="linear", env_mappings={"L": "api_key"})],
            session_id="s",
        )
    with pytest.raises(ValueError, match="missing field 'nope'"):
        await adapter.ensure_secret_provider_class(
            "dev-user",
            [CredentialMapping(credential_name="anthropic", env_mappings={"L": "nope"})],
            session_id="s",
        )
    with pytest.raises(ValueError, match="not in the credential store"):
        await adapter.ensure_secret_provider_class(
            "nobody",
            [CredentialMapping(credential_name="anthropic", env_mappings={"L": "api_key"})],
            session_id="s",
        )


@pytest.mark.asyncio
async def test_no_session_id_and_empty_session_are_noops(tmp_path: Path) -> None:
    adapter = SessionFileSecretInjectionAdapter(base_dir=str(tmp_path), unknown="ignored")
    await adapter.ensure_secret_provider_class("dev-user", [], session_id=None)
    additions = await adapter.pod_spec_additions("dev-user", "never-rendered")
    assert additions.volumes == ()
    assert additions.volume_mounts == ()
    await adapter.provision_user("dev-user")
    await adapter.deprovision_user("dev-user")


@pytest.mark.asyncio
async def test_malformed_store_values_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "user" / "dev-user" / "credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"metadata": {}, "values": []}))
    adapter = SessionFileSecretInjectionAdapter(base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="not in the credential store"):
        await adapter.ensure_secret_provider_class(
            "dev-user",
            [CredentialMapping(credential_name="anthropic", env_mappings={"K": "api_key"})],
            session_id="s",
        )
