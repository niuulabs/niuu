"""Campaign stage progress derived from a pinned workflow graph.

A campaign's ``stage_state`` is a projection of evidence onto the stages its
workflow graph declares. Which evidence proves a given stage finished is
surface-specific (research reads Mímir artifacts and the durable fan-out
ledger); what that evidence *implies* is not, and lives here:

* The graph's edges order the stages. A stage runs on an event some upstream
  stage produced, so a stage that finished proves every stage upstream of it
  finished too. Progress is therefore closed upstream, which is what keeps a
  later stage from ever showing complete while an earlier one is still open.
* A stage failure reported by the runtime names a workflow node or a persona;
  :func:`stage_for_failure` attributes it to the stage that owns it, so the
  campaign can say *which* stage failed and why.

Nothing here knows what any particular workflow is for.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from ting.domain.models import CampaignStageState, WorkflowCampaign, WorkflowCampaignStatus

#: Campaign metadata keys a stage failure is recorded under. ``failure_error``
#: predates stage attribution and is what A2A and campaign events already read.
FAILURE_ERROR_KEY = "failure_error"
FAILURE_STAGE_KEY = "failure_stage_id"
#: Failure details copied verbatim from the runtime's activity metadata.
_FAILURE_DETAIL_KEYS = (
    "failure_persona",
    "failure_workflow_node_id",
    "failure_task_id",
    "failure_kind",
)


@dataclass(frozen=True)
class WorkflowStage:
    """One ``stage`` node of a workflow graph, with what the graph says about it."""

    id: str
    label: str
    persona_ids: tuple[str, ...] = ()
    #: Event types the stage emits: its outgoing edges' source events plus the
    #: canonical and verdict events its personas declare in ``produces``.
    produced_events: tuple[str, ...] = ()
    #: Mímir paths (``{slug}`` templated) the stage node declares it writes.
    artifact_paths: tuple[str, ...] = ()
    #: ``subworkflow`` nodes this stage dispatches (direct successors).
    dispatches_subworkflows: tuple[str, ...] = ()
    #: Stages that must have finished before this one can run.
    upstream: frozenset[str] = frozenset()

    @property
    def tokens(self) -> tuple[str, ...]:
        """Stable identifiers for the stage — never its display label."""
        return (self.id, *self.persona_ids, *self.produced_events)


def workflow_stages(snapshot: Mapping[str, Any] | None) -> list[WorkflowStage]:
    """Return the snapshot's stage nodes, in authored order, with graph facts."""
    graph = snapshot.get("graph") if isinstance(snapshot, Mapping) else None
    if not isinstance(graph, Mapping):
        return []
    nodes = [node for node in graph.get("nodes") or [] if isinstance(node, Mapping)]
    kinds = {str(node.get("id") or ""): str(node.get("kind") or "") for node in nodes}
    edges = _forward_edges(nodes, graph.get("edges") or [])
    stage_nodes = [node for node in nodes if node.get("kind") == "stage"]
    stage_ids = [str(node.get("id") or "") for node in stage_nodes]
    upstream = _upstream_stages(stage_ids, kinds, edges)
    produces = _persona_produced_events(snapshot)

    stages: list[WorkflowStage] = []
    for node, stage_id in zip(stage_nodes, stage_ids, strict=True):
        persona_ids = _stage_persona_ids(node)
        outgoing = [
            (source, target, event) for source, target, event in edges if source == stage_id
        ]
        events = [event for _, _, event in outgoing if event]
        for persona_id in persona_ids:
            events.extend(produces.get(persona_id, ()))
        stages.append(
            WorkflowStage(
                id=stage_id,
                label=str(node.get("label") or stage_id or "Stage"),
                persona_ids=persona_ids,
                produced_events=tuple(dict.fromkeys(events)),
                artifact_paths=_string_list(
                    node.get("artifactPaths") or node.get("artifact_paths")
                ),
                dispatches_subworkflows=tuple(
                    dict.fromkeys(
                        target for _, target, _ in outgoing if kinds.get(target) == "subworkflow"
                    )
                ),
                upstream=upstream.get(stage_id, frozenset()),
            )
        )
    return stages


def close_upstream(stages: Iterable[WorkflowStage], completed: Iterable[str]) -> set[str]:
    """Return ``completed`` plus every stage upstream of a completed stage."""
    by_id = {stage.id: stage for stage in stages}
    closed = {stage_id for stage_id in completed if stage_id in by_id}
    for stage_id in list(closed):
        closed.update(by_id[stage_id].upstream)
    return closed


def render_stage_state(
    stages: list[WorkflowStage],
    completed: Iterable[str],
    *,
    status: WorkflowCampaignStatus,
    previous: list[CampaignStageState],
    now: datetime,
    failed_stage_id: str | None = None,
    failure_reason: str | None = None,
) -> list[CampaignStageState]:
    """Project stage completion onto ``CampaignStageState`` rows.

    Completion is closed upstream first. The stage the campaign is currently
    on — the one a failure names, else the first unfinished one — carries the
    campaign's own blocked/failed status.
    """
    done = close_upstream(stages, completed)
    previous_map = {stage.stage_id: stage for stage in previous}
    derived: list[CampaignStageState] = []
    for stage in stages:
        prior = previous_map.get(stage.id)
        if stage.id in done:
            derived.append(
                CampaignStageState(
                    stage_id=stage.id,
                    label=stage.label,
                    status="complete",
                    started_at=prior.started_at if prior else None,
                    completed_at=(prior.completed_at if prior and prior.completed_at else now),
                    reason=_carried_reason(prior),
                )
            )
            continue
        derived.append(
            CampaignStageState(
                stage_id=stage.id,
                label=stage.label,
                status="pending",
                started_at=prior.started_at if prior else None,
                completed_at=None,
                reason=_carried_reason(prior),
            )
        )

    current = _current_stage_index(derived, status=status, failed_stage_id=failed_stage_id)
    if current is None:
        return derived
    row = derived[current]
    current_status = "active"
    reason = row.reason
    if status == WorkflowCampaignStatus.BLOCKED:
        current_status = "blocked"
    elif status == WorkflowCampaignStatus.FAILED:
        current_status = "failed"
        reason = failure_reason or reason
    derived[current] = replace(
        row,
        status=current_status,
        started_at=row.started_at or now,
        reason=reason,
    )
    return derived


def stage_for_failure(
    snapshot: Mapping[str, Any] | None,
    *,
    workflow_node_id: str = "",
    persona: str = "",
) -> str | None:
    """Attribute a runtime failure to the stage that owns it.

    The failing task's workflow node is authoritative when it names a stage;
    otherwise the stage whose members include the failing persona.
    """
    stages = workflow_stages(snapshot)
    node_id = workflow_node_id.strip()
    if node_id and any(stage.id == node_id for stage in stages):
        return node_id
    name = persona.strip()
    if name:
        for stage in stages:
            if name in stage.persona_ids:
                return stage.id
    return None


def failure_metadata(
    snapshot: Mapping[str, Any] | None,
    activity_metadata: Mapping[str, Any],
    *,
    error: str,
) -> dict[str, str]:
    """Campaign metadata recording a runtime failure: the error and its stage."""
    recorded = {FAILURE_ERROR_KEY: error} if error else {}
    for key in _FAILURE_DETAIL_KEYS:
        value = str(activity_metadata.get(key) or "").strip()
        if value:
            recorded[key] = value
    stage_id = stage_for_failure(
        snapshot,
        workflow_node_id=str(activity_metadata.get("failure_workflow_node_id") or ""),
        persona=str(activity_metadata.get("failure_persona") or ""),
    )
    if stage_id:
        recorded[FAILURE_STAGE_KEY] = stage_id
    return recorded


def mark_stage_failed(
    stage_state: list[CampaignStageState],
    stage_id: str | None,
    *,
    reason: str,
    now: datetime,
) -> list[CampaignStageState]:
    """Mark one stage failed in place of whichever stage was current.

    Only the stage attribution changes: complete stages stay complete, and a
    stage that was merely active or blocked becomes pending again, so exactly
    one stage carries the failure. Unknown stages leave the state unchanged.
    """
    if not stage_id or not any(
        stage.stage_id == stage_id and stage.status != "complete" for stage in stage_state
    ):
        return stage_state
    updated: list[CampaignStageState] = []
    for stage in stage_state:
        if stage.stage_id == stage_id:
            updated.append(
                replace(
                    stage,
                    status="failed",
                    started_at=stage.started_at or now,
                    reason=reason or stage.reason,
                )
            )
        elif stage.status in {"active", "blocked", "failed"}:
            updated.append(replace(stage, status="pending"))
        else:
            updated.append(stage)
    return updated


def record_runtime_failure(
    campaign: WorkflowCampaign,
    activity_metadata: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any], list[CampaignStageState], str | None]:
    """Record a runtime-reported failure on a campaign: the error and its stage.

    Returns the campaign's next ``(metadata, stage_state, active_stage_id)``.
    The stage is attributed from the failing task's workflow node or persona;
    when neither names a stage, only the metadata changes.
    """
    error = str(activity_metadata.get("error") or activity_metadata.get("message") or "").strip()
    recorded = failure_metadata(
        campaign.workflow_snapshot,
        activity_metadata,
        error=error or "Workflow runtime reported an error",
    )
    stage_id = recorded.get(FAILURE_STAGE_KEY)
    stage_state = mark_stage_failed(
        campaign.stage_state,
        stage_id,
        reason=recorded[FAILURE_ERROR_KEY],
        now=now,
    )
    active_stage_id = stage_id if stage_state is not campaign.stage_state else None
    return (
        {**campaign.metadata, **recorded},
        stage_state,
        active_stage_id or campaign.active_stage_id,
    )


def _current_stage_index(
    derived: list[CampaignStageState],
    *,
    status: WorkflowCampaignStatus,
    failed_stage_id: str | None,
) -> int | None:
    if status == WorkflowCampaignStatus.FAILED and failed_stage_id:
        for index, row in enumerate(derived):
            if row.stage_id == failed_stage_id and row.status != "complete":
                return index
    for index, row in enumerate(derived):
        if row.status != "complete":
            return index
    return None


def _carried_reason(prior: CampaignStageState | None) -> str | None:
    """Keep a pending stage's reason unless it only explained a past failure."""
    if prior is None or prior.status == "failed":
        return None
    return prior.reason


def _stage_persona_ids(node: Mapping[str, Any]) -> tuple[str, ...]:
    persona_ids: list[str] = []
    for member in node.get("stageMembers") or []:
        if isinstance(member, Mapping):
            persona_id = str(member.get("personaId") or "").strip()
            if persona_id:
                persona_ids.append(persona_id)
    for persona_id in node.get("personaIds") or []:
        if isinstance(persona_id, str) and persona_id.strip():
            persona_ids.append(persona_id.strip())
    return tuple(dict.fromkeys(persona_ids))


def _persona_produced_events(snapshot: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Map persona alias to the events its pinned definition declares it produces."""
    definitions = snapshot.get("persona_definitions")
    if not isinstance(definitions, Mapping):
        return {}
    produced: dict[str, tuple[str, ...]] = {}
    for alias, document in definitions.items():
        if not isinstance(document, Mapping):
            continue
        definition = document.get("definition", document)
        produces = definition.get("produces") if isinstance(definition, Mapping) else None
        if not isinstance(produces, Mapping):
            continue
        events = [str(produces.get("event_type") or "").strip()]
        event_map = produces.get("event_type_map")
        if isinstance(event_map, Mapping):
            events.extend(str(value or "").strip() for value in event_map.values())
        produced[str(alias)] = tuple(dict.fromkeys(event for event in events if event))
    return produced


def _forward_edges(
    nodes: list[Mapping[str, Any]],
    raw_edges: Iterable[Any],
) -> list[tuple[str, str, str]]:
    """Return ``(source, target, source_event)`` edges with loop-back edges removed.

    A loop (a stage re-entered on a retry or revision event) must not make a
    downstream stage "upstream" of the stage that loops back to it, so edges
    closing a cycle — found by DFS from the graph's entry nodes — are dropped.
    """
    node_ids = [str(node.get("id") or "") for node in nodes]
    edges: list[tuple[str, str, str]] = []
    for edge in raw_edges:
        if not isinstance(edge, Mapping):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target:
            edges.append((source, target, _source_event(edge)))
    successors: dict[str, list[str]] = {}
    has_incoming: set[str] = set()
    for source, target, _ in edges:
        successors.setdefault(source, []).append(target)
        has_incoming.add(target)

    back_edges: set[tuple[str, str]] = set()
    visited: set[str] = set()
    roots = [node_id for node_id in node_ids if node_id not in has_incoming]
    for root in [*roots, *node_ids, *successors]:
        if root in visited:
            continue
        on_stack: set[str] = set()
        stack: list[tuple[str, int]] = [(root, 0)]
        visited.add(root)
        on_stack.add(root)
        while stack:
            node_id, index = stack[-1]
            children = successors.get(node_id, [])
            if index >= len(children):
                stack.pop()
                on_stack.discard(node_id)
                continue
            stack[-1] = (node_id, index + 1)
            child = children[index]
            if child in on_stack:
                back_edges.add((node_id, child))
            elif child not in visited:
                visited.add(child)
                on_stack.add(child)
                stack.append((child, 0))
    return [edge for edge in edges if (edge[0], edge[1]) not in back_edges]


def _upstream_stages(
    stage_ids: list[str],
    kinds: Mapping[str, str],
    edges: list[tuple[str, str, str]],
) -> dict[str, frozenset[str]]:
    stage_set = set(stage_ids)
    predecessors: dict[str, set[str]] = {}
    for source, target, _ in edges:
        predecessors.setdefault(target, set()).add(source)
    if not any(node in stage_set for edge in edges for node in edge[:2]):
        # A graph with no edges between its stages still lists them in order.
        return {stage_id: frozenset(stage_ids[:index]) for index, stage_id in enumerate(stage_ids)}
    upstream: dict[str, frozenset[str]] = {}
    for stage_id in stage_ids:
        seen: set[str] = set()
        frontier = list(predecessors.get(stage_id, ()))
        while frontier:
            node_id = frontier.pop()
            if node_id in seen or node_id == stage_id:
                continue
            seen.add(node_id)
            frontier.extend(predecessors.get(node_id, ()))
        upstream[stage_id] = frozenset(node_id for node_id in seen if kinds.get(node_id) == "stage")
    return upstream


def _source_event(edge: Mapping[str, Any]) -> str:
    label = edge.get("label")
    if isinstance(label, str) and "->" in label:
        return label.split("->", 1)[0].strip()
    return ""


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if isinstance(item, str) and item.strip())
