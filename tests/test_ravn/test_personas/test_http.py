"""Unit tests for HttpPersonaAdapter.

All HTTP calls are intercepted by ``respx`` — no real network required.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from ravn.adapters.personas.http import HttpPersonaAdapter
from ravn.adapters.personas.loader import PersonaConfig

# ---------------------------------------------------------------------------
# Sample API payloads
# ---------------------------------------------------------------------------

_DETAIL_CODER: dict = {
    "name": "coder",
    "permission_mode": "workspace-write",
    "allowed_tools": ["file", "git"],
    "forbidden_tools": [],
    "iteration_budget": 40,
    "is_builtin": True,
    "has_override": False,
    "produces_event": "",
    "consumes_events": [],
    "system_prompt_template": "You are a coding agent.",
    "llm": {"primary_alias": "balanced", "thinking_enabled": False, "max_tokens": 0},
    "produces": {"event_type": "", "schema": {}},
    "consumes": {"event_types": [], "injects": []},
    "fan_in": {"strategy": "merge", "contributes_to": ""},
    "yaml_source": "[built-in]",
}

_DETAIL_REVIEWER: dict = {
    "name": "reviewer",
    "permission_mode": "read-only",
    "allowed_tools": ["file"],
    "forbidden_tools": [],
    "iteration_budget": 20,
    "is_builtin": False,
    "has_override": False,
    "produces_event": "review.completed",
    "consumes_events": ["code.changed"],
    "system_prompt_template": "You are a reviewer.",
    "llm": {"primary_alias": "powerful", "thinking_enabled": True, "max_tokens": 8192},
    "produces": {"event_type": "review.completed", "schema": {}},
    "consumes": {"event_types": ["code.changed"], "injects": ["diff"]},
    "fan_in": {"strategy": "all_must_pass", "contributes_to": "verdict"},
    "yaml_source": "/custom/reviewer.yaml",
}

_SUMMARIES: list[dict] = [
    {
        "name": "coder",
        "permission_mode": "workspace-write",
        "allowed_tools": ["file"],
        "iteration_budget": 40,
        "is_builtin": True,
        "has_override": False,
        "produces_event": "",
        "consumes_events": [],
    },
    {
        "name": "reviewer",
        "permission_mode": "read-only",
        "allowed_tools": [],
        "iteration_budget": 20,
        "is_builtin": False,
        "has_override": False,
        "produces_event": "review.completed",
        "consumes_events": ["code.changed"],
    },
]

_BASE = "http://volundr.test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _adapter(**kwargs: object) -> HttpPersonaAdapter:
    return HttpPersonaAdapter(base_url=_BASE, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# load — happy path
# ---------------------------------------------------------------------------


class TestLoad:
    @respx.mock
    def test_load_returns_persona_config(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        adapter = _adapter()
        config = adapter.load("coder")

        assert isinstance(config, PersonaConfig)
        assert config.name == "coder"
        assert config.permission_mode == "workspace-write"
        assert config.allowed_tools == ["file", "git"]
        assert config.iteration_budget == 40

    @respx.mock
    def test_load_parses_llm_fields(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas/reviewer").mock(
            return_value=httpx.Response(200, json=_DETAIL_REVIEWER)
        )
        config = _adapter().load("reviewer")
        assert config is not None
        assert config.llm.primary_alias == "powerful"
        assert config.llm.thinking_enabled is True
        assert config.llm.max_tokens == 8192

    @respx.mock
    def test_load_parses_consumes_and_fan_in(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas/reviewer").mock(
            return_value=httpx.Response(200, json=_DETAIL_REVIEWER)
        )
        config = _adapter().load("reviewer")
        assert config is not None
        assert config.consumes.event_types == ["code.changed"]
        assert config.consumes.injects == ["diff"]
        assert config.fan_in.strategy == "all_must_pass"
        assert config.fan_in.contributes_to == "verdict"

    @respx.mock
    def test_load_parses_produces(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas/reviewer").mock(
            return_value=httpx.Response(200, json=_DETAIL_REVIEWER)
        )
        config = _adapter().load("reviewer")
        assert config is not None
        assert config.produces.event_type == "review.completed"

    # ------------------------------------------------------------------
    # 404 path
    # ------------------------------------------------------------------

    @respx.mock
    def test_load_returns_none_on_404(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas/missing").mock(
            return_value=httpx.Response(404, json={"detail": "Persona not found: missing"})
        )
        config = _adapter().load("missing")
        assert config is None

    # ------------------------------------------------------------------
    # 5xx fail-closed
    # ------------------------------------------------------------------

    @respx.mock
    def test_load_returns_none_on_500_no_cache(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas/coder").mock(return_value=httpx.Response(500))
        config = _adapter().load("coder")
        assert config is None

    @respx.mock
    def test_load_returns_stale_cache_on_500(self) -> None:
        adapter = _adapter()
        # Prime the cache with a successful response
        respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        config_first = adapter.load("coder")
        assert config_first is not None

        # Force TTL expiry
        adapter._persona_cache["coder"].expires_at = time.monotonic() - 1.0

        # Server now returns 500
        respx.get(f"{_BASE}/api/v1/personas/coder").mock(return_value=httpx.Response(500))
        config_stale = adapter.load("coder")
        assert config_stale is not None
        assert config_stale.name == "coder"

    @respx.mock
    def test_load_does_not_raise_on_network_error(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        # Must not raise — fail-closed returns None
        config = _adapter().load("coder")
        assert config is None

    @respx.mock
    def test_load_returns_stale_cache_on_network_error(self) -> None:
        adapter = _adapter()
        # Prime cache
        respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        adapter.load("coder")

        # Expire cache, then fail
        adapter._persona_cache["coder"].expires_at = time.monotonic() - 1.0
        respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        config = adapter.load("coder")
        assert config is not None
        assert config.name == "coder"

    # ------------------------------------------------------------------
    # Warning logged on errors
    # ------------------------------------------------------------------

    @respx.mock
    def test_load_logs_warning_on_5xx(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        respx.get(f"{_BASE}/api/v1/personas/coder").mock(return_value=httpx.Response(503))
        with caplog.at_level(logging.WARNING, logger="ravn.adapters.personas.http"):
            _adapter().load("coder")
        assert any("503" in r.message for r in caplog.records)

    @respx.mock
    def test_load_logs_warning_on_network_error(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with caplog.at_level(logging.WARNING, logger="ravn.adapters.personas.http"):
            _adapter().load("coder")
        assert caplog.records


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class TestCache:
    @respx.mock
    def test_cache_hit_single_http_request(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        adapter = _adapter()
        adapter.load("coder")
        adapter.load("coder")  # second call — must use cache

        assert route.call_count == 1

    @respx.mock
    def test_cache_expiry_triggers_new_request(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        adapter = _adapter(cache_ttl_seconds=60)
        adapter.load("coder")

        # Manually expire the cache entry
        adapter._persona_cache["coder"].expires_at = time.monotonic() - 1.0

        adapter.load("coder")  # must hit server again

        assert route.call_count == 2

    @respx.mock
    def test_list_names_cache_hit(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/personas").mock(
            return_value=httpx.Response(200, json=_SUMMARIES)
        )
        adapter = _adapter()
        adapter.list_names()
        adapter.list_names()

        assert route.call_count == 1

    @respx.mock
    def test_list_names_cache_expiry(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/personas").mock(
            return_value=httpx.Response(200, json=_SUMMARIES)
        )
        adapter = _adapter(cache_ttl_seconds=60)
        adapter.list_names()

        adapter._names_cache.expires_at = time.monotonic() - 1.0  # type: ignore[union-attr]
        adapter.list_names()

        assert route.call_count == 2


# ---------------------------------------------------------------------------
# list_names
# ---------------------------------------------------------------------------


class TestListNames:
    @respx.mock
    def test_list_names_returns_sorted_names(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas").mock(
            return_value=httpx.Response(200, json=_SUMMARIES)
        )
        names = _adapter().list_names()
        assert names == ["coder", "reviewer"]

    @respx.mock
    def test_list_names_returns_empty_on_500_no_cache(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas").mock(return_value=httpx.Response(500))
        names = _adapter().list_names()
        assert names == []

    @respx.mock
    def test_list_names_returns_stale_cache_on_500(self) -> None:
        adapter = _adapter()
        respx.get(f"{_BASE}/api/v1/personas").mock(
            return_value=httpx.Response(200, json=_SUMMARIES)
        )
        adapter.list_names()

        adapter._names_cache.expires_at = time.monotonic() - 1.0  # type: ignore[union-attr]
        respx.get(f"{_BASE}/api/v1/personas").mock(return_value=httpx.Response(500))
        names = adapter.list_names()
        assert names == ["coder", "reviewer"]

    @respx.mock
    def test_list_names_does_not_raise_on_network_error(self) -> None:
        respx.get(f"{_BASE}/api/v1/personas").mock(side_effect=httpx.ConnectError("refused"))
        names = _adapter().list_names()
        assert names == []

    @respx.mock
    def test_list_names_returns_stale_cache_on_network_error(self) -> None:
        adapter = _adapter()
        respx.get(f"{_BASE}/api/v1/personas").mock(
            return_value=httpx.Response(200, json=_SUMMARIES)
        )
        adapter.list_names()

        adapter._names_cache.expires_at = time.monotonic() - 1.0  # type: ignore[union-attr]
        respx.get(f"{_BASE}/api/v1/personas").mock(side_effect=httpx.ConnectError("refused"))
        names = adapter.list_names()
        assert names == ["coder", "reviewer"]


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


class TestAuth:
    @respx.mock
    def test_bearer_header_sent_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAVN_VOLUNDR_TOKEN", "my-secret-pat")
        route = respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        _adapter(token_env="RAVN_VOLUNDR_TOKEN").load("coder")

        assert route.calls.last is not None
        auth = route.calls.last.request.headers.get("authorization", "")
        assert auth == "Bearer my-secret-pat"

    @respx.mock
    def test_no_auth_header_when_env_var_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RAVN_VOLUNDR_TOKEN", raising=False)
        route = respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        _adapter(token_env="RAVN_VOLUNDR_TOKEN").load("coder")

        assert route.calls.last is not None
        assert "authorization" not in route.calls.last.request.headers

    @respx.mock
    def test_custom_token_env_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CUSTOM_TOKEN_VAR", "custom-token-value")
        route = respx.get(f"{_BASE}/api/v1/personas/coder").mock(
            return_value=httpx.Response(200, json=_DETAIL_CODER)
        )
        _adapter(token_env="CUSTOM_TOKEN_VAR").load("coder")

        assert route.calls.last is not None
        auth = route.calls.last.request.headers.get("authorization", "")
        assert auth == "Bearer custom-token-value"

    @respx.mock
    def test_bearer_header_sent_for_list_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAVN_VOLUNDR_TOKEN", "list-token")
        route = respx.get(f"{_BASE}/api/v1/personas").mock(
            return_value=httpx.Response(200, json=_SUMMARIES)
        )
        _adapter(token_env="RAVN_VOLUNDR_TOKEN").list_names()

        assert route.calls.last is not None
        auth = route.calls.last.request.headers.get("authorization", "")
        assert auth == "Bearer list-token"


# ---------------------------------------------------------------------------
# Write operations — NotImplementedError
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_save_raises_not_implemented(self) -> None:
        adapter = _adapter()
        config = PersonaConfig(name="x")
        with pytest.raises(NotImplementedError, match="read-only"):
            adapter.save(config)

    def test_delete_raises_not_implemented(self) -> None:
        adapter = _adapter()
        with pytest.raises(NotImplementedError, match="read-only"):
            adapter.delete("x")

    def test_save_error_message_mentions_rest_api(self) -> None:
        adapter = _adapter()
        with pytest.raises(NotImplementedError, match="volundr REST API"):
            adapter.save(PersonaConfig(name="x"))

    def test_delete_error_message_mentions_rest_api(self) -> None:
        adapter = _adapter()
        with pytest.raises(NotImplementedError, match="volundr REST API"):
            adapter.delete("x")


# ---------------------------------------------------------------------------
# PersonaPort compliance
# ---------------------------------------------------------------------------


class TestPortCompliance:
    def test_adapter_is_persona_port_instance(self) -> None:
        from ravn.ports.persona import PersonaPort

        adapter = _adapter()
        assert isinstance(adapter, PersonaPort)
