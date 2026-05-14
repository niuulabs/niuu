"""Tests for VolundrAdapterFactory — multi-cluster support."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.domain.models import IntegrationConnection, IntegrationType
from tests.test_ting.conftest import StubCredentialStore, StubIntegrationRepo
from ting.adapters.volundr_factory import DEFAULT_VOLUNDR_URL, VolundrAdapterFactory
from ting.adapters.volundr_http import VolundrHTTPAdapter

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_connection(
    *,
    conn_id: str = "conn-1",
    enabled: bool = True,
    integration_type: IntegrationType = IntegrationType.CODE_FORGE,
    credential_name: str = "volundr-pat",
    config: dict | None = None,
    slug: str = "",
) -> IntegrationConnection:
    return IntegrationConnection(
        id=conn_id,
        owner_id="owner-1",
        integration_type=integration_type,
        adapter="ting.adapters.volundr_http.VolundrHTTPAdapter",
        credential_name=credential_name,
        config={"url": "http://volundr-test:8000"} if config is None else config,
        enabled=enabled,
        created_at=_NOW,
        updated_at=_NOW,
        slug=slug,
    )


# ---------------------------------------------------------------------------
# Tests — for_owner (returns only authenticated adapters, no fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_code_forge_connection_returns_empty() -> None:
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_disabled_connection_returns_empty() -> None:
    conn = _make_connection(enabled=False)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-123"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_enabled_connection_valid_cred_returns_adapter() -> None:
    conn = _make_connection(enabled=True)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-123"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert isinstance(result[0], VolundrHTTPAdapter)
    assert result[0]._api_key == "tok-123"
    assert result[0]._base_url == "http://volundr-test:8000"


@pytest.mark.asyncio
async def test_credential_store_returns_none_returns_empty() -> None:
    conn = _make_connection(enabled=True)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(values={}),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_credential_store_returns_none_allows_unauthenticated_in_dev() -> None:
    conn = _make_connection(enabled=True, config={"url": "http://cluster-a:8000", "name": "alpha"})
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(values={}),
        allow_unauthenticated=True,
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert result[0]._base_url == "http://cluster-a:8000"
    assert result[0]._api_key is None
    assert result[0]._name == "alpha"


@pytest.mark.asyncio
async def test_uses_default_url_when_not_in_config() -> None:
    conn = _make_connection(enabled=True, config={})
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-456"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert result[0]._base_url == DEFAULT_VOLUNDR_URL


@pytest.mark.asyncio
async def test_multiple_connections_return_multiple_adapters() -> None:
    conn1 = _make_connection(
        conn_id="conn-1",
        config={"url": "http://cluster-a:8000", "name": "alpha"},
        credential_name="pat-a",
    )
    conn2 = _make_connection(
        conn_id="conn-2",
        config={"url": "http://cluster-b:8000", "name": "beta"},
        credential_name="pat-b",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn1, conn2]),
        credential_store=StubCredentialStore(
            values={
                "user:owner-1:pat-a": {"token": "tok-a"},
                "user:owner-1:pat-b": {"token": "tok-b"},
            }
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 2
    assert result[0]._base_url == "http://cluster-a:8000"
    assert result[0]._api_key == "tok-a"
    assert result[0]._name == "alpha"
    assert result[1]._base_url == "http://cluster-b:8000"
    assert result[1]._api_key == "tok-b"
    assert result[1]._name == "beta"


@pytest.mark.asyncio
async def test_skips_connection_with_no_cred() -> None:
    """One connection has a credential, the other doesn't — only the valid one is returned."""
    conn1 = _make_connection(
        conn_id="conn-1",
        config={"url": "http://cluster-a:8000"},
        credential_name="pat-a",
    )
    conn2 = _make_connection(
        conn_id="conn-2",
        config={"url": "http://cluster-b:8000"},
        credential_name="pat-b",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn1, conn2]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:pat-a": {"token": "tok-a"}},
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert result[0]._base_url == "http://cluster-a:8000"


@pytest.mark.asyncio
async def test_adapter_name_uses_slug_when_no_config_name() -> None:
    conn = _make_connection(
        config={"url": "http://volundr:8000"},
        slug="my-volundr",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-1"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert result[0]._name == "my-volundr"


@pytest.mark.asyncio
async def test_adapter_name_falls_back_to_conn_id() -> None:
    conn = _make_connection(config={"url": "http://volundr:8000"})
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-1"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert result[0]._name == "conn-1"


# ---------------------------------------------------------------------------
# Tests — primary_for_owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_for_owner_returns_first() -> None:
    conn1 = _make_connection(
        conn_id="conn-1",
        config={"url": "http://primary:8000"},
        credential_name="pat-a",
    )
    conn2 = _make_connection(
        conn_id="conn-2",
        config={"url": "http://secondary:8000"},
        credential_name="pat-b",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn1, conn2]),
        credential_store=StubCredentialStore(
            values={
                "user:owner-1:pat-a": {"token": "tok-a"},
                "user:owner-1:pat-b": {"token": "tok-b"},
            }
        ),
    )
    result = await factory.primary_for_owner("owner-1")
    assert result is not None
    assert isinstance(result, VolundrHTTPAdapter)
    assert result._base_url == "http://primary:8000"


@pytest.mark.asyncio
async def test_primary_for_owner_returns_none_when_empty() -> None:
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
    )
    result = await factory.primary_for_owner("owner-1")
    assert result is None


# ---------------------------------------------------------------------------
# Tests — error handling in _resolve_connections
# ---------------------------------------------------------------------------


class _FailingCredentialStore(StubCredentialStore):
    """Credential store that raises on get_value."""

    async def get_value(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        raise RuntimeError("credential store unavailable")


@pytest.mark.asyncio
async def test_credential_store_error_skips_connection() -> None:
    """If the credential store raises, the connection should be skipped (not crash)."""
    conn = _make_connection(enabled=True)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=_FailingCredentialStore(),
    )
    result = await factory.for_owner("owner-1")
    assert result == []
