"""Daemon wiring that attaches build_tool to signal investigations (NIU-1051)."""

from __future__ import annotations

from typing import Any

from ravn.adapters.tools.build_tool import attach_build_tool
from ravn.cli.commands import _attach_signal_build_tool, _build_tool_build_backend
from ravn.config import Settings


class _FakeAgent:
    """Minimal RavnAgent stand-in that records registered tools."""

    def __init__(self) -> None:
        self.registered: list[tuple[Any, bool]] = []

    def register_tool(self, tool: Any, replace: bool = False) -> None:
        self.registered.append((tool, replace))


def test_attach_signal_build_tool_skips_non_signal_tasks(tmp_path) -> None:
    agent = _FakeAgent()
    out = _attach_signal_build_tool(
        agent, tmp_path, triggered_by="thread:abc", settings=Settings(), publisher=None
    )
    assert out is agent
    assert agent.registered == []


def test_attach_signal_build_tool_registers_build_tool_for_signals(tmp_path) -> None:
    agent = _FakeAgent()
    out = _attach_signal_build_tool(
        agent,
        tmp_path,
        triggered_by="signal:signal.host.event",
        settings=Settings(),
        publisher=None,
    )
    assert out is agent
    assert any(getattr(tool, "name", "") == "build_tool" for tool, _ in agent.registered)


def test_build_tool_build_backend_is_inline_by_default_and_dynamic_when_configured() -> None:
    # Empty adapter -> inline authoring (None backend).
    assert _build_tool_build_backend(Settings()) is None

    configured = Settings(
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.ForgeSessionToolBuildBackend",
            "tool_build_kwargs": {"base_url": "http://forge", "pat_env": "UNSET_PAT"},
        }
    )
    backend = _build_tool_build_backend(configured)
    assert backend is not None
    assert backend.name == "forge_session"


def test_investigation_prompt_handles_missing_and_failing_providers(tmp_path) -> None:
    def _boom() -> str:
        raise RuntimeError("provider exploded")

    no_provider = attach_build_tool(_FakeAgent(), tools_dir=tmp_path / "a")
    raising = attach_build_tool(
        _FakeAgent(), tools_dir=tmp_path / "b", investigation_context=_boom
    )
    # Provenance must never break a build: both degrade to an empty prompt.
    assert no_provider._investigation_prompt() == ""
    assert raising._investigation_prompt() == ""
