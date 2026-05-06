"""Tests for PostgresPersonaRegistry."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.personas.loader import PersonaConfig
from ravn.adapters.personas.postgres_registry import (
    PostgresPersonaRegistry,
    _config_to_payload,
    _default_payload,
    _humanize_name,
    _normalize_consumes_events,
    _normalize_executor,
    _normalize_letter,
    _normalize_non_negative_int,
    _normalize_optional_number,
    _normalize_optional_string,
    _normalize_params,
    _normalize_payload,
    _normalize_permission_mode,
    _normalize_schema,
    _parse_payload,
    _payload_to_config,
)


def _mock_row(**overrides):
    defaults = {
        "name": "custom-agent",
        "config_json": {
            "name": "custom-agent",
            "role": "build",
            "letter": "C",
            "color": "var(--color-accent-indigo)",
            "summary": "Custom agent",
            "description": "Custom agent description",
            "system_prompt_template": "You are custom.",
            "allowed_tools": ["read"],
            "forbidden_tools": [],
            "permission_mode": "default",
            "executor": {
                "adapter": "ravn.adapters.executors.cli.CliTransportExecutor",
                "kwargs": {
                    "transport_adapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "transport_kwargs": {"model": ""},
                },
            },
            "iteration_budget": 7,
            "llm_primary_alias": "balanced",
            "llm_thinking_enabled": False,
            "llm_max_tokens": 4096,
            "llm_temperature": None,
            "produces_event_type": "code.changed",
            "produces_schema": {"file": "string"},
            "consumes_events": [{"name": "code.requested"}],
            "fan_in_strategy": "merge",
            "fan_in_params": {},
            "mimir_write_routing": None,
        },
    }
    defaults.update(overrides)
    row = MagicMock()
    row.__getitem__ = lambda self, key: defaults[key]
    return row


class _BuiltinLoader:
    def __init__(self) -> None:
        self._personas = {
            "coding-agent": PersonaConfig(
                name="coding-agent",
                system_prompt_template="Builtin coding agent.",
                allowed_tools=["file"],
                permission_mode="workspace-write",
                iteration_budget=40,
            )
        }

    def list_names(self) -> list[str]:
        return sorted(self._personas)

    def load(self, name: str) -> PersonaConfig | None:
        return self._personas.get(name)

    def is_builtin(self, name: str) -> bool:
        return name in self._personas


def _make_registry():
    pool = AsyncMock()
    loader = _BuiltinLoader()
    return PostgresPersonaRegistry(pool, builtin_loader=loader), pool


class TestListPersonas:
    async def test_merges_builtin_and_user_personas(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [_mock_row()]

        result = await registry.list_personas("user-1")

        names = [item.payload["name"] for item in result]
        assert names == ["coding-agent", "custom-agent"]
        assert result[0].is_builtin is True
        assert result[0].has_override is False
        assert result[1].is_builtin is False
        assert result[1].has_override is True

    async def test_custom_filter_returns_only_user_records(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [_mock_row()]

        result = await registry.list_personas("user-1", source="custom")

        assert [item.payload["name"] for item in result] == ["custom-agent"]


class TestGetPersona:
    async def test_prefers_user_override_for_builtin(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [
            _mock_row(
                name="coding-agent",
                config_json={
                    "name": "coding-agent",
                    "role": "build",
                    "letter": "C",
                    "color": "var(--color-accent-indigo)",
                    "summary": "Overridden coding agent",
                    "description": "Overridden coding agent",
                    "system_prompt_template": "Override prompt.",
                    "allowed_tools": ["read", "write"],
                    "forbidden_tools": [],
                    "permission_mode": "default",
                    "executor": {
                        "adapter": "ravn.adapters.executors.cli.CliTransportExecutor",
                        "kwargs": {
                            "transport_adapter": (
                                "skuld.transports.codex_ws.CodexWebSocketTransport"
                            ),
                            "transport_kwargs": {"model": ""},
                        },
                    },
                    "iteration_budget": 11,
                    "llm_primary_alias": "balanced",
                    "llm_thinking_enabled": True,
                    "llm_max_tokens": 8192,
                    "llm_temperature": None,
                    "produces_event_type": "code.changed",
                    "produces_schema": {"file": "string"},
                    "consumes_events": [{"name": "code.requested"}],
                    "fan_in_strategy": "merge",
                    "fan_in_params": {},
                    "mimir_write_routing": None,
                },
            )
        ]

        result = await registry.get_persona("user-1", "coding-agent")

        assert result is not None
        assert result.is_builtin is True
        assert result.has_override is True
        assert result.yaml_source == "[built-in]"
        assert result.override_source == "[user:user-1]"
        assert result.payload["summary"] == "Overridden coding agent"
        assert result.config.permission_mode == "workspace-write"
        assert result.config.executor.adapter == "ravn.adapters.executors.cli.CliTransportExecutor"

    async def test_get_persona_yaml_returns_runtime_yaml(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [_mock_row()]

        yaml_text = await registry.get_persona_yaml("user-1", "custom-agent")

        assert yaml_text is not None
        assert "name: custom-agent" in yaml_text
        assert "permission_mode: workspace-write" in yaml_text


class TestSavePersona:
    async def test_persists_raw_and_runtime_json(self):
        registry, pool = _make_registry()
        payload = {
            "name": "custom-agent",
            "role": "build",
            "letter": "C",
            "color": "var(--color-accent-indigo)",
            "summary": "Custom agent",
            "description": "Custom agent description",
            "system_prompt_template": "You are custom.",
            "allowed_tools": ["read"],
            "forbidden_tools": [],
            "permission_mode": "safe",
            "executor": {
                "adapter": "ravn.adapters.executors.cli.CliTransportExecutor",
                "kwargs": {
                    "transport_adapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "transport_kwargs": {"model": ""},
                },
            },
            "iteration_budget": 7,
            "llm_primary_alias": "balanced",
            "llm_thinking_enabled": False,
            "llm_max_tokens": 4096,
            "produces_event_type": "code.changed",
            "produces_schema": {"file": "string"},
            "consumes_events": [{"name": "code.requested"}],
        }

        await registry.save_persona("user-1", payload)

        sql = pool.execute.call_args[0][0]
        raw_json = json.loads(pool.execute.call_args[0][3])
        runtime_json = json.loads(pool.execute.call_args[0][4])
        assert "runtime_config_json" in sql
        assert raw_json["permission_mode"] == "safe"
        assert runtime_json["permission_mode"] == "read-only"
        assert raw_json["executor"]["adapter"] == "ravn.adapters.executors.cli.CliTransportExecutor"
        assert (
            runtime_json["executor"]["adapter"]
            == "ravn.adapters.executors.cli.CliTransportExecutor"
        )


class TestDeletePersona:
    async def test_returns_true_when_row_deleted(self):
        registry, pool = _make_registry()
        pool.execute.return_value = "DELETE 1"

        result = await registry.delete_persona("user-1", "custom-agent")

        assert result is True
        assert "DELETE FROM ravn_personas" in pool.execute.call_args[0][0]


class TestParsePayload:
    async def test_handles_string_backed_json_rows(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [
            _mock_row(
                config_json=json.dumps(
                    {
                        "name": "custom-agent",
                        "role": "build",
                        "letter": "C",
                        "color": "var(--color-accent-indigo)",
                        "summary": "Custom agent",
                        "description": "Custom agent",
                        "system_prompt_template": "You are custom.",
                        "allowed_tools": [],
                        "forbidden_tools": [],
                        "permission_mode": "default",
                        "executor": {"adapter": "", "kwargs": {}},
                        "iteration_budget": 0,
                        "llm_primary_alias": "",
                        "llm_thinking_enabled": False,
                        "llm_max_tokens": 0,
                        "llm_temperature": None,
                        "produces_event_type": "",
                        "produces_schema": {},
                        "consumes_events": [],
                        "fan_in_strategy": None,
                        "fan_in_params": {},
                        "mimir_write_routing": None,
                    }
                )
            )
        ]

        result = await registry.list_personas("user-1", source="custom")

        assert len(result) == 1
        assert result[0].payload["name"] == "custom-agent"


class TestHelperNormalization:
    def test_parse_payload_and_default_helpers_cover_invalid_inputs(self):
        assert _parse_payload(None) is None
        assert _parse_payload(json.dumps(["not", "a", "dict"])) is None
        assert _parse_payload({"name": "   "}) is None
        assert _humanize_name("code_reviewer-agent") == "Code reviewer agent"
        assert _humanize_name("   ") == "Custom persona"
        assert _default_payload("custom-agent")["name"] == "custom-agent"
        assert _normalize_letter("", "!!!") == "P"
        assert _normalize_non_negative_int("bad", default=7) == 7
        assert _normalize_non_negative_int(-3, default=7) == 0
        assert _normalize_optional_number("1.5") == 1.5
        assert _normalize_optional_number("bad") is None
        assert _normalize_optional_string("  hi  ") == "hi"
        assert _normalize_optional_string("") is None
        assert _normalize_params({"a": 1}) == {"a": 1}
        assert _normalize_params("bad") == {}
        assert _normalize_schema({"x": " string ", "": "ignored", "y": ""}) == {"x": "string"}
        assert _normalize_schema("bad") == {}
        assert _normalize_permission_mode("") == "default"
        assert _normalize_permission_mode("workspace-write") == "default"
        assert _normalize_executor("bad", {"adapter": "x", "kwargs": {"k": 1}}) == {
            "adapter": "x",
            "kwargs": {"k": 1},
        }
        assert _normalize_executor({"adapter": "", "kwargs": {}}, {}) == {
            "adapter": "",
            "kwargs": {},
        }
        assert _normalize_consumes_events(
            [{"name": " code.requested ", "injects": ["a", ""], "trust": "0.5"}, {}, "bad"]
        ) == [{"name": "code.requested", "injects": ["a"], "trust": 0.5}]

    def test_payload_roundtrip_normalizes_fallback_and_runtime_fields(self):
        fallback = _BuiltinLoader().load("coding-agent")
        assert fallback is not None

        normalized = _normalize_payload(
            {
                "name": "coding-agent",
                "letter": "zebra",
                "allowed_tools": ["read", ""],
                "forbidden_tools": ["rm"],
                "permission_mode": "workspace-write",
                "executor": {"adapter": "demo.Adapter", "kwargs": {"foo": "bar"}},
                "iteration_budget": "9",
                "llm_primary_alias": "fast",
                "llm_thinking_enabled": True,
                "llm_max_tokens": "2048",
                "produces_event_type": "code.changed",
                "produces_schema": {"file": "string", "": "bad"},
                "consumes_events": [{"name": "code.requested", "injects": ["summary", "summary"]}],
                "fan_in_strategy": "collect",
                "fan_in_params": {"contributes_to": "summary"},
            },
            fallback=fallback,
        )

        assert normalized["letter"] == "Z"
        assert normalized["permission_mode"] == "default"
        assert normalized["executor"]["adapter"] == "demo.Adapter"
        assert normalized["produces_schema"] == {"file": "string"}

        config = _payload_to_config(normalized)
        assert config.permission_mode == "workspace-write"
        assert config.executor.adapter == "demo.Adapter"
        assert config.fan_in.contributes_to == "summary"
        assert config.consumes.injects == ["summary"]

        payload = _config_to_payload(config)
        assert payload["permission_mode"] == "default"
        assert payload["executor"]["adapter"] == "demo.Adapter"
        assert payload["consumes_events"] == [{"name": "code.requested"}]

    def test_normalize_payload_requires_name(self):
        with pytest.raises(ValueError, match="Persona name is required"):
            _normalize_payload({})
