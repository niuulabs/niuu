from __future__ import annotations

import json
import stat
from datetime import UTC, datetime

import jwt
import pytest

from skuld.openshell_home import prepare_codex_home, prepare_from_environment


def test_prepare_codex_home_writes_only_runtime_reference_and_account_metadata(tmp_path) -> None:
    codex_home = tmp_path / ".codex"

    destination = prepare_codex_home(
        codex_home=codex_home,
        access_token_reference="openshell:resolve:env:CODEX_AUTH_ACCESS_TOKEN",
        account_id="account-123",
        now=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    )

    document = json.loads(destination.read_text())
    assert document["tokens"]["access_token"] == ("openshell:resolve:env:CODEX_AUTH_ACCESS_TOKEN")
    assert document["tokens"]["refresh_token"] == ("openshell:resolve:env:CODEX_AUTH_REFRESH_TOKEN")
    assert document["tokens"]["account_id"] == "account-123"
    id_claims = jwt.decode(
        document["tokens"]["id_token"],
        options={"verify_signature": False, "verify_aud": False},
    )
    assert id_claims["https://api.openai.com/auth.chatgpt_account_id"] == "account-123"
    assert stat.S_IMODE(codex_home.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    rendered = destination.read_text()
    assert "access-from-openbao" not in rendered
    assert "refresh-from-openbao" not in rendered


def test_prepare_from_environment_is_noop_without_codex_provider(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_AUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_AUTH_ACCOUNT_ID", raising=False)

    assert prepare_from_environment() is None


def test_prepare_codex_home_rejects_literal_access_token(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="OpenShell credential reference"):
        prepare_codex_home(
            codex_home=tmp_path / ".codex",
            access_token_reference="access-from-openbao",
            account_id="account-123",
        )
