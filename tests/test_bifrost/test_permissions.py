"""Tests for per-agent model permissions: wildcard agent IDs and '*' models."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import AgentPermissions, BifrostConfig, ProviderConfig

# ---------------------------------------------------------------------------
# Wildcard agent ID matching in BifrostConfig
# ---------------------------------------------------------------------------


class TestPermissionsForAgent:
    def _cfg(self, permissions: dict) -> BifrostConfig:
        return BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            agent_permissions=permissions,
        )

    def test_exact_match_takes_priority_over_glob(self):
        cfg = self._cfg(
            {
                "tyr": AgentPermissions(allowed_models=["best"]),
                "tyr-*": AgentPermissions(allowed_models=["fast"]),
            }
        )
        perms = cfg.permissions_for_agent("tyr")
        assert perms.allowed_models == ["best"]

    def test_glob_pattern_matches_prefix_wildcard(self):
        cfg = self._cfg(
            {
                "volundr-session-*": AgentPermissions(allowed_models=["balanced", "fast"]),
            }
        )
        perms = cfg.permissions_for_agent("volundr-session-abc123")
        assert perms.allowed_models == ["balanced", "fast"]

    def test_glob_no_match_returns_empty_permissions(self):
        cfg = self._cfg(
            {
                "volundr-session-*": AgentPermissions(allowed_models=["fast"]),
            }
        )
        perms = cfg.permissions_for_agent("unknown-agent")
        assert perms.allowed_models == []

    def test_empty_agent_permissions_unrestricted(self):
        cfg = self._cfg({})
        perms = cfg.permissions_for_agent("any-agent")
        assert perms.allowed_models == []

    def test_first_glob_match_wins(self):
        cfg = self._cfg(
            {
                "tyr-*": AgentPermissions(allowed_models=["best"]),
                "tyr-worker-*": AgentPermissions(allowed_models=["fast"]),
            }
        )
        # tyr-* matches first
        perms = cfg.permissions_for_agent("tyr-worker-1")
        assert perms.allowed_models == ["best"]


# ---------------------------------------------------------------------------
# Wildcard '*' in allowed_models
# ---------------------------------------------------------------------------


@contextmanager
def _make_app_with_permissions(
    agent_id: str,
    allowed_models: list[str],
):
    from bifrost.translation.models import AnthropicResponse, TextBlock, UsageInfo

    cfg = BifrostConfig(
        providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
        agent_permissions={
            agent_id: AgentPermissions(allowed_models=allowed_models),
        },
    )
    app = create_app(cfg)
    mock_response = AnthropicResponse(
        id="msg",
        content=[TextBlock(text="hi")],
        model="claude-sonnet-4-6",
        stop_reason="end_turn",
        usage=UsageInfo(input_tokens=5, output_tokens=2),
    )
    with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
        m.return_value = mock_response
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


class TestModelAccessWildcard:
    def _post(self, client, agent_id: str = "my-agent", model: str = "claude-sonnet-4-6"):
        return client.post(
            "/api/v1/bifrost/v1/messages",
            headers={"x-agent-id": agent_id},
            json={
                "model": model,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    def test_star_wildcard_allows_any_model(self):
        with _make_app_with_permissions("my-agent", ["*"]) as client:
            resp = self._post(client)
            assert resp.status_code == 200

    def test_explicit_model_allowed(self):
        with _make_app_with_permissions("my-agent", ["claude-sonnet-4-6"]) as client:
            resp = self._post(client)
            assert resp.status_code == 200

    def test_model_not_in_list_rejected(self):
        with _make_app_with_permissions("my-agent", ["gpt-4o"]) as client:
            resp = self._post(client, model="claude-sonnet-4-6")
            assert resp.status_code == 403

    def test_empty_allowed_models_unrestricted(self):
        with _make_app_with_permissions("my-agent", []) as client:
            resp = self._post(client)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Admin reload-keys endpoint
# ---------------------------------------------------------------------------


class TestAdminReloadKeys:
    @contextmanager
    def _client(self):
        cfg = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            yield client

    def test_reload_keys_returns_ok(self):
        with self._client() as client:
            resp = client.post("/api/v1/bifrost/admin/reload-keys")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

    def test_reload_keys_method_not_allowed_on_get(self):
        with self._client() as client:
            resp = client.get("/api/v1/bifrost/admin/reload-keys")
            assert resp.status_code == 405
