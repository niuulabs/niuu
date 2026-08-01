"""Tests for the central access-only Codex credential broker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import jwt
import pytest

from volundr.adapters.outbound.codex_credential_broker import (
    CODEX_AUTH_ERROR_CODE_METADATA_KEY,
    CODEX_AUTH_LAST_REFRESHED_AT_METADATA_KEY,
    CODEX_AUTH_REFRESH_FAILED,
    CODEX_AUTH_STATE_ACTIVE,
    CODEX_AUTH_STATE_METADATA_KEY,
    CODEX_AUTH_STATE_REQUIRED,
    CodexCredentialBrokerError,
    OpenBaoCodexCredentialBroker,
)
from volundr.domain.models import SecretType


def _access_token(*, expires_in: int) -> str:
    return jwt.encode(
        {"sub": "user", "exp": int(time.time()) + expires_in},
        key="",
        algorithm="none",
    )


def _auth_document(*, access_token: str) -> str:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": access_token,
                "refresh_token": "refresh-from-openbao",
                "account_id": "account-from-openbao",
                "id_token": "id-token-from-openbao",
            },
        }
    )


class _Store:
    def __init__(self, auth_document: str) -> None:
        self.values = {
            "auth.json": auth_document,
            "config.toml": 'model = "gpt-5"',
        }
        self.metadata: dict = {}
        self.stores: list[dict] = []

    async def get(self, owner_type: str, owner_id: str, name: str):
        del owner_type, owner_id, name
        return SimpleNamespace(secret_type=SecretType.GENERIC, metadata=dict(self.metadata))

    async def get_value(self, owner_type: str, owner_id: str, name: str):
        del owner_type, owner_id, name
        return dict(self.values)

    async def store(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict,
        metadata: dict,
    ):
        self.values = dict(data)
        self.metadata = dict(metadata)
        self.stores.append(
            {
                "owner_type": owner_type,
                "owner_id": owner_id,
                "name": name,
                "secret_type": secret_type,
                "data": dict(data),
                "metadata": dict(metadata),
            }
        )
        return SimpleNamespace(secret_type=secret_type, metadata=dict(metadata))


class _RefreshLock:
    def __init__(self) -> None:
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self.held: list[tuple[str, str, str]] = []

    @asynccontextmanager
    async def hold(self, owner_type: str, owner_id: str, name: str):
        key = (owner_type, owner_id, name)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            self.held.append(key)
            yield


class _OAuthResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return dict(self._payload)


class _OAuthClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.posts: list[dict] = []

    async def post(self, url: str, data: dict):
        self.posts.append({"url": url, "data": dict(data)})
        await asyncio.sleep(0)
        return _OAuthResponse(self._payload)


def _broker(store: _Store, lock: _RefreshLock, oauth_client) -> OpenBaoCodexCredentialBroker:
    return OpenBaoCodexCredentialBroker(
        credential_store=store,
        refresh_lock=lock,
        oauth_client=oauth_client,
    )


@pytest.mark.asyncio
async def test_returns_existing_access_token_until_refresh_window() -> None:
    access_token = _access_token(expires_in=3600)
    store = _Store(_auth_document(access_token=access_token))
    lock = _RefreshLock()
    oauth = _OAuthClient({})

    tokens = await _broker(store, lock, oauth).get_tokens(
        owner_id="owner-1",
        credential_name="codex-main",
        credential_field="auth.json",
    )

    assert tokens.access_token == access_token
    assert tokens.account_id == "account-from-openbao"
    assert tokens.expires_in > 3000
    assert oauth.posts == []
    assert store.stores == []
    assert lock.held == [("user", "owner-1", "codex-main")]


@pytest.mark.asyncio
async def test_refreshes_expiring_token_and_persists_full_rotated_document() -> None:
    store = _Store(_auth_document(access_token=_access_token(expires_in=-60)))
    lock = _RefreshLock()
    refreshed_access_token = _access_token(expires_in=7200)
    oauth = _OAuthClient(
        {
            "access_token": refreshed_access_token,
            "refresh_token": "rotated-refresh-token",
            "id_token": "rotated-id-token",
        }
    )

    tokens = await _broker(store, lock, oauth).get_tokens(
        owner_id="owner-1",
        credential_name="codex-main",
        credential_field="auth.json",
    )

    assert tokens.access_token == refreshed_access_token
    assert oauth.posts[0]["data"] == {
        "grant_type": "refresh_token",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "refresh_token": "refresh-from-openbao",
    }
    persisted = json.loads(store.values["auth.json"])
    assert persisted["tokens"]["access_token"] == refreshed_access_token
    assert persisted["tokens"]["refresh_token"] == "rotated-refresh-token"
    assert persisted["tokens"]["id_token"] == "rotated-id-token"
    assert store.values["config.toml"] == 'model = "gpt-5"'
    assert store.metadata[CODEX_AUTH_STATE_METADATA_KEY] == CODEX_AUTH_STATE_ACTIVE
    assert CODEX_AUTH_LAST_REFRESHED_AT_METADATA_KEY in store.metadata


@pytest.mark.asyncio
async def test_concurrent_forced_refresh_reuses_rotation_completed_under_lock() -> None:
    previous_access_token = _access_token(expires_in=3600)
    store = _Store(_auth_document(access_token=previous_access_token))
    lock = _RefreshLock()
    refreshed_access_token = _access_token(expires_in=7200)
    oauth = _OAuthClient(
        {
            "access_token": refreshed_access_token,
            "refresh_token": "single-use-rotated-refresh-token",
        }
    )
    broker = _broker(store, lock, oauth)

    first, second = await asyncio.gather(
        broker.get_tokens(
            owner_id="owner-1",
            credential_name="codex-main",
            credential_field="auth.json",
            force_refresh=True,
            previous_access_token_sha256=hashlib.sha256(previous_access_token.encode()).hexdigest(),
        ),
        broker.get_tokens(
            owner_id="owner-1",
            credential_name="codex-main",
            credential_field="auth.json",
            force_refresh=True,
            previous_access_token_sha256=hashlib.sha256(previous_access_token.encode()).hexdigest(),
        ),
    )

    assert first.access_token == second.access_token == refreshed_access_token
    assert len(oauth.posts) == 1
    assert len(store.stores) == 1
    assert lock.held == [
        ("user", "owner-1", "codex-main"),
        ("user", "owner-1", "codex-main"),
    ]


@pytest.mark.asyncio
async def test_refresh_failure_marks_only_owners_credential_for_reconnection() -> None:
    store = _Store(_auth_document(access_token=_access_token(expires_in=-60)))
    lock = _RefreshLock()

    class FailingOAuthClient:
        async def post(self, _url: str, data: dict):
            raise httpx.HTTPError(f"refresh rejected for {data['refresh_token'][:3]}")

    with pytest.raises(CodexCredentialBrokerError, match="requires reconnection"):
        await _broker(store, lock, FailingOAuthClient()).get_tokens(
            owner_id="owner-1",
            credential_name="codex-main",
            credential_field="auth.json",
        )

    assert len(store.stores) == 1
    assert store.stores[0]["owner_id"] == "owner-1"
    assert store.stores[0]["name"] == "codex-main"
    assert store.metadata[CODEX_AUTH_STATE_METADATA_KEY] == CODEX_AUTH_STATE_REQUIRED
    assert store.metadata[CODEX_AUTH_ERROR_CODE_METADATA_KEY] == CODEX_AUTH_REFRESH_FAILED
