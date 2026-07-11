"""Pure workflow graph, gate, and terminal-outcome behavior for Skuld."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_PASSING_VERDICTS = {
    "approve",
    "approved",
    "clean",
    "complete",
    "completed",
    "ok",
    "pass",
    "passed",
    "success",
    "succeeded",
}
_FAILING_VERDICTS = {
    "blocked",
    "changes_requested",
    "error",
    "errors",
    "fail",
    "failed",
    "needs_changes",
    "reject",
    "rejected",
}


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _split_workflow_edge_label(label: object) -> tuple[str, str]:
    if not isinstance(label, str):
        return "", ""
    parts = label.split("->", 1)
    if len(parts) != 2:
        stripped = label.strip()
        return stripped, stripped
    return parts[0].strip(), parts[1].strip()


@dataclass(frozen=True)
class WorkflowTerminalNode:
    node_id: str
    label: str
    event_types: list[str]
    join_mode: str
    completion_event_type: str
    require_git_commit: bool = False
    require_git_push: bool = False


@dataclass(frozen=True)
class WorkflowGateNode:
    node_id: str
    label: str
    condition: str
    event_types: list[str]
    mode: str = "human_approval"
    approval_event_type: str = "gate.approved"
    changes_requested_event_type: str = "gate.changes_requested"
    pending_behavior: str = "help_needed"
    instructions: str = ""
    auto_forward_after: str = "30m"


@dataclass
class WorkflowGateState:
    id: str
    node_id: str
    activation_id: str
    label: str
    condition: str
    status: str
    mode: str
    pending_behavior: str
    instructions: str
    auto_forward_after: str
    requested_at: str
    updated_at: str
    triggered_by_event_type: str
    approval_event_type: str
    changes_requested_event_type: str
    attempt: int = 1
    decision: str | None = None
    notes: str = ""
    source: str = "workflow"
    summary: str = ""


def _workflow_terminal_nodes(graph: dict[str, Any] | None) -> list[WorkflowTerminalNode]:
    if not isinstance(graph, dict):
        return []

    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    terminal_nodes: list[WorkflowTerminalNode] = []

    for node in nodes:
        if str(node.get("kind") or "") != "end":
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        event_types = _dedupe_preserve_order(
            [
                source_event
                for edge in edges
                if str(edge.get("target") or "").strip() == node_id
                for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
                if source_event and source_event != "complete"
            ]
        )
        if not event_types:
            continue
        terminal_nodes.append(
            WorkflowTerminalNode(
                node_id=node_id,
                label=str(node.get("label") or node_id),
                event_types=event_types,
                join_mode=str(node.get("joinMode") or "all"),
                completion_event_type=str(node.get("completionEvent") or "ravn.task.completed"),
                require_git_commit=bool(
                    (node.get("completionRules") or {}).get("requireGitCommit")
                ),
                require_git_push=bool((node.get("completionRules") or {}).get("requireGitPush")),
            )
        )

    return terminal_nodes


def _workflow_gate_nodes(graph: dict[str, Any] | None) -> list[WorkflowGateNode]:
    if not isinstance(graph, dict):
        return []

    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    gate_nodes: list[WorkflowGateNode] = []

    for node in nodes:
        if str(node.get("kind") or "") != "gate":
            continue

        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue

        incoming_event_types = _dedupe_preserve_order(
            [
                source_event
                for edge in edges
                if str(edge.get("target") or "").strip() == node_id
                for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
                if source_event and source_event != "complete"
            ]
        )
        if not incoming_event_types:
            continue

        outgoing_event_types = _dedupe_preserve_order(
            [
                source_event
                for edge in edges
                if str(edge.get("source") or "").strip() == node_id
                for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
                if source_event and source_event != "complete"
            ]
        )
        explicit_approval_event_type = str(
            node.get("approvalEvent") or node.get("approval_event") or ""
        ).strip()
        explicit_changes_requested_event_type = str(
            node.get("changesRequestedEvent") or node.get("changes_requested_event") or ""
        ).strip()
        approval_event_type = explicit_approval_event_type or next(
            (
                event_type
                for event_type in outgoing_event_types
                if "approved" in event_type or event_type.endswith(".approve")
            ),
            outgoing_event_types[0] if outgoing_event_types else "gate.approved",
        )
        changes_requested_event_type = explicit_changes_requested_event_type or next(
            (
                event_type
                for event_type in outgoing_event_types
                if "changes_requested" in event_type or "changes-requested" in event_type
            ),
            next(
                (
                    event_type
                    for event_type in outgoing_event_types
                    if "changes" in event_type or "rework" in event_type
                ),
                outgoing_event_types[1]
                if len(outgoing_event_types) > 1
                else "gate.changes_requested",
            ),
        )
        pending_behavior = (
            str(
                node.get("pendingBehavior") or node.get("pending_behavior") or "help_needed"
            ).strip()
            or "help_needed"
        )
        mode = str(node.get("mode") or "human_approval").strip() or "human_approval"
        instructions = str(node.get("instructions") or "").strip()

        gate_nodes.append(
            WorkflowGateNode(
                node_id=node_id,
                label=str(node.get("label") or node_id),
                condition=str(node.get("condition") or ""),
                event_types=incoming_event_types,
                mode=mode,
                approval_event_type=approval_event_type,
                changes_requested_event_type=changes_requested_event_type,
                pending_behavior=pending_behavior,
                instructions=instructions,
                auto_forward_after=str(node.get("autoForwardAfter") or "30m"),
            )
        )

    return gate_nodes


def _workflow_outcome_passed(payload: dict[str, Any]) -> bool:
    if not bool(payload.get("valid", True)):
        return False

    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict in _FAILING_VERDICTS:
        return False
    if verdict in _PASSING_VERDICTS:
        return True

    fields = payload.get("fields")
    if isinstance(fields, dict):
        approved = fields.get("approved")
        if approved is False:
            return False
        if approved is True:
            return True
        tests_passing = fields.get("tests_passing")
        if tests_passing is False:
            return False

    tests_passing = payload.get("tests_passing")
    if tests_passing is False:
        return False

    return True


def _workflow_join_satisfied(join_mode: str, outcomes: list[dict[str, Any]]) -> bool:
    if not outcomes:
        return False
    passed = [_workflow_outcome_passed(outcome) for outcome in outcomes]
    match join_mode:
        case "any":
            return any(passed)
        case "merge":
            return all(passed)
        case _:
            return all(passed)
    raise AssertionError("Unreachable _workflow_join_satisfied fallthrough")


def _merge_workflow_terminal_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: list[str] = []
    files_changed: list[str] = []
    seen_files: set[str] = set()
    tests: list[bool] = []
    scope_values: list[float] = []
    checks: list[dict[str, Any]] = []

    for outcome in outcomes:
        event_type = str(outcome.get("event_type") or "").strip()
        persona = str(outcome.get("persona") or "").strip()
        verdict = str(outcome.get("verdict") or "").strip()
        summary = str(outcome.get("summary") or "").strip()
        if summary:
            label = persona or event_type or "outcome"
            summaries.append(f"{label}: {summary}")

        raw_files = outcome.get("files_changed")
        if isinstance(raw_files, list):
            for file_path in raw_files:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.strip()
                if not normalized or normalized in seen_files:
                    continue
                seen_files.add(normalized)
                files_changed.append(normalized)

        candidate_tests = outcome.get("tests_passing")
        if isinstance(candidate_tests, bool):
            tests.append(candidate_tests)

        candidate_scope = outcome.get("scope_adherence")
        if isinstance(candidate_scope, (int, float)):
            scope_values.append(float(candidate_scope))

        checks.append(
            {
                "persona": persona,
                "event_type": event_type,
                "verdict": verdict,
                "summary": summary,
            }
        )

    merged: dict[str, Any] = {
        "verdict": "approve",
        "summary": " | ".join(summaries) if summaries else "Workflow checks passed",
        "checks": checks,
        "authoritative": True,
    }
    if files_changed:
        merged["files_changed"] = files_changed
    if tests:
        merged["tests_passing"] = all(tests)
    if scope_values:
        merged["scope_adherence"] = min(scope_values)
    return merged
