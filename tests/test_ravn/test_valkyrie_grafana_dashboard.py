"""Structural checks for the importable Valkyrie runtime Grafana dashboard."""

from __future__ import annotations

import json
from pathlib import Path


def test_runtime_dashboard_covers_judgment_and_causal_dependencies() -> None:
    dashboard_path = (
        Path(__file__).parents[2]
        / "docs"
        / "operator"
        / "grafana"
        / "valkyrie-runtime.json"
    )
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    panels = dashboard["panels"]
    panel_ids = [panel["id"] for panel in panels]
    queries = "\n".join(
        str(target.get("expr") or target.get("query") or "")
        for panel in panels
        for target in panel.get("targets", [])
    )

    assert dashboard["uid"] == "valkyrie-runtime-judgment"
    assert len(panel_ids) == len(set(panel_ids))
    assert {panel.get("type") for panel in panels} >= {"stat", "timeseries", "table"}
    trace_search_panel = next(panel for panel in panels if panel["id"] == 50)
    assert trace_search_panel["type"] == "table"
    a2a_trace_panel = next(panel for panel in panels if panel["id"] == 51)
    assert a2a_trace_panel["type"] == "table"
    assert "ravn.a2a" in a2a_trace_panel["targets"][0]["query"]
    assert "ravn.tool_build" in a2a_trace_panel["targets"][0]["query"]
    assert "ravn_valkyrie_judgments" in queries
    assert "ravn_signal_transport_messages" in queries
    assert "ravn_agent_tool_calls" in queries
    assert "ravn_learned_tool_installed" in queries
    assert "ravn_a2a_operations" in queries
    assert "ravn_tool_build_verifications" in queries
    assert "ravn_tool_build_reviews" in queries
    assert "ravn_tool_build_canary_operations" in queries
    assert "ravn_event_bus_operations" in queries
    assert "tempo_warnings_total" in queries
    assert "rootless_trace_flushed_to_wal" in queries
    assert "disconnected_trace_flushed_to_wal" in queries
    assert "span.ravn.task.id" in queries
