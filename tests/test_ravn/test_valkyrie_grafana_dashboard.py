"""Structural checks for the importable Valkyrie runtime Grafana dashboard."""

from __future__ import annotations

import json
from pathlib import Path

from ravn import memory_telemetry


def test_runtime_dashboard_covers_judgment_and_causal_dependencies() -> None:
    dashboard_path = (
        Path(__file__).parents[2] / "docs" / "operator" / "grafana" / "valkyrie-runtime.json"
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
    assert "ravn_trace_boundaries" in queries
    assert "ravn_trace_relationship" in queries
    assert 'ravn_runtime_component="resident"' in queries
    assert "span.ravn.task.id" in queries


def _dashboard_queries() -> str:
    dashboard_path = (
        Path(__file__).parents[2] / "docs" / "operator" / "grafana" / "valkyrie-runtime.json"
    )
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    return "\n".join(
        str(target.get("expr") or target.get("query") or "")
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )


def test_runtime_dashboard_covers_every_agent_memory_metric() -> None:
    """Each metric ``ravn.memory_telemetry`` emits must be plotted somewhere.

    The three memory surfaces previously emitted nothing at all, so an empty
    prefetch was indistinguishable from a healthy one. Pinning the emitted
    names against the dashboard keeps a new metric from landing unplotted, and
    keeps a renamed one from silently blanking a panel.
    """
    queries = _dashboard_queries()

    for metric in (
        memory_telemetry.OPERATIONS,
        memory_telemetry.CANDIDATES,
        memory_telemetry.ADMITTED,
        memory_telemetry.RELEVANCE_SCORE,
        memory_telemetry.CANDIDATE_AGE_DAYS,
        memory_telemetry.INJECTED_CHARS,
        memory_telemetry.CORPUS_EPISODES,
        memory_telemetry.CORPUS_EMBEDDING_COVERAGE,
        memory_telemetry.CORPUS_INDEX_COVERAGE,
        memory_telemetry.RESIDENT_STATE_OPERATIONS,
        memory_telemetry.MIMIR_OPERATIONS,
    ):
        assert metric.replace(".", "_") in queries, f"{metric} is emitted but never plotted"


def test_runtime_dashboard_plots_the_admission_funnel_together() -> None:
    """Candidates and admitted must share a panel.

    Their ratio is the diagnostic that separates "nothing stored" from "the
    gate discarded good candidates"; on separate panels the comparison is lost.
    """
    dashboard_path = (
        Path(__file__).parents[2] / "docs" / "operator" / "grafana" / "valkyrie-runtime.json"
    )
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    funnel_panels = [
        panel
        for panel in dashboard["panels"]
        if all(
            any(name in str(target.get("expr", "")) for target in panel.get("targets", []))
            for name in ("ravn_memory_candidates", "ravn_memory_admitted")
        )
        and panel.get("targets")
    ]
    assert funnel_panels, "no panel plots candidates and admitted together"
