"""Tests for PostgresPersonaRegistry."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personas.adapters.postgres_registry import (
    PostgresPersonaRegistry,
    _config_to_payload,
    _default_payload,
    _humanize_name,
    _normalize_consumes_events,
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
from personas.domain.persona import PersonaConfig, PersonaProduces
from personas.domain.persona_document import (
    PersonaDocumentError,
    persona_definition_from_mapping,
    portable_persona_from_config,
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
            "permission_mode": "default",
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
            "coder": PersonaConfig(
                name="coder",
                system_prompt_template="Builtin coder.",
                permission_mode="workspace-write",
                iteration_budget=40,
            )
        }

    def list_names(self) -> list[str]:
        return sorted(self._personas)

    def list_builtin_names(self) -> list[str]:
        return self.list_names()

    def load(self, name: str) -> PersonaConfig | None:
        return self._personas.get(name)

    def load_current_portable(self, name: str):  # noqa: ANN201
        config = self._personas.get(name)
        return portable_persona_from_config(config) if config is not None else None

    def is_builtin(self, name: str) -> bool:
        return name in self._personas


def _make_registry():
    pool = AsyncMock()
    loader = _BuiltinLoader()
    return PostgresPersonaRegistry(pool, builtin_loader=loader), pool


class TestPortablePersonas:
    async def test_builtin_uses_raw_source_document(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = []

        document = await registry.get_current_portable_persona("user-1", "coder")

        assert document is not None
        assert document.id == "coder"
        assert document.revision.startswith("content-")
        assert document.definition["system_prompt_template"] == "Builtin coder."

    async def test_custom_persona_is_owner_scoped_and_exactly_addressable(self):
        registry, pool = _make_registry()
        row = _mock_row()
        pool.fetch.return_value = [row]

        document = await registry.get_current_portable_persona("user-1", "custom-agent")

        assert document is not None
        assert document.definition["system_prompt_template"] == "You are custom."
        exact = await registry.get_portable_persona_revision(
            "user-1", "custom-agent", document.revision
        )
        assert exact == document
        assert (
            await registry.get_portable_persona_revision(
                "user-1", "custom-agent", "content-0000000000000000"
            )
            is None
        )
        assert pool.fetch.await_args.args[1:] == ("user-1", "custom-agent")

    async def test_stored_persona_still_carrying_an_executor_is_refused(self):
        registry, pool = _make_registry()
        row = _mock_row()
        row["config_json"]["executor"] = {"adapter": "some.module.Executor", "kwargs": {}}
        pool.fetch.return_value = [row]

        with pytest.raises(ValueError, match="declares an 'executor' block.*Remove"):
            await registry.get_current_portable_persona("user-1", "custom-agent")

    @pytest.mark.parametrize("key", ["allowed_tools", "forbidden_tools"])
    async def test_stored_persona_still_carrying_a_tool_restriction_is_refused(self, key):
        registry, pool = _make_registry()
        row = _mock_row()
        row["config_json"][key] = ["file_read"]
        pool.fetch.return_value = [row]

        with pytest.raises(
            ValueError,
            match=rf"persona custom-agent: {key} is not supported: per-harness tool restrictions",
        ):
            await registry.get_current_portable_persona("user-1", "custom-agent")


class TestListPersonas:
    async def test_merges_builtin_and_user_personas(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [_mock_row()]

        result = await registry.list_personas("user-1")

        names = [item.payload["name"] for item in result]
        assert names == ["coder", "custom-agent"]
        assert result[0].is_builtin is True
        assert result[0].has_override is False
        assert result[1].is_builtin is False
        assert result[1].has_override is True

    async def test_custom_filter_returns_only_user_records(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [_mock_row()]

        result = await registry.list_personas("user-1", source="custom")

        assert [item.payload["name"] for item in result] == ["custom-agent"]

    async def test_stored_override_of_a_removed_builtin_lists_as_custom(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [_mock_row(name="coding-agent")]
        pool.fetch.return_value[0]["config_json"]["name"] = "coding-agent"

        result = await registry.list_personas("user-1")

        by_name = {item.payload["name"]: item for item in result}
        assert sorted(by_name) == ["coder", "coding-agent"]
        assert by_name["coding-agent"].is_builtin is False
        assert by_name["coding-agent"].has_override is True


class TestGetPersona:
    async def test_prefers_user_override_for_builtin(self):
        registry, pool = _make_registry()
        pool.fetch.return_value = [
            _mock_row(
                name="coder",
                config_json={
                    "name": "coder",
                    "role": "build",
                    "letter": "C",
                    "color": "var(--color-accent-indigo)",
                    "summary": "Overridden coder",
                    "description": "Overridden coder",
                    "system_prompt_template": "Override prompt.",
                    "permission_mode": "default",
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

        result = await registry.get_persona("user-1", "coder")

        assert result is not None
        assert result.is_builtin is True
        assert result.has_override is True
        assert result.yaml_source == "[built-in]"
        assert result.override_source == "[user:user-1]"
        assert result.payload["summary"] == "Overridden coder"
        assert result.config.permission_mode == "workspace-write"

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
            "permission_mode": "safe",
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
        assert "executor" not in raw_json
        assert "executor" not in runtime_json

    async def test_refuses_a_payload_that_names_an_executor(self):
        registry, pool = _make_registry()

        with pytest.raises(ValueError, match="declares an 'executor' block.*Remove"):
            await registry.save_persona(
                "user-1",
                {"name": "custom-agent", "executor": {"adapter": "some.module.Executor"}},
            )

        pool.execute.assert_not_awaited()

    @pytest.mark.parametrize("key", ["allowed_tools", "forbidden_tools"])
    async def test_refuses_a_payload_that_carries_a_tool_restriction(self, key):
        registry, pool = _make_registry()

        with pytest.raises(
            ValueError,
            match=rf"persona custom-agent: {key} is not supported.*remove allowed_tools",
        ):
            await registry.save_persona("user-1", {"name": "custom-agent", key: []})

        pool.execute.assert_not_awaited()


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
                        "permission_mode": "default",
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
        assert _normalize_consumes_events(
            [{"name": " code.requested ", "injects": ["a", ""], "trust": "0.5"}, {}, "bad"]
        ) == [{"name": "code.requested", "injects": ["a"], "trust": 0.5}]

    def test_payload_roundtrip_normalizes_fallback_and_runtime_fields(self):
        fallback = _BuiltinLoader().load("coder")
        assert fallback is not None

        normalized = _normalize_payload(
            {
                "name": "coder",
                "letter": "zebra",
                "permission_mode": "workspace-write",
                "iteration_budget": "9",
                "llm_primary_alias": "fast",
                "llm_thinking_enabled": True,
                "llm_max_tokens": "2048",
                "produces_event_type": "code.changed",
                "produces_schema": {"file": "string", "": "bad"},
                "consumes_events": [{"name": "code.requested", "injects": ["summary", "summary"]}],
                "fan_in_strategy": "quorum",
                "fan_in_params": {"contributes_to": "summary"},
            },
            fallback=fallback,
        )

        assert normalized["letter"] == "Z"
        assert normalized["permission_mode"] == "default"
        assert "executor" not in normalized
        assert normalized["produces_schema"] == {"file": "string"}

        config = _payload_to_config(normalized)
        assert config.permission_mode == "workspace-write"
        assert config.fan_in.strategy == "majority"
        assert config.fan_in.contributes_to == "summary"
        assert config.consumes.injects == ["summary"]

        payload = _config_to_payload(config)
        assert payload["permission_mode"] == "default"
        assert "executor" not in payload
        assert payload["consumes_events"] == [{"name": "code.requested"}]

    def test_an_unknown_fan_in_strategy_is_refused_not_merged(self):
        fallback = _BuiltinLoader().load("coder")
        normalized = _normalize_payload(
            {"name": "coder", "fan_in_strategy": "collect"}, fallback=fallback
        )

        with pytest.raises(ValueError, match="'collect' is not a fan-in strategy"):
            _payload_to_config(normalized)

    def test_normalize_payload_requires_name(self):
        with pytest.raises(ValueError, match="Persona name is required"):
            _normalize_payload({})

    def test_outcome_event_map_survives_storage_payload_round_trip(self):
        config = PersonaConfig(
            name="reviewer",
            produces=PersonaProduces(
                event_type="review.completed",
                event_type_map={
                    "pass": "review.passed",
                    "needs_changes": "review.changes_requested",
                },
                when={"pass": "The change is ready to merge."},
            ),
        )

        payload = _normalize_payload(_config_to_payload(config))
        restored = _payload_to_config(payload)

        assert payload["produces_event_map"] == config.produces.event_type_map
        assert restored.produces.event_type_map == config.produces.event_type_map
        assert restored.produces.when == {"pass": "The change is ready to merge."}


class TestSavePersonaDocument:
    async def test_a_payload_whose_document_is_not_a_mapping_is_refused(self):
        registry, pool = _make_registry()

        with pytest.raises(PersonaDocumentError, match="document must be a mapping"):
            await registry.save_persona("user-1", {"name": "odd", "document": ["name", "odd"]})

        pool.execute.assert_not_awaited()

    async def test_a_document_is_stored_whole_and_read_back_as_written(self):
        registry, pool = _make_registry()
        definition = {
            "name": "checker",
            "description": "Checks things.",
            "produces": {
                "event_type_map": {"ok": "checker.ok"},
                "when": {"ok": "Everything checks out."},
            },
        }

        await registry.save_persona_document("user-1", persona_definition_from_mapping(definition))

        stored = json.loads(pool.execute.await_args.args[3])
        assert json.loads(stored["document_json"]) == definition
        assert stored["summary"] == "Checks things."
        pool.fetch.return_value = [_mock_row(name="checker", config_json=stored)]
        view = await registry.get_persona("user-1", "checker")
        assert view is not None
        assert view.config.produces.when == {"ok": "Everything checks out."}
        portable = await registry.get_current_portable_persona("user-1", "checker")
        assert portable is not None
        assert portable.definition == definition

    async def test_the_document_keeps_the_order_its_author_wrote(self):
        registry, pool = _make_registry()
        definition = {
            "name": "checker",
            "produces": {
                "event_type_map": {"pass": "c.pass", "needs_changes": "c.nc", "fail": "c.f"}
            },
        }
        await registry.save_persona_document("user-1", persona_definition_from_mapping(definition))
        stored = json.loads(pool.execute.await_args.args[3])
        # Postgres jsonb sorts object keys; the stored document is text, so it does not.
        reordered = json.loads(json.dumps(stored, sort_keys=True))
        pool.fetch.return_value = [_mock_row(name="checker", config_json=reordered)]

        portable = await registry.get_current_portable_persona("user-1", "checker")

        assert portable is not None
        assert list(portable.definition["produces"]["event_type_map"]) == [
            "pass",
            "needs_changes",
            "fail",
        ]

    @pytest.mark.parametrize("stored", [{"name": "x"}, "[1, 2]"])
    async def test_a_stored_document_that_is_not_json_text_of_a_mapping_is_refused(self, stored):
        registry, pool = _make_registry()
        payload = {**_mock_row()["config_json"], "name": "x", "document_json": stored}
        pool.fetch.return_value = [_mock_row(name="x", config_json=payload)]

        with pytest.raises(PersonaDocumentError, match="Stored persona 'x'"):
            await registry.get_persona("user-1", "x")
