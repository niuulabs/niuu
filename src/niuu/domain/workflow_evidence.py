"""Pure evaluation helper for deterministic workflow evidence gates."""

from __future__ import annotations

from niuu.domain.evidence import EvidencePolicy, EvidenceValidationReport
from niuu.ports.evidence import EvidenceGateVerifier


def evaluate_workflow_evidence(
    verifier: EvidenceGateVerifier,
    *,
    evidence: object,
    policy: object,
) -> EvidenceValidationReport:
    """Validate graph payloads without invoking a model or mutating workflow state."""
    if not isinstance(evidence, dict):
        raise ValueError("Evidence gate payload must contain an evidence object")
    if not isinstance(policy, dict):
        raise ValueError("Evidence gate configuration must contain an evidence policy")
    return verifier.validate(evidence, policy)


def validate_evidence_gate_nodes(graph: dict) -> None:
    """Reject unusable gates before import or runtime startup."""
    outputs: set[str] = set()
    edges = graph.get("edges", [])
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("mode") != "evidence":
            if "evidencePolicy" in node:
                raise ValueError("evidencePolicy requires a gate with mode evidence")
            continue
        if node.get("kind") != "gate":
            raise ValueError("Evidence mode requires a gate node")
        EvidencePolicy.model_validate(node.get("evidencePolicy"))
        artifact = node.get("artifact")
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"kind", "id"}
            or any(not isinstance(value, str) or not value.strip() for value in artifact.values())
        ):
            raise ValueError("Evidence gates require artifact with non-empty kind and id")
        node_id = node.get("id")
        incoming = [edge for edge in edges if edge.get("target") == node_id]
        if len(incoming) != 1 or not str(incoming[0].get("label") or "").strip():
            raise ValueError("Evidence gates require exactly one incoming evidence bundle event")
        events = [node.get("approvalEvent"), node.get("changesRequestedEvent")]
        if any(not isinstance(event, str) or not event.strip() for event in events):
            raise ValueError(
                "Evidence gates require explicit approvalEvent and changesRequestedEvent"
            )
        if events[0] == events[1] or outputs.intersection(events):
            raise ValueError("Evidence gate output events must be distinct and unique to each gate")
        incoming_event = str(incoming[0]["label"]).split("->", 1)[0].strip()
        if incoming_event in events or incoming_event == "complete":
            raise ValueError("Evidence gates require a distinct incoming evidence event")
        outgoing = {
            str(edge.get("label") or "").split("->", 1)[0].strip()
            for edge in edges
            if edge.get("source") == node_id
        }
        if not set(events).issubset(outgoing):
            raise ValueError("Evidence gate edges must route both approval and rejection events")
        outputs.update(events)
    for edge in edges:
        event = str(edge.get("label") or "").split("->", 1)[0].strip()
        if event not in outputs:
            continue
        source = next(
            (node for node in graph.get("nodes", []) if node.get("id") == edge.get("source")), {}
        )
        if source.get("mode") != "evidence" or event not in {
            source.get("approvalEvent"),
            source.get("changesRequestedEvent"),
        }:
            raise ValueError("Evidence output events may only originate at their evidence gate")
