"""Tests for bifrost.auth — core identity types and integration."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import jwt
from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.auth import AgentIdentity, AuthMode
from bifrost.config import BifrostConfig, ProviderConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-key-that-is-at-least-32-bytes-long!"


def _make_token(payload: dict) -> str:
    return jwt.encode(payload, _SECRET, algorithm="HS256")


def _make_config(auth_mode: AuthMode = AuthMode.OPEN, secret: str = "") -> BifrostConfig:
    return BifrostConfig(
        providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
        auth_mode=auth_mode,
        pat_secret=secret,
    )


# ---------------------------------------------------------------------------
# AgentIdentity defaults
# ---------------------------------------------------------------------------


class TestAgentIdentity:
    def test_default_values(self):
        identity = AgentIdentity()
        assert identity.agent_id == "anonymous"
        assert identity.tenant_id == "default"
        assert identity.session_id == ""
        assert identity.saga_id == ""

    def test_custom_values(self):
        identity = AgentIdentity(
            agent_id="my-agent",
            tenant_id="my-tenant",
            session_id="s1",
            saga_id="sg1",
        )
        assert identity.agent_id == "my-agent"
        assert identity.tenant_id == "my-tenant"


# ---------------------------------------------------------------------------
# Integration: auth mode wired into the app via TestClient
# ---------------------------------------------------------------------------


class TestAuthIntegration:
    """Verify the FastAPI app enforces the configured auth mode end-to-end."""

    @contextmanager
    def _client_with_pat(self) -> TestClient:
        cfg = _make_config(AuthMode.PAT, _SECRET)
        app = create_app(cfg)
        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            from bifrost.translation.models import AnthropicResponse, TextBlock, UsageInfo

            m.return_value = AnthropicResponse(
                id="msg",
                content=[TextBlock(text="hi")],
                model="claude-sonnet-4-6",
                stop_reason="end_turn",
                usage=UsageInfo(input_tokens=5, output_tokens=2),
            )
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c

    def test_pat_mode_rejects_missing_token(self):
        with self._client_with_pat() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 401

    def test_pat_mode_accepts_valid_token(self):
        token = _make_token({"sub": "agent-test"})
        with self._client_with_pat() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 200

    def test_pat_mode_rejects_admin_endpoint_without_token(self):
        with self._client_with_pat() as client:
            resp = client.post("/api/v1/bifrost/admin/reload-keys")
            assert resp.status_code == 401

    def test_pat_mode_accepts_admin_endpoint_with_valid_token(self):
        token = _make_token({"sub": "operator"})
        with self._client_with_pat() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
