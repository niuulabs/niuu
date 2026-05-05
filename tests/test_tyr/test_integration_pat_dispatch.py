"""Integration tests — autonomous dispatch with stored PAT credential end-to-end.

Validates the full PAT-based autonomous dispatch flow:
  factory resolves stored PAT → VolundrHTTPAdapter constructed with api_key
  → spawn_session called with Authorization: Bearer <pat>
  → no set_auth_token() call required.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from niuu.domain.models import IntegrationConnection, IntegrationType
from tests.test_tyr.conftest import StubCredentialStore, StubIntegrationRepo
from tyr.adapters.volundr_factory import VolundrAdapterFactory
from tyr.adapters.volundr_http import VolundrHTTPAdapter
from tyr.ports.volundr import SpawnRequest

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)

VOLUNDR_BASE = "http://volundr-test:8000"
SESSIONS_URL = f"{VOLUNDR_BASE}/api/v1/forge/sessions"
STORED_PAT = "test-pat-jwt"
OWNER_ID = "user-123"


def _make_connection(
    *,
    enabled: bool = True,
    config: dict | None = None,
) -> IntegrationConnection:
    return IntegrationConnection(
        id="conn-1",
        owner_id=OWNER_ID,
        integration_type=IntegrationType.CODE_FORGE,
        adapter="tyr.adapters.volundr_http.VolundrHTTPAdapter",
        credential_name="volundr-pat",
        config={"url": VOLUNDR_BASE} if config is None else config,
        enabled=enabled,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _spawn_request() -> SpawnRequest:
    return SpawnRequest(
        name="autonomous-session",
        repo="niuulabs/volundr",
        branch="feat/alpha",
        model="claude-sonnet-4-6",
        tracker_issue_id="NIU-234",
        tracker_issue_url="https://linear.app/niuu/issue/NIU-234",
        system_prompt="You are a senior software engineer.",
        initial_prompt="Implement the feature.",
        base_branch="dev",
    )


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestAutonomousDispatchWithPAT:
    """Verifies the full autonomous dispatch path:

    factory resolves stored PAT → VolundrHTTPAdapter constructed with api_key
    → spawn_session called with Authorization: Bearer <pat>
    → no set_auth_token() call required.
    """

    @pytest.mark.asyncio
    @respx.mock
    async def test_factory_resolves_pat_and_dispatches(self) -> None:
        """End-to-end: factory → adapter → spawn_session with stored PAT."""
        conn = _make_connection()
        factory = VolundrAdapterFactory(
            integration_repo=StubIntegrationRepo(connections=[conn]),
            credential_store=StubCredentialStore(
                values={f"user:{OWNER_ID}:volundr-pat": {"token": STORED_PAT}}
            ),
        )

        route = respx.post(SESSIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-auto-1",
                    "name": "autonomous-session",
                    "status": "creating",
                    "tracker_issue_id": "NIU-234",
                },
            )
        )

        adapters = await factory.for_owner(OWNER_ID)
        assert len(adapters) == 1
        adapter = adapters[0]

        session = await adapter.spawn_session(_spawn_request())

        # Verify the stored PAT was used as the Authorization header
        sent = route.calls[0].request
        assert sent.headers["Authorization"] == f"Bearer {STORED_PAT}"

        # Verify adapter state: api_key is set
        assert adapter._api_key == STORED_PAT

        # Verify the session was created correctly
        assert session.id == "ses-auto-1"
        assert session.status == "creating"
        assert session.tracker_issue_id == "NIU-234"

    @pytest.mark.asyncio
    async def test_factory_returns_empty_when_no_connection(self) -> None:
        """Factory returns empty list when no CODE_FORGE connection exists."""
        factory = VolundrAdapterFactory(
            integration_repo=StubIntegrationRepo(connections=[]),
            credential_store=StubCredentialStore(),
        )

        result = await factory.for_owner(OWNER_ID)
        assert result == []

    @pytest.mark.asyncio
    async def test_primary_for_owner_returns_none_when_no_connection(self) -> None:
        """primary_for_owner returns None when no CODE_FORGE connection exists."""
        factory = VolundrAdapterFactory(
            integration_repo=StubIntegrationRepo(connections=[]),
            credential_store=StubCredentialStore(),
        )

        result = await factory.primary_for_owner(OWNER_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_per_request_auth_token_overrides_stored_pat(self) -> None:
        """Per-request auth_token takes precedence over stored api_key."""
        adapter = VolundrHTTPAdapter(base_url=VOLUNDR_BASE, api_key=STORED_PAT)

        # Stored PAT is the default
        assert adapter._headers()["Authorization"] == f"Bearer {STORED_PAT}"

        # Per-request auth_token overrides stored PAT
        headers = adapter._headers(auth_token="request-token")
        assert headers["Authorization"] == "Bearer request-token"

        # Without auth_token, falls back to stored PAT
        assert adapter._headers()["Authorization"] == f"Bearer {STORED_PAT}"

    @pytest.mark.asyncio
    async def test_dispatch_without_any_credentials_sends_no_auth(self) -> None:
        """Adapter with no api_key and no runtime token sends no Authorization header."""
        adapter = VolundrHTTPAdapter(base_url=VOLUNDR_BASE)

        headers = adapter._headers()
        assert headers == {}
        assert "Authorization" not in headers
