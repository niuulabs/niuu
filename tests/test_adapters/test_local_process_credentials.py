"""A host-process session gets the credentials its secret injection rendered."""

from __future__ import annotations

from pathlib import Path

import pytest

from volundr.adapters.outbound.file_credential_store import FileCredentialStore
from volundr.adapters.outbound.local_process import injected_credential_env
from volundr.adapters.outbound.session_file_secret_injection import (
    SessionFileSecretInjectionAdapter,
)
from volundr.domain.models import CredentialMapping, PodSpecAdditions, SecretType, SessionSpec


async def _rendered(tmp_path: Path, mapping: CredentialMapping) -> PodSpecAdditions:
    store = FileCredentialStore(base_dir=str(tmp_path / "creds"))
    await store.store(
        "user",
        "dev-user",
        "claude-code-credentials",
        SecretType.GENERIC,
        {"token": "sk-ant-oat01-test-only"},
    )
    injection = SessionFileSecretInjectionAdapter(
        base_dir=str(tmp_path / "creds"), sessions_dir=str(tmp_path / "sessions")
    )
    await injection.ensure_secret_provider_class("dev-user", [mapping], session_id="s-1")
    return await injection.pod_spec_additions("dev-user", "s-1")


async def test_a_connection_token_becomes_the_session_environment(tmp_path: Path) -> None:
    pod_spec = await _rendered(
        tmp_path,
        CredentialMapping(
            credential_name="claude-code-credentials",
            env_mappings={"CLAUDE_CODE_OAUTH_TOKEN": "token"},
        ),
    )

    env = injected_credential_env(SessionSpec(values={}, pod_spec=pod_spec))

    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-test-only"}


async def test_a_credential_file_is_refused_naming_where_it_was_for(tmp_path: Path) -> None:
    pod_spec = await _rendered(
        tmp_path,
        CredentialMapping(
            credential_name="claude-code-credentials",
            file_mappings={"/home/skuld/.grok/auth.json": "token"},
        ),
    )

    with pytest.raises(ValueError, match=r"/home/skuld/\.grok/auth\.json"):
        injected_credential_env(SessionSpec(values={}, pod_spec=pod_spec))


def test_a_session_without_rendered_credentials_gets_none() -> None:
    pod_spec = PodSpecAdditions(
        volumes=({"name": "scratch", "emptyDir": {}},),
        volume_mounts=({"name": "scratch", "mountPath": "/scratch"},),
    )

    assert injected_credential_env(SessionSpec(values={}, pod_spec=pod_spec)) == {}
    assert injected_credential_env(SessionSpec(values={}, pod_spec=PodSpecAdditions())) == {}
