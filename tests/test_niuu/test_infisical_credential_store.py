"""Tests for InfisicalCredentialStore — Infisical REST API adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from niuu.adapters.infisical_credential_store import InfisicalCredentialStore
from niuu.domain.models import SecretType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW_STR = "2024-01-01T12:00:00+00:00"


def _make_store(**kwargs) -> InfisicalCredentialStore:
    defaults = dict(
        site_url="https://infisical.example.com",
        client_id="cid",
        client_secret="csecret",
        project_id="proj-1",
        environment="dev",
    )
    defaults.update(kwargs)
    return InfisicalCredentialStore(**defaults)


def _mock_response(json_data: object = None, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# __init__ and pure helpers
# ---------------------------------------------------------------------------


def test_init_strips_trailing_slash():
    store = _make_store(site_url="https://infisical.example.com/")
    assert store._site_url == "https://infisical.example.com"


def test_credential_folder():
    store = _make_store()
    assert store._credential_folder("user", "uid-1", "my-cred") == "/users/uid-1/my-cred"


def test_owner_folder():
    store = _make_store()
    assert store._owner_folder("tenant", "ten-1") == "/tenants/ten-1"


# ---------------------------------------------------------------------------
# _get_client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_client_creates_client():
    store = _make_store()
    client = await store._get_client()
    assert client is not None
    assert store._client is client
    await store.close()


@pytest.mark.asyncio
async def test_get_client_returns_existing():
    store = _make_store()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    store._client = mock_client

    result = await store._get_client()
    assert result is mock_client


# ---------------------------------------------------------------------------
# _ensure_authenticated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_authenticated_returns_cached_token():
    store = _make_store()
    store._access_token = "existing-token"

    token = await store._ensure_authenticated()
    assert token == "existing-token"


@pytest.mark.asyncio
async def test_ensure_authenticated_fetches_token():
    store = _make_store()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _mock_response({"accessToken": "new-token"})
    store._client = mock_client

    token = await store._ensure_authenticated()

    assert token == "new-token"
    assert store._access_token == "new-token"
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_authenticated_raises_on_error():
    store = _make_store()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _mock_response({}, status_code=401)
    store._client = mock_client

    with pytest.raises(RuntimeError, match="Infisical auth failed"):
        await store._ensure_authenticated()


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_headers_returns_auth_header():
    store = _make_store()
    store._access_token = "my-token"

    headers = await store._headers()
    assert headers == {"Authorization": "Bearer my-token"}


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_client():
    store = _make_store()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    store._client = mock_client

    await store.close()

    mock_client.aclose.assert_awaited_once()
    assert store._client is None


@pytest.mark.asyncio
async def test_close_noop_when_no_client():
    store = _make_store()
    assert store._client is None
    await store.close()  # should not raise


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_success():
    store = _make_store()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = _mock_response({}, status_code=200)
    store._client = mock_client

    result = await store.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_failure_status():
    store = _make_store()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = _mock_response({}, status_code=503)
    store._client = mock_client

    result = await store.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_exception_returns_false():
    store = _make_store()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = Exception("connection refused")
    store._client = mock_client

    result = await store.health_check()
    assert result is False


# ---------------------------------------------------------------------------
# _parse_meta
# ---------------------------------------------------------------------------


def test_parse_meta_empty_string_returns_none():
    result = InfisicalCredentialStore._parse_meta("")
    assert result is None


def test_parse_meta_invalid_json_returns_none():
    result = InfisicalCredentialStore._parse_meta("{not-json")
    assert result is None


def test_parse_meta_missing_keys_raises_key_error():
    with pytest.raises(KeyError):
        InfisicalCredentialStore._parse_meta(json.dumps({"id": "x"}))


def test_parse_meta_valid_returns_stored_credential():
    meta = {
        "id": "cred-1",
        "name": "my-api-key",
        "secret_type": "api_key",
        "keys": ["api_key"],
        "metadata": {"provider": "openai"},
        "owner_id": "user-1",
        "owner_type": "user",
        "created_at": _NOW_STR,
        "updated_at": _NOW_STR,
    }
    result = InfisicalCredentialStore._parse_meta(json.dumps(meta))

    assert result is not None
    assert result.id == "cred-1"
    assert result.name == "my-api-key"
    assert result.secret_type == SecretType.API_KEY
    assert result.keys == ("api_key",)
    assert result.metadata == {"provider": "openai"}
    assert result.owner_id == "user-1"
    assert result.owner_type == "user"
