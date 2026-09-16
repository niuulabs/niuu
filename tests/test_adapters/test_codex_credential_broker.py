"""The Codex broker authorizes delivery; OpenBao alone owns refresh."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest

from niuu.domain.oauth_credentials import OAuthCredentialUnavailableError
from volundr.adapters.outbound.codex_credential_broker import (
    CodexCredentialBrokerError,
    DisabledCodexCredentialBroker,
    OpenBaoCodexCredentialBroker,
)


@pytest.fixture
def store():
    token = jwt.encode({"exp": int(time.time()) + 3600}, key="", algorithm="none")
    return SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                metadata={
                    "tenant_id": "tenant-a",
                    "renewal_owner": "openbao_oauthapp",
                    "oauth_format": "codex_auth",
                    "oauth_token_field": "auth.json",
                }
            )
        ),
        get_value=AsyncMock(
            return_value={
                "auth.json": json.dumps(
                    {
                        "tokens": {"access_token": token, "account_id": "account"},
                        "chatgpt_plan_type": "pro",
                    }
                ),
                "expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
            }
        ),
        store=AsyncMock(),
    )


async def get_tokens(store, **kwargs):
    return await OpenBaoCodexCredentialBroker(credential_store=store).get_tokens(
        **{
            "owner_id": "alice",
            "tenant_id": "tenant-a",
            "credential_name": "codex",
            "credential_field": "auth.json",
            **kwargs,
        }
    )


async def test_concurrent_delivery_is_access_only_and_does_not_write(store):
    responses = await asyncio.gather(*(get_tokens(store) for _ in range(8)))
    assert all(r.account_id == "account" and r.plan_type == "pro" for r in responses)
    assert all(0 < r.expires_in <= 120 for r in responses)
    store.store.assert_not_called()
    store.get.assert_awaited_with("user", "alice", "codex")


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": "other"},
        {"credential_field": "other"},
        {"owner_id": ""},
    ],
)
async def test_scope_failures_do_not_read_grant(store, change):
    with pytest.raises(CodexCredentialBrokerError):
        await get_tokens(store, **change)
    store.get_value.assert_not_called()


async def test_unmigrated_credentials_are_not_refreshed_by_application(store):
    store.get.return_value.metadata.pop("renewal_owner")
    with pytest.raises(CodexCredentialBrokerError, match="migration"):
        await get_tokens(store)
    store.get_value.assert_not_called()
    store.store.assert_not_called()


async def test_missing_credential(store):
    store.get.return_value = None
    with pytest.raises(CodexCredentialBrokerError, match="unavailable"):
        await get_tokens(store)


@pytest.mark.parametrize("reconnect", [False, True])
async def test_engine_failure_preserves_reconnect_semantics(store, reconnect):
    store.get_value.side_effect = OAuthCredentialUnavailableError(reconnect=reconnect)
    with pytest.raises(CodexCredentialBrokerError) as exc:
        await get_tokens(store)
    assert exc.value.reconnect is reconnect


async def test_expired_engine_token_is_not_returned(store):
    store.get_value.return_value["expires_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(CodexCredentialBrokerError) as exc:
        await get_tokens(store)
    assert exc.value.reconnect is False


async def test_auth_retry_accepts_a_token_already_rotated_by_openbao(store):
    result = await get_tokens(store, force_refresh=True, previous_access_token_sha256="a" * 64)
    assert result.account_id == "account"


@pytest.mark.parametrize("with_hash", [False, True])
async def test_rejected_token_is_not_returned_as_a_successful_refresh(store, with_hash):
    token = json.loads(store.get_value.return_value["auth.json"])["tokens"]["access_token"]
    previous = hashlib.sha256(token.encode()).hexdigest() if with_hash else ""
    with pytest.raises(CodexCredentialBrokerError, match="reconnect"):
        await get_tokens(store, force_refresh=True, previous_access_token_sha256=previous)


@pytest.mark.parametrize("document", [None, "not json", "[]", '{"tokens":{}}'])
async def test_invalid_engine_document_is_sanitized(store, document):
    store.get_value.return_value = {"auth.json": document}
    with pytest.raises(CodexCredentialBrokerError, match="unavailable"):
        await get_tokens(store)


async def test_disabled_local_broker():
    with pytest.raises(CodexCredentialBrokerError, match="disabled"):
        await DisabledCodexCredentialBroker().get_tokens(
            owner_id="a", tenant_id="t", credential_name="c", credential_field="auth.json"
        )
