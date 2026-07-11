"""Review signals and compatibility characterization for large-module extraction."""

from __future__ import annotations

from niuu.app import RootServer
from ravn.cli.commands import (
    _build_workflow_capability_sources as command_capability_sources,
)
from ravn.cli.runtime_builders import (
    _build_workflow_capability_sources as runtime_capability_sources,
)
from ravn.cli.tool_builders import (
    _build_workflow_capability_sources as tool_capability_sources,
)
from scripts.module_review import DEFAULT_TARGETS, analyze
from skuld.broker import Broker
from volundr.app_shell import build_app_shell


def test_module_review_tracks_all_hardening_hotspots() -> None:
    expected = {
        "src/skuld/broker.py",
        "src/ravn/cli/commands.py",
        "src/ravn/api/valkyries.py",
        "src/volundr/main.py",
        "src/niuu/app.py",
        "src/niuu/session_proxy.py",
        "src/volundr/composition_builders.py",
        "web-next/packages/plugin-volundr/src/ui/LaunchWizard.tsx",
        "web-next/packages/plugin-volundr/src/ui/useLaunchWizard.ts",
        "web-next/packages/plugin-ting/src/ui/ResearchCampaignPage.tsx",
    }
    assert set(DEFAULT_TARGETS) == expected
    signals = [analyze(path) for path in DEFAULT_TARGETS]
    assert all(signal.lines > 0 for signal in signals)


def test_cli_builder_compatibility_export_points_to_runtime_builder() -> None:
    assert command_capability_sources is runtime_capability_sources
    assert tool_capability_sources is runtime_capability_sources


def test_composition_builders_remain_visible_through_public_roots() -> None:
    assert RootServer.__module__ == "niuu.root_server"
    assert build_app_shell.__module__ == "volundr.app_shell"


def test_broker_preserves_lifecycle_methods_through_focused_mixins() -> None:
    transport_methods = {
        "_build_transport_kwargs",
        "_create_transport",
        "_auto_start_transport",
        "startup",
        "shutdown",
    }
    websocket_methods = {
        "_authorize_websocket",
        "_update_jwt_from_websocket",
        "_safe_browser_send_json",
        "handle_websocket",
        "handle_cli_websocket",
        "handle_ravn_websocket",
    }
    for name in transport_methods | websocket_methods:
        assert callable(getattr(Broker, name))
    assert all(
        getattr(Broker, name).__module__ == "skuld.transport_lifecycle"
        for name in transport_methods
    )
    assert all(
        getattr(Broker, name).__module__ == "skuld.websocket_lifecycle"
        for name in websocket_methods
    )
