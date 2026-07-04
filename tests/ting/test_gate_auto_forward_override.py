"""Tests for the launch-time gate autoForwardAfter override."""

from __future__ import annotations

from ting.api.workflows import WorkflowLaunchBody, _apply_gate_auto_forward_override


def _snapshot() -> dict:
    return {
        "workflow_id": "wf",
        "graph": {
            "nodes": [
                {"id": "t", "kind": "trigger"},
                {"id": "g1", "kind": "gate", "autoForwardAfter": "30m"},
                {"id": "s", "kind": "stage"},
                {"id": "g2", "kind": "gate", "autoForwardAfter": "30m"},
            ],
            "edges": [],
        },
    }


class TestGateAutoForwardOverride:
    def test_override_replaces_every_gate(self):
        patched = _apply_gate_auto_forward_override(_snapshot(), "24h")
        gates = [n for n in patched["graph"]["nodes"] if n["kind"] == "gate"]
        assert all(g["autoForwardAfter"] == "24h" for g in gates)

    def test_empty_value_disables_auto_forward(self):
        patched = _apply_gate_auto_forward_override(_snapshot(), "")
        gates = [n for n in patched["graph"]["nodes"] if n["kind"] == "gate"]
        assert all("autoForwardAfter" not in g for g in gates)

    def test_non_gate_nodes_untouched(self):
        patched = _apply_gate_auto_forward_override(_snapshot(), "24h")
        stage = next(n for n in patched["graph"]["nodes"] if n["kind"] == "stage")
        assert "autoForwardAfter" not in stage

    def test_original_snapshot_not_mutated(self):
        original = _snapshot()
        _apply_gate_auto_forward_override(original, "")
        gates = [n for n in original["graph"]["nodes"] if n["kind"] == "gate"]
        assert all(g["autoForwardAfter"] == "30m" for g in gates)


class TestLaunchBodyField:
    def test_alias_round_trip(self):
        body = WorkflowLaunchBody.model_validate({"prompt": "go", "gateAutoForwardAfter": ""})
        assert body.gate_auto_forward_after == ""

    def test_default_is_none(self):
        body = WorkflowLaunchBody.model_validate({"prompt": "go"})
        assert body.gate_auto_forward_after is None
