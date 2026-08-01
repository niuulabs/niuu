"""Daemon wiring that exposes build_tool through the persona capability surface."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import ravn.cli.commands as commands_mod
from ravn.adapters.realm.client import BuildGrant
from ravn.adapters.tools.build_tool import attach_build_tool
from ravn.cli.commands import (
    _attach_agent_build_tool,
    _build_tool_build_backend,
    _resolve_realm_build_config,
)
from ravn.config import Settings


@pytest.fixture(autouse=True)
def _fresh_realm_client_cache():
    """RealmClients are cached per auth config; tests must not share them."""
    commands_mod._REALM_CLIENT_CACHE.clear()
    yield
    commands_mod._REALM_CLIENT_CACHE.clear()


class _FakeAgent:
    """Minimal RavnAgent stand-in that records registered tools."""

    def __init__(self) -> None:
        self.registered: list[tuple[Any, bool]] = []

    def register_tool(self, tool: Any, replace: bool = False) -> None:
        self.registered.append((tool, replace))


def test_attach_agent_build_tool_skips_when_persona_disallows_it(tmp_path) -> None:
    agent = _FakeAgent()
    out = _attach_agent_build_tool(
        agent, tmp_path, enabled=False, settings=Settings(), publisher=None
    )
    assert out is agent
    assert agent.registered == []


def test_attach_agent_build_tool_registers_for_allowed_persona(tmp_path) -> None:
    agent = _FakeAgent()
    out = _attach_agent_build_tool(
        agent,
        tmp_path,
        enabled=True,
        settings=Settings(),
        publisher=None,
    )
    assert out is agent
    assert any(getattr(tool, "name", "") == "build_tool" for tool, _ in agent.registered)


def test_tool_mcp_attaches_build_tool_for_allowed_persona(tmp_path) -> None:
    settings = Settings(state_dir=str(tmp_path / "state"))
    persona = MagicMock(allowed_tools=["build_tool"])
    server = MagicMock()
    server.run_stdio = AsyncMock()

    with (
        patch.object(commands_mod, "Settings", return_value=settings),
        patch.object(commands_mod, "_configure_logging"),
        patch.object(commands_mod.ProjectConfig, "discover", return_value=MagicMock()),
        patch.object(commands_mod, "_resolve_profile", return_value=None),
        patch.object(commands_mod, "_resolve_persona", return_value=persona),
        patch.object(commands_mod, "_build_tool_mcp_tools", return_value=[]),
        patch.object(commands_mod, "_resolve_workspace", return_value=tmp_path),
        patch.object(commands_mod, "_attach_agent_build_tool") as attach,
        patch(
            "ravn.adapters.mcp.tool_port_server.ToolPortMcpServer",
            return_value=server,
        ),
    ):
        commands_mod.tool_mcp(config="", persona="ivaldi", profile="")

    attach.assert_called_once_with(
        server,
        tmp_path,
        enabled=True,
        settings=settings,
    )
    server.run_stdio.assert_awaited_once()


def _registered_build_tool(agent: _FakeAgent) -> Any:
    return next(tool for tool, _ in agent.registered if getattr(tool, "name", "") == "build_tool")


def test_attach_agent_build_tool_defaults_preserve_previous_policy_constants(tmp_path) -> None:
    # P5a: a config-less Settings() must wire the exact old constants —
    # 3 repair attempts and 0.74 flock confidence.
    agent = _FakeAgent()
    _attach_agent_build_tool(
        agent,
        tmp_path,
        enabled=True,
        settings=Settings(),
        publisher=None,
    )

    tool = _registered_build_tool(agent)
    assert tool._max_repair_attempts == 3
    assert tool._flock_confidence == 0.74


def test_attach_agent_build_tool_threads_configured_policy_values(tmp_path) -> None:
    agent = _FakeAgent()
    settings = Settings(
        resident_evolution={
            "build_repair_attempts": 7,
            "self_registered_tool_confidence": 0.9,
        }
    )
    _attach_agent_build_tool(
        agent,
        tmp_path,
        enabled=True,
        settings=settings,
        publisher=None,
    )

    tool = _registered_build_tool(agent)
    assert tool._max_repair_attempts == 7
    assert tool._flock_confidence == 0.9


def test_build_tool_build_backend_is_inline_by_default_and_dynamic_when_configured() -> None:
    # Empty adapter -> inline authoring (None backend).
    assert _build_tool_build_backend(Settings()) is None

    configured = Settings(
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.ForgeSessionToolBuildBackend",
            "tool_build_kwargs": {"base_url": "http://forge"},
        }
    )
    backend = _build_tool_build_backend(configured)
    assert backend is not None
    assert backend.name == "forge_session"


def test_build_tool_build_backend_injects_configured_workflow_selector() -> None:
    configured = Settings(
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.TingWorkflowToolBuildBackend",
            "tool_build_kwargs": {"base_url": "http://ting"},
            "tool_builder_workflow": {"tags": ["tool-builder"]},
        }
    )

    backend = _build_tool_build_backend(configured)

    assert backend is not None
    assert backend.name == "ting_workflow"
    assert backend._workflow_selector.tags == ["tool-builder"]


def test_build_tool_build_backend_applies_realm_selector_override() -> None:
    configured = Settings(
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.TingWorkflowToolBuildBackend",
            "tool_build_kwargs": {"base_url": "http://ting"},
            "tool_builder_workflow": {"tags": ["static-only"]},
        }
    )

    # A realm-resolved selector overrides the static tool_builder_workflow.
    backend = _build_tool_build_backend(configured, workflow_selector={"names": ["tool-builder"]})

    assert backend is not None
    assert backend._workflow_selector.names == ["tool-builder"]
    assert backend._workflow_selector.tags == []


def test_build_tool_build_backend_injects_a2a_activity_emitter() -> None:
    configured = Settings(
        gateway={
            "platform": {
                "a2a_push_callback_url": "https://ivaldi.example/a2a/push",
                "a2a_push_notification_token": "push-secret",
            }
        },
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.A2AToolBuildBackend",
            "tool_build_kwargs": {"card_url": "https://ting.example/.well-known/agent-card.json"},
        },
    )
    emitter = AsyncMock()

    backend = _build_tool_build_backend(configured, activity_emitter=emitter)

    assert backend is not None
    assert backend._activity_emitter is emitter
    assert backend._push_callback_url == "https://ivaldi.example/a2a/push"
    assert backend._push_notification_token == "push-secret"


# ---------------------------------------------------------------------------
# _resolve_realm_build_config — realm grant resolution + fallbacks
# ---------------------------------------------------------------------------


def _realm_settings(**overrides: Any) -> Settings:
    base = {
        "tool_build_adapter": "ravn.adapters.tool_build.TingWorkflowToolBuildBackend",
        "tool_build_kwargs": {"base_url": "http://volundr"},
    }
    base.update(overrides)
    return Settings(resident_evolution=base)


def test_resolve_realm_build_config_falls_back_when_no_realm_slug() -> None:
    settings = _realm_settings(autonomy_mode="guarded")

    resolved = _resolve_realm_build_config(settings)

    assert resolved.autonomy_mode == "guarded"
    assert resolved.workflow_selector is None


def test_resolve_realm_build_config_uses_grant_when_present() -> None:
    settings = _realm_settings(realm_slug="payments", autonomy_mode="guarded")
    grant = BuildGrant(level=5, limits={"workflow": "tool-builder"}, target="t")

    async def _resolve(_slug: str) -> BuildGrant:
        return grant

    with patch("ravn.adapters.realm.RealmClient") as fake_cls:
        fake_cls.return_value.resolve_build_grant = _resolve
        resolved = _resolve_realm_build_config(settings)

    # level 5 -> yolo; workflow "tool-builder" -> names selector.
    assert resolved.autonomy_mode == "yolo"
    assert resolved.workflow_selector == {"names": ["tool-builder"]}


def test_resolve_realm_build_config_uses_configured_trust_table() -> None:
    # P5a: the trust-level -> autonomy-mode table comes from config. Level 5
    # is yolo under the default table but only autonomous under this one.
    settings = _realm_settings(
        realm_slug="payments",
        autonomy_mode="guarded",
        trust_level_autonomy_table={"autonomous": 4, "yolo": 6},
    )
    grant = BuildGrant(level=5, limits={}, target="t")

    async def _resolve(_slug: str) -> BuildGrant:
        return grant

    with patch("ravn.adapters.realm.RealmClient") as fake_cls:
        fake_cls.return_value.resolve_build_grant = _resolve
        resolved = _resolve_realm_build_config(settings)

    assert resolved.autonomy_mode == "autonomous"


def test_resolve_realm_build_config_falls_back_when_no_grant() -> None:
    settings = _realm_settings(realm_slug="payments", autonomy_mode="autonomous")

    async def _resolve(_slug: str) -> None:
        return None

    with patch("ravn.adapters.realm.RealmClient") as fake_cls:
        fake_cls.return_value.resolve_build_grant = _resolve
        resolved = _resolve_realm_build_config(settings)

    assert resolved.autonomy_mode == "autonomous"
    assert resolved.workflow_selector is None


def test_resolve_realm_build_config_falls_back_on_realm_outage() -> None:
    settings = _realm_settings(realm_slug="payments", autonomy_mode="guarded")

    async def _boom(_slug: str) -> BuildGrant:
        raise RuntimeError("realm unreachable")

    with patch("ravn.adapters.realm.RealmClient") as fake_cls:
        fake_cls.return_value.resolve_build_grant = _boom
        resolved = _resolve_realm_build_config(settings)

    # A realm outage must not brick the resident: degrade to static config.
    assert resolved.autonomy_mode == "guarded"
    assert resolved.workflow_selector is None


def test_resolve_realm_build_config_falls_back_without_base_url() -> None:
    settings = Settings(resident_evolution={"realm_slug": "payments", "autonomy_mode": "guarded"})

    resolved = _resolve_realm_build_config(settings)

    # No realm_api_base_url and no tool_build base_url -> static config.
    assert resolved.autonomy_mode == "guarded"
    assert resolved.workflow_selector is None


def test_investigation_prompt_handles_missing_and_failing_providers(tmp_path) -> None:
    def _boom() -> str:
        raise RuntimeError("provider exploded")

    no_provider = attach_build_tool(_FakeAgent(), tools_dir=tmp_path / "a")
    raising = attach_build_tool(_FakeAgent(), tools_dir=tmp_path / "b", investigation_context=_boom)
    # Provenance must never break a build: both degrade to an empty prompt.
    assert no_provider._investigation_prompt() == ""
    assert raising._investigation_prompt() == ""
