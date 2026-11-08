"""The workflow graph as the step engine reads it.

``compile_step_graph`` turns the portable graph mapping (``graph.nodes`` and
``graph.edges``, with every ``include`` already resolved) into typed nodes and
edges, plus the persona contracts the engine routes with: what each stage member
consumes and which event each verdict produces.

Edges are labelled ``"source_event -> target_event"``. The source event is what the
source node's step produced; the target event is what the target node consumes.

This module only models the graph. Whether a graph is *valid* is decided by
``ting.domain.workflow_document``; how a run moves through it is decided by
``ting.domain.step_engine``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ting.domain.exceptions import WorkflowDocumentError


class NodeKind(StrEnum):
    TRIGGER = "trigger"
    STAGE = "stage"
    GATE = "gate"
    END = "end"
    WAIT = "wait"
    SUBWORKFLOW = "subworkflow"
    RESOURCE = "resource"


#: Node kinds a run moves through. ``resource`` nodes only bind data; nothing activates them.
FLOW_NODE_KINDS = frozenset(
    {
        NodeKind.TRIGGER,
        NodeKind.STAGE,
        NodeKind.GATE,
        NodeKind.END,
        NodeKind.WAIT,
        NodeKind.SUBWORKFLOW,
    }
)

#: ``include`` stands in for copied nodes and is gone once a graph is resolved.
UNRESOLVED_INCLUDE_KIND = "include"
UNSUPPORTED_COND_KIND = "cond"

JOIN_ALL = "all"
JOIN_ANY = "any"
JOIN_MERGE = "merge"
JOIN_MODES = frozenset({JOIN_ALL, JOIN_ANY, JOIN_MERGE})

EXECUTION_SEQUENTIAL = "sequential"
EXECUTION_PARALLEL = "parallel"
EXECUTION_MODES = frozenset({EXECUTION_SEQUENTIAL, EXECUTION_PARALLEL})

#: What a graph that does not say gets, the same defaults the workflow builder applies.
DEFAULT_JOIN_MODE = JOIN_ALL
DEFAULT_EXECUTION_MODE = EXECUTION_PARALLEL

GATE_MODE_AUTOMATED_APPROVAL = "automated_approval"
DEFAULT_GATE_MODE = "human_approval"


def split_edge_label(label: object) -> tuple[str, str]:
    """Split ``"source_event -> target_event"``; ``("", "")`` when it is not that."""
    if not isinstance(label, str) or "->" not in label:
        return "", ""
    source, target = label.split("->", 1)
    return source.strip(), target.strip()


@dataclass(frozen=True)
class PersonaContract:
    """What a persona says about the events it takes in and gives out.

    ``produces_map`` maps a verdict to the event it hands off; when a persona has
    one, a verdict outside it is refused. ``produces_event`` is the event of a
    persona that declares no map, whatever its verdict.
    """

    produces_map: Mapping[str, str] = field(default_factory=dict)
    produces_event: str = ""
    consumes: tuple[str, ...] = ()
    #: The persona's ``produces.schema`` exactly as its document states it; ``None`` when absent.
    outcome_schema: Mapping[str, Any] | None = None

    @property
    def produced_events(self) -> frozenset[str]:
        if self.produces_map:
            return frozenset(self.produces_map.values())
        return frozenset({self.produces_event}) if self.produces_event else frozenset()

    def event_for(self, verdict: str) -> str | None:
        """The event a verdict hands off, or ``None`` when the persona does not name it."""
        if self.produces_map:
            return self.produces_map.get(verdict)
        return self.produces_event or None


def persona_contract_of(definition: Mapping[str, Any]) -> PersonaContract:
    """Read a persona's event contract from its portable document or its bare definition."""
    body = definition.get("definition")
    persona = body if isinstance(body, Mapping) else definition
    produces = persona.get("produces")
    produces = produces if isinstance(produces, Mapping) else {}
    consumes = persona.get("consumes")
    consumes = consumes if isinstance(consumes, Mapping) else {}
    raw_map = produces.get("event_type_map")
    event_map = (
        {str(verdict): str(event) for verdict, event in raw_map.items()}
        if isinstance(raw_map, Mapping)
        else {}
    )
    raw_consumes = consumes.get("event_types")
    raw_schema = produces.get("schema")
    return PersonaContract(
        produces_map=event_map,
        produces_event=str(produces.get("event_type") or ""),
        consumes=tuple(str(item) for item in raw_consumes)
        if isinstance(raw_consumes, list)
        else (),
        outcome_schema=dict(raw_schema) if isinstance(raw_schema, Mapping) and raw_schema else None,
    )


@dataclass(frozen=True)
class MemberSpec:
    """One persona taking part in a stage."""

    alias: str
    declared_consumes: tuple[str, ...] = ()
    event_filters: Mapping[str, str] = field(default_factory=dict)
    contract: PersonaContract | None = None

    @property
    def consumes(self) -> tuple[str, ...]:
        """The member's ``consumesEventTypes``, else its persona's ``consumes.event_types``."""
        if self.declared_consumes:
            return self.declared_consumes
        return self.contract.consumes if self.contract is not None else ()

    @property
    def produced_events(self) -> frozenset[str]:
        return self.contract.produced_events if self.contract is not None else frozenset()


@dataclass(frozen=True)
class ReviewPolicy:
    """``reviewVerdictPolicy``: a quorum of reviewer verdicts joined on one event type."""

    event_type: str
    pass_outcomes: frozenset[str]
    fail_outcomes: frozenset[str]
    binding_fields: tuple[str, ...]


@dataclass(frozen=True)
class NodeSpec:
    id: str
    kind: NodeKind
    label: str
    join_mode: str = DEFAULT_JOIN_MODE
    execution_mode: str = DEFAULT_EXECUTION_MODE
    max_concurrent: int | None = None
    members: tuple[MemberSpec, ...] = ()
    review_policy: ReviewPolicy | None = None
    # trigger
    dispatch_event: str = ""
    # gate
    gate_mode: str = DEFAULT_GATE_MODE
    approval_event: str = ""
    changes_requested_event: str = ""
    # end
    completion_event: str = ""
    passing_verdicts: tuple[str, ...] = ()
    # wait
    wait_conditions: tuple[str, ...] = ()
    # subworkflow
    blocked_event: str = ""
    # any waiting node
    deadline_seconds: float | None = None

    @property
    def concurrency_limit(self) -> int | None:
        """How many of this node's members may be active at once; ``None`` is unbounded."""
        if self.execution_mode == EXECUTION_SEQUENTIAL:
            return 1
        return self.max_concurrent

    def member(self, alias: str) -> MemberSpec | None:
        return next((member for member in self.members if member.alias == alias), None)


@dataclass(frozen=True)
class EdgeSpec:
    id: str
    source: str
    target: str
    source_event: str
    target_event: str
    rework: bool = False
    max_iterations: int | None = None


@dataclass(frozen=True)
class StepGraph:
    """A compiled workflow graph."""

    nodes: Mapping[str, NodeSpec]
    edges: tuple[EdgeSpec, ...]

    def incoming(self, node_id: str) -> tuple[EdgeSpec, ...]:
        return tuple(edge for edge in self.edges if edge.target == node_id)

    def outgoing(self, node_id: str) -> tuple[EdgeSpec, ...]:
        return tuple(edge for edge in self.edges if edge.source == node_id)

    def edge(self, edge_id: str) -> EdgeSpec | None:
        return next((edge for edge in self.edges if edge.id == edge_id), None)

    def triggers(self) -> tuple[NodeSpec, ...]:
        return tuple(node for node in self.nodes.values() if node.kind is NodeKind.TRIGGER)

    def ends(self) -> tuple[NodeSpec, ...]:
        return tuple(node for node in self.nodes.values() if node.kind is NodeKind.END)

    def producers(self, edge: EdgeSpec) -> tuple[str | None, ...]:
        """Who can produce an edge's source event: the members of a stage that can, else the node.

        A stage member is named by persona alias; any other node produces on its own
        behalf, which is ``None``.
        """
        source = self.nodes[edge.source]
        if source.kind is not NodeKind.STAGE:
            return (None,)
        return tuple(
            member.alias for member in source.members if edge.source_event in member.produced_events
        )

    def consumers(self, edge: EdgeSpec) -> tuple[MemberSpec, ...]:
        """The members of an edge's target stage that consume its target event."""
        target = self.nodes[edge.target]
        return tuple(member for member in target.members if edge.target_event in member.consumes)


def compile_step_graph(
    graph: Mapping[str, Any],
    persona_definitions: Mapping[str, Mapping[str, Any]] | None = None,
) -> StepGraph:
    """Compile a resolved portable graph.

    ``persona_definitions`` maps a stage member's alias to its persona document. A
    stage member whose alias has no definition keeps no contract; the validator
    reports that, and the engine refuses to run such a graph.
    """
    raw_nodes = graph.get("nodes") or []
    raw_edges = graph.get("edges") or []
    nodes: dict[str, NodeSpec] = {}
    for raw in raw_nodes:
        node = _compile_node(raw, persona_definitions)
        if node.id in nodes:
            raise WorkflowDocumentError(f"Workflow node id {node.id!r} is declared twice")
        nodes[node.id] = node
    edges = tuple(_compile_edge(raw) for raw in raw_edges)
    return StepGraph(nodes=nodes, edges=edges)


def _compile_node(
    raw: Mapping[str, Any],
    persona_definitions: Mapping[str, Mapping[str, Any]] | None,
) -> NodeSpec:
    node_id = str(raw.get("id") or "").strip()
    if not node_id:
        raise WorkflowDocumentError("Every workflow node needs an id")
    raw_kind = str(raw.get("kind") or "").strip()
    if raw_kind == UNRESOLVED_INCLUDE_KIND:
        raise WorkflowDocumentError(
            f"Workflow node {node_id!r} is an unresolved include; resolve includes before "
            "compiling the graph"
        )
    try:
        kind = NodeKind(raw_kind)
    except ValueError as exc:
        known = ", ".join(sorted(item.value for item in NodeKind))
        raise WorkflowDocumentError(
            f"Workflow node {node_id!r} has unknown kind {raw_kind!r}; the engine runs: {known}"
        ) from exc
    return NodeSpec(
        id=node_id,
        kind=kind,
        label=str(raw.get("label") or node_id),
        join_mode=str(raw.get("joinMode") or DEFAULT_JOIN_MODE),
        execution_mode=str(raw.get("executionMode") or DEFAULT_EXECUTION_MODE),
        max_concurrent=_optional_int(raw.get("maxConcurrent")),
        members=_compile_members(raw, persona_definitions) if kind is NodeKind.STAGE else (),
        review_policy=_compile_policy(raw.get("reviewVerdictPolicy")),
        dispatch_event=str(raw.get("dispatchEvent") or "").strip(),
        gate_mode=str(raw.get("mode") or DEFAULT_GATE_MODE),
        approval_event=str(raw.get("approvalEvent") or "").strip(),
        changes_requested_event=str(raw.get("changesRequestedEvent") or "").strip(),
        completion_event=str(raw.get("completionEvent") or "").strip(),
        passing_verdicts=tuple(str(item) for item in raw.get("passingVerdicts") or ()),
        wait_conditions=tuple(str(item) for item in raw.get("conditions") or ()),
        blocked_event=str(raw.get("blockedEvent") or "").strip(),
        deadline_seconds=_optional_float(raw.get("deadlineSeconds")),
    )


def _compile_members(
    raw: Mapping[str, Any],
    persona_definitions: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[MemberSpec, ...]:
    definitions = persona_definitions or {}

    def contract(alias: str) -> PersonaContract | None:
        definition = definitions.get(alias)
        return persona_contract_of(definition) if definition is not None else None

    members: list[MemberSpec] = []
    for raw_member in raw.get("stageMembers") or ():
        alias = str(raw_member.get("personaId") or "").strip()
        filters = raw_member.get("eventFilters")
        members.append(
            MemberSpec(
                alias=alias,
                declared_consumes=tuple(
                    str(item).strip() for item in raw_member.get("consumesEventTypes") or ()
                ),
                event_filters={str(k): str(v) for k, v in filters.items()}
                if isinstance(filters, Mapping)
                else {},
                contract=contract(alias),
            )
        )
    if members:
        return tuple(members)
    return tuple(
        MemberSpec(alias=str(alias).strip(), contract=contract(str(alias).strip()))
        for alias in raw.get("personaIds") or ()
    )


def _compile_policy(raw: object) -> ReviewPolicy | None:
    if not isinstance(raw, Mapping):
        return None
    return ReviewPolicy(
        event_type=str(raw.get("eventType") or "").strip(),
        pass_outcomes=frozenset(str(item).strip() for item in raw.get("passOutcomes") or ()),
        fail_outcomes=frozenset(str(item).strip() for item in raw.get("failOutcomes") or ()),
        binding_fields=tuple(str(item).strip() for item in raw.get("bindingFields") or ()),
    )


def _compile_edge(raw: Mapping[str, Any]) -> EdgeSpec:
    edge_id = str(raw.get("id") or "").strip()
    source_event, target_event = split_edge_label(raw.get("label"))
    max_iterations = raw.get("maxIterations")
    return EdgeSpec(
        id=edge_id,
        source=str(raw.get("source") or "").strip(),
        target=str(raw.get("target") or "").strip(),
        source_event=source_event,
        target_event=target_event,
        rework=raw.get("rework") is True,
        max_iterations=max_iterations if type(max_iterations) is int else None,
    )


def _optional_int(value: object) -> int | None:
    return value if type(value) is int else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
