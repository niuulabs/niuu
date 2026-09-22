"""Direct unit coverage for the deterministic workflow evidence gate helpers.

``tests/test_niuu/test_evidence.py`` exercises ``evaluate_workflow_evidence``'s
happy path and its evidence-shape rejection; ``tests/test_skuld/
test_workflow_evidence_gate.py`` exercises ``validate_evidence_gate_nodes``
indirectly through the Skuld broker's own valid fixture graph. This file
covers the policy-shape rejection and every ``validate_evidence_gate_nodes``
branch directly against small graphs.
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from niuu.domain.workflow_evidence import evaluate_workflow_evidence, validate_evidence_gate_nodes


def _policy() -> dict:
    return {"require_checks": True, "check_producers": ["ci"]}


def _valid_graph() -> dict:
    return {
        "nodes": [
            {"id": "trigger", "kind": "trigger"},
            {
                "id": "verify",
                "kind": "gate",
                "mode": "evidence",
                "artifact": {"kind": "document", "id": "policy/doc"},
                "evidencePolicy": _policy(),
                "approvalEvent": "doc.accepted",
                "changesRequestedEvent": "doc.rejected",
            },
            {"id": "done", "kind": "end"},
            {"id": "repair", "kind": "stage"},
        ],
        "edges": [
            {
                "id": "in",
                "source": "trigger",
                "target": "verify",
                "label": "doc.ready -> doc.ready",
            },
            {
                "id": "accept",
                "source": "verify",
                "target": "done",
                "label": "doc.accepted -> doc.accepted",
            },
            {
                "id": "reject",
                "source": "verify",
                "target": "repair",
                "label": "doc.rejected -> doc.rejected",
            },
        ],
    }


# ---------------------------------------------------------------------------
# evaluate_workflow_evidence
# ---------------------------------------------------------------------------


def test_evaluate_workflow_evidence_rejects_non_dict_evidence() -> None:
    with pytest.raises(ValueError, match="evidence object"):
        evaluate_workflow_evidence(Mock(), evidence=["not", "a", "dict"], policy={})


def test_evaluate_workflow_evidence_rejects_non_dict_policy() -> None:
    with pytest.raises(ValueError, match="evidence policy"):
        evaluate_workflow_evidence(Mock(), evidence={}, policy="not-a-dict")


def test_evaluate_workflow_evidence_delegates_to_verifier() -> None:
    verifier = Mock()
    verifier.validate.return_value = "report"
    evidence = {"a": 1}
    policy = {"b": 2}

    result = evaluate_workflow_evidence(verifier, evidence=evidence, policy=policy)

    assert result == "report"
    verifier.validate.assert_called_once_with(evidence, policy)


# ---------------------------------------------------------------------------
# validate_evidence_gate_nodes
# ---------------------------------------------------------------------------


def test_validate_evidence_gate_nodes_accepts_valid_graph() -> None:
    validate_evidence_gate_nodes(_valid_graph())


def test_validate_evidence_gate_nodes_skips_non_dict_nodes() -> None:
    graph = _valid_graph()
    graph["nodes"].append("not-a-node")
    validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_rejects_evidence_policy_without_evidence_mode() -> None:
    graph = _valid_graph()
    graph["nodes"].append({"id": "stray", "kind": "gate", "evidencePolicy": _policy()})
    with pytest.raises(ValueError, match="evidencePolicy requires a gate with mode evidence"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_requires_gate_kind() -> None:
    graph = _valid_graph()
    graph["nodes"][1]["kind"] = "stage"
    with pytest.raises(ValueError, match="Evidence mode requires a gate node"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_rejects_missing_artifact() -> None:
    graph = _valid_graph()
    graph["nodes"][1]["artifact"] = {"kind": "document"}
    with pytest.raises(ValueError, match="artifact with non-empty kind and id"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_rejects_blank_artifact_values() -> None:
    graph = _valid_graph()
    graph["nodes"][1]["artifact"] = {"kind": "document", "id": "   "}
    with pytest.raises(ValueError, match="artifact with non-empty kind and id"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_requires_single_incoming_edge() -> None:
    graph = _valid_graph()
    graph["edges"].append(
        {"id": "extra-in", "source": "repair", "target": "verify", "label": "retry -> retry"}
    )
    with pytest.raises(ValueError, match="exactly one incoming evidence bundle event"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_requires_approval_and_rejection_events() -> None:
    graph = _valid_graph()
    graph["nodes"][1]["changesRequestedEvent"] = ""
    with pytest.raises(ValueError, match="approvalEvent and changesRequestedEvent"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_requires_distinct_events() -> None:
    graph = _valid_graph()
    graph["nodes"][1]["changesRequestedEvent"] = "doc.accepted"
    with pytest.raises(ValueError, match="distinct and unique to each gate"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_rejects_incoming_event_reused_as_output() -> None:
    graph = _valid_graph()
    graph["edges"][0]["label"] = "doc.accepted -> doc.accepted"
    with pytest.raises(ValueError, match="distinct incoming evidence event"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_rejects_incoming_event_named_complete() -> None:
    graph = _valid_graph()
    graph["edges"][0]["label"] = "complete -> complete"
    with pytest.raises(ValueError, match="distinct incoming evidence event"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_requires_both_output_events_routed() -> None:
    graph = _valid_graph()
    graph["edges"] = [graph["edges"][0], graph["edges"][1]]  # drop the rejection edge
    with pytest.raises(ValueError, match="route both approval and rejection events"):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_rejects_output_event_originating_elsewhere() -> None:
    graph = _valid_graph()
    graph["nodes"].append({"id": "impostor", "kind": "stage"})
    graph["edges"].append(
        {
            "id": "leak",
            "source": "impostor",
            "target": "done",
            "label": "doc.accepted -> doc.accepted",
        }
    )
    with pytest.raises(
        ValueError, match="Evidence output events may only originate at their evidence gate"
    ):
        validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_ignores_unrelated_edge_labels() -> None:
    graph = _valid_graph()
    graph["edges"].append(
        {"id": "unrelated", "source": "repair", "target": "done", "label": "resume -> resume"}
    )
    validate_evidence_gate_nodes(graph)


def test_validate_evidence_gate_nodes_handles_empty_graph() -> None:
    validate_evidence_gate_nodes({"nodes": [], "edges": []})


def test_validate_evidence_gate_nodes_does_not_mutate_input_graph() -> None:
    graph = _valid_graph()
    snapshot = deepcopy(graph)
    validate_evidence_gate_nodes(graph)
    assert graph == snapshot
