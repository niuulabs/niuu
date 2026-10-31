"""Research campaign stage progress comes from stage evidence, not stage labels.

Stages used to complete only when their *label* contained one of a handful of
words ("frame", "explore", "challenge", "synth", ...). The bundled Research
Campaign and the Kanuck Valley Models variant both have stages that match
none of them — "Define and dispatch exploration threads" and "Analyze the
joined threads" — so those stages never completed, the first of them stayed
"active" forever, and later stages showed complete out of order (a synthesis
stage matched the analyst's ``analysis.md``).

These tests pin the evidence-driven derivation: stable identifiers (node id,
persona ids, produced events), declared artifact paths, the durable fan-out
join for the stage that dispatches it, upstream closure along the graph, and
the failed stage a runtime failure is attributed to.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests.test_ting.test_research_api import (
    InMemoryWorkflowCampaignRepository,
    InMemoryWorkflowRepository,
    RecordingVolundrFactory,
    RecordingVolundrPort,
    _headers,
    _make_client,
    _research_workflow,
)
from ting.api.research import CampaignArtifactResponse, _derive_stage_state
from ting.api.workflow_executions import resolve_optional_workflow_execution_repo
from ting.domain.campaign_stages import (
    close_upstream,
    failure_metadata,
    mark_stage_failed,
    stage_for_failure,
    workflow_stages,
)
from ting.domain.models import CampaignStageState, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.workflow_execution import CHILDREN_JOINED_SUSPENSION_REASON, ExecutionState
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.ports.volundr import VolundrSession
from ting.system_workflows import load_system_workflows

_STAGE_IDS = [
    "research-frame",
    "research-coordinate",
    "research-analysis",
    "research-challenge",
    "research-synthesis",
    "research-curation",
    "research-publish",
]
_SLUG = "kit-joinery"

#: The Kanuck Valley Models variant's stage labels and persona ids
#: (smidja/niuu/research/workflow.yaml), applied over the bundled graph.
_KVM_LABELS = {
    "research-frame": "Frame the inquiry",
    "research-coordinate": "Define and dispatch exploration threads",
    "research-analysis": "Analyze the joined threads",
    "research-challenge": "Challenge the thesis",
    "research-synthesis": "Synthesize the research",
    "research-curation": "Curate learnings and follow-ups",
    "research-publish": "Publish to Mimir (gbrain-kanuck)",
}
_KVM_PERSONAS = {
    "research-coordinator": "kvm-research-coordinator",
    "research-curator": "kvm-research-curator",
    "research-publisher": "kvm-research-publisher",
}


def _bundled_snapshot() -> dict[str, Any]:
    workflows = {workflow.name: workflow for workflow in load_system_workflows()}
    return build_workflow_snapshot(workflows["Research Campaign"])


def _kvm_snapshot() -> dict[str, Any]:
    """The KVM variant: KVM labels and personas, no pinned persona definitions."""
    snapshot = copy.deepcopy(_bundled_snapshot())
    snapshot.pop("persona_definitions", None)
    for node in snapshot["graph"]["nodes"]:
        if node.get("id") in _KVM_LABELS:
            node["label"] = _KVM_LABELS[node["id"]]
        for member in node.get("stageMembers") or []:
            member["personaId"] = _KVM_PERSONAS.get(member["personaId"], member["personaId"])
        if node.get("kind") == "subworkflow":
            node["allowedCoordinator"] = "kvm-research-coordinator"
    return snapshot


def _opaque_snapshot() -> dict[str, Any]:
    """Stage ids and labels that say nothing: only the graph's events remain."""
    snapshot = copy.deepcopy(_bundled_snapshot())
    renamed = {stage_id: f"step-{index}" for index, stage_id in enumerate(_STAGE_IDS, 1)}
    for node in snapshot["graph"]["nodes"]:
        if node.get("id") in renamed:
            node["label"] = f"Step {renamed[node['id']][-1]}"
            node["id"] = renamed[node["id"]]
    for edge in snapshot["graph"]["edges"]:
        edge["source"] = renamed.get(edge["source"], edge["source"])
        edge["target"] = renamed.get(edge["target"], edge["target"])
    return snapshot


def _artifacts(*kinds: str) -> list[CampaignArtifactResponse]:
    paths = {
        "brief": f"research/campaigns/{_SLUG}/brief.md",
        "plan": f"research/campaigns/{_SLUG}/plan.md",
        "note": f"research/campaigns/{_SLUG}/notes/breadth.md",
        "analysis": f"research/campaigns/{_SLUG}/analysis.md",
        "sources": f"research/campaigns/{_SLUG}/sources.md",
        "critique": f"research/campaigns/{_SLUG}/critique.md",
        "final": f"research/campaigns/{_SLUG}/final.md",
        "learnings": f"learnings/research/{_SLUG}.md",
        "followups": f"followups/research/{_SLUG}.md",
        "manifest": f"research/campaigns/{_SLUG}/manifest.md",
    }
    now = datetime.now(UTC)
    return [
        CampaignArtifactResponse(
            path=paths[kind],
            title=kind,
            updated_at=now,
            kind=kind,
            publish_state="unpublished",
        )
        for kind in kinds
    ]


def _joined_execution(state: ExecutionState = ExecutionState.RUNNING, **overrides: Any):
    return SimpleNamespace(
        parent_node_id="research-threads",
        state=state,
        suspension_reason=overrides.get("suspension_reason", CHILDREN_JOINED_SUSPENSION_REASON),
    )


def _statuses(stages: list[CampaignStageState]) -> dict[str, str]:
    return {stage.stage_id: stage.status for stage in stages}


def _derive(
    snapshot: dict[str, Any],
    *kinds: str,
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
    execution: Any = None,
    metadata: dict[str, Any] | None = None,
) -> list[CampaignStageState]:
    return _derive_stage_state(
        snapshot,
        _artifacts(*kinds),
        status,
        [],
        slug=_SLUG,
        execution=execution,
        metadata=metadata,
    )


@pytest.mark.parametrize("snapshot_factory", [_bundled_snapshot, _kvm_snapshot])
def test_framed_campaign_moves_on_to_the_coordinate_stage(snapshot_factory) -> None:
    stages = _derive(snapshot_factory(), "brief", "plan")

    assert _statuses(stages) == {
        "research-frame": "complete",
        "research-coordinate": "active",
        "research-analysis": "pending",
        "research-challenge": "pending",
        "research-synthesis": "pending",
        "research-curation": "pending",
        "research-publish": "pending",
    }


@pytest.mark.parametrize("snapshot_factory", [_bundled_snapshot, _kvm_snapshot])
def test_coordinate_stage_completes_when_its_children_join(snapshot_factory) -> None:
    stages = _derive(snapshot_factory(), "brief", "plan", execution=_joined_execution())

    statuses = _statuses(stages)
    assert statuses["research-coordinate"] == "complete"
    assert statuses["research-analysis"] == "active"


@pytest.mark.parametrize("snapshot_factory", [_bundled_snapshot, _kvm_snapshot])
def test_coordinate_stage_stays_active_while_children_run(snapshot_factory) -> None:
    waiting = _joined_execution(
        state=ExecutionState.WAITING,
        suspension_reason="awaiting_children",
    )
    # A child's note is not the join: the coordinator is still waiting.
    stages = _derive(snapshot_factory(), "brief", "plan", "note", execution=waiting)

    assert _statuses(stages)["research-coordinate"] == "active"


def test_a_completed_execution_has_joined() -> None:
    finished = _joined_execution(state=ExecutionState.COMPLETED, suspension_reason="")
    stages = _derive(_bundled_snapshot(), "brief", execution=finished)

    assert _statuses(stages)["research-coordinate"] == "complete"


def test_an_execution_for_another_node_is_not_evidence() -> None:
    other = SimpleNamespace(
        parent_node_id="some-other-fanout",
        state=ExecutionState.COMPLETED,
        suspension_reason=CHILDREN_JOINED_SUSPENSION_REASON,
    )
    stages = _derive(_bundled_snapshot(), "brief", execution=other)

    assert _statuses(stages)["research-coordinate"] == "active"


@pytest.mark.parametrize("snapshot_factory", [_bundled_snapshot, _kvm_snapshot])
def test_analysis_artifacts_complete_the_analysis_stage_not_synthesis(snapshot_factory) -> None:
    """``analysis.md`` is the analyst's; it must not complete the synthesis stage."""
    stages = _derive(snapshot_factory(), "brief", "plan", "analysis", "sources")

    assert _statuses(stages) == {
        "research-frame": "complete",
        # The analysis stage consumes the joined event, so the fan-out joined.
        "research-coordinate": "complete",
        "research-analysis": "complete",
        "research-challenge": "active",
        "research-synthesis": "pending",
        "research-curation": "pending",
        "research-publish": "pending",
    }


@pytest.mark.parametrize("snapshot_factory", [_bundled_snapshot, _kvm_snapshot, _opaque_snapshot])
@pytest.mark.parametrize(
    "kinds",
    [
        ("brief",),
        ("brief", "analysis"),
        ("critique",),
        ("final",),
        ("learnings",),
        ("manifest",),
        ("brief", "manifest"),
        ("analysis", "followups"),
    ],
)
def test_no_stage_completes_ahead_of_an_open_upstream_stage(snapshot_factory, kinds) -> None:
    statuses = [stage.status for stage in _derive(snapshot_factory(), *kinds)]

    first_open = next(
        (index for index, value in enumerate(statuses) if value != "complete"), len(statuses)
    )
    assert all(value != "complete" for value in statuses[first_open:]), statuses
    assert statuses.count("active") == (1 if first_open < len(statuses) else 0)


@pytest.mark.parametrize("snapshot_factory", [_bundled_snapshot, _kvm_snapshot, _opaque_snapshot])
def test_published_campaign_has_every_stage_complete(snapshot_factory) -> None:
    stages = _derive(snapshot_factory(), "manifest")

    assert [stage.status for stage in stages] == ["complete"] * 7


def test_opaque_stages_complete_from_the_events_they_produce() -> None:
    """Ids and labels say nothing; the graph's edge events still identify stages."""
    stages = _derive(_opaque_snapshot(), "brief", "plan", "analysis", "critique")

    assert [stage.status for stage in stages] == [
        "complete",
        "complete",
        "complete",
        "complete",
        "active",
        "pending",
        "pending",
    ]


def test_label_matching_remains_the_fallback() -> None:
    snapshot = {
        "graph": {
            "nodes": [
                {"id": "s1", "kind": "stage", "label": "Frame the inquiry"},
                {"id": "s2", "kind": "stage", "label": "Critique it"},
                {"id": "s3", "kind": "stage", "label": "Wrap up"},
            ],
            "edges": [
                {"id": "e1", "source": "s1", "target": "s2"},
                {"id": "e2", "source": "s2", "target": "s3"},
            ],
        }
    }

    assert [stage.status for stage in _derive(snapshot, "brief")] == [
        "complete",
        "active",
        "pending",
    ]
    assert [stage.status for stage in _derive(snapshot, "critique")] == [
        "complete",
        "complete",
        "active",
    ]


def test_declared_stage_artifact_paths_are_authoritative() -> None:
    snapshot = {
        "graph": {
            "nodes": [
                {
                    "id": "gather",
                    "kind": "stage",
                    "label": "Gather",
                    "artifactPaths": [
                        "research/campaigns/{slug}/photos.md",
                        "research/campaigns/{slug}/sizes.md",
                    ],
                },
                {"id": "draw", "kind": "stage", "label": "Draw"},
            ],
            "edges": [{"id": "e1", "source": "gather", "target": "draw"}],
        }
    }
    now = datetime.now(UTC)

    def _page(name: str) -> CampaignArtifactResponse:
        return CampaignArtifactResponse(
            path=f"research/campaigns/{_SLUG}/{name}",
            title=name,
            updated_at=now,
            publish_state="unpublished",
        )

    partial = _derive_stage_state(
        snapshot, [_page("photos.md")], WorkflowCampaignStatus.RUNNING, [], slug=_SLUG
    )
    full = _derive_stage_state(
        snapshot,
        [_page("photos.md"), _page("sizes.md")],
        WorkflowCampaignStatus.RUNNING,
        [],
        slug=_SLUG,
    )

    assert _statuses(partial) == {"gather": "active", "draw": "pending"}
    assert _statuses(full) == {"gather": "complete", "draw": "active"}


def test_a_revision_loop_does_not_make_a_later_stage_upstream() -> None:
    snapshot = {
        "graph": {
            "nodes": [
                {"id": "start", "kind": "trigger"},
                {"id": "frame", "kind": "stage", "label": "Frame"},
                {"id": "critique", "kind": "stage", "label": "Critique"},
                {"id": "publish", "kind": "stage", "label": "Publish"},
            ],
            "edges": [
                {"id": "e0", "source": "start", "target": "frame"},
                {"id": "e1", "source": "frame", "target": "critique"},
                {"id": "e2", "source": "critique", "target": "publish"},
                # A revision loop back to the framing stage.
                {"id": "e3", "source": "critique", "target": "frame"},
            ],
        }
    }
    stages = workflow_stages(snapshot)

    assert close_upstream(stages, {"frame"}) == {"frame"}
    assert close_upstream(stages, {"publish"}) == {"frame", "critique", "publish"}


def test_workflow_stages_read_the_graph_facts() -> None:
    stages = {stage.id: stage for stage in workflow_stages(_bundled_snapshot())}

    coordinate = stages["research-coordinate"]
    assert coordinate.dispatches_subworkflows == ("research-threads",)
    assert coordinate.persona_ids == ("research-coordinator",)
    assert "research.threads.waiting" in coordinate.produced_events
    assert stages["research-analysis"].upstream == {"research-frame", "research-coordinate"}
    # Persona-declared produced events come from the pinned definitions.
    assert "research.analysis.completed" in stages["research-analysis"].produced_events


# ---------------------------------------------------------------------------
# Failure attribution
# ---------------------------------------------------------------------------


def test_failure_is_attributed_to_the_failing_node_then_the_persona() -> None:
    snapshot = _kvm_snapshot()

    assert (
        stage_for_failure(snapshot, workflow_node_id="research-coordinate") == "research-coordinate"
    )
    assert stage_for_failure(snapshot, persona="kvm-research-coordinator") == "research-coordinate"
    # A node id that is not a stage falls back to the persona.
    assert (
        stage_for_failure(snapshot, workflow_node_id="research-threads", persona="research-skeptic")
        == "research-challenge"
    )
    assert stage_for_failure(snapshot, persona="someone-else") is None


def test_failure_metadata_records_error_and_stage() -> None:
    recorded = failure_metadata(
        _kvm_snapshot(),
        {
            "failure_source": "ravn_flock",
            "failure_persona": "kvm-research-coordinator",
            "failure_workflow_node_id": "research-coordinate",
            "failure_task_id": "event_research_coordinate_fe4ae2efb1c879c7",
            "failure_kind": "RuntimeError",
        },
        error="RuntimeError: Persona requires durable workflow execution tools",
    )

    assert recorded == {
        "failure_error": "RuntimeError: Persona requires durable workflow execution tools",
        "failure_persona": "kvm-research-coordinator",
        "failure_workflow_node_id": "research-coordinate",
        "failure_task_id": "event_research_coordinate_fe4ae2efb1c879c7",
        "failure_kind": "RuntimeError",
        "failure_stage_id": "research-coordinate",
    }


def test_mark_stage_failed_moves_the_failure_onto_the_named_stage() -> None:
    now = datetime.now(UTC)
    state = [
        CampaignStageState(stage_id="a", label="A", status="complete"),
        CampaignStageState(stage_id="b", label="B", status="active", started_at=now),
        CampaignStageState(stage_id="c", label="C", status="pending"),
    ]

    failed = mark_stage_failed(state, "b", reason="boom", now=now)
    assert _statuses(failed) == {"a": "complete", "b": "failed", "c": "pending"}
    assert failed[1].reason == "boom"
    # Complete and unknown stages are never re-failed.
    assert mark_stage_failed(state, "a", reason="boom", now=now) is state
    assert mark_stage_failed(state, "zzz", reason="boom", now=now) is state


def test_failed_campaign_marks_the_attributed_stage_with_its_error() -> None:
    error = "RuntimeError: Persona requires durable workflow execution tools"
    stages = _derive(
        _kvm_snapshot(),
        "brief",
        "plan",
        status=WorkflowCampaignStatus.FAILED,
        metadata={"failure_error": error, "failure_stage_id": "research-coordinate"},
    )

    coordinate = next(stage for stage in stages if stage.stage_id == "research-coordinate")
    assert coordinate.status == "failed"
    assert coordinate.reason == error
    assert _statuses(stages)["research-frame"] == "complete"
    assert _statuses(stages)["research-analysis"] == "pending"


def test_failed_campaign_without_attribution_fails_the_current_stage() -> None:
    stages = _derive(
        _bundled_snapshot(),
        "brief",
        status=WorkflowCampaignStatus.FAILED,
        metadata={"failure_error": "Session failed"},
    )

    coordinate = next(stage for stage in stages if stage.stage_id == "research-coordinate")
    assert coordinate.status == "failed"
    assert coordinate.reason == "Session failed"


# ---------------------------------------------------------------------------
# API: runtime refresh and campaign detail
# ---------------------------------------------------------------------------


def _local_campaign(
    tmp_path: Path,
    *,
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
    metadata: dict[str, Any] | None = None,
) -> tuple[WorkflowCampaign, InMemoryWorkflowRepository]:
    """A campaign on the bundled research graph, reading Mímir from ``tmp_path``."""
    workflow = _research_workflow(tmp_path)
    snapshot = build_workflow_snapshot(workflow)
    bundled = _bundled_snapshot()
    resources = [node for node in snapshot["graph"]["nodes"] if node.get("kind") == "resource"]
    snapshot["graph"] = {
        **bundled["graph"],
        "nodes": [
            *resources,
            *(node for node in bundled["graph"]["nodes"] if node.get("kind") != "resource"),
        ],
    }
    now = datetime.now(UTC)
    stage_state = [
        CampaignStageState(
            stage_id=stage.id,
            label=stage.label,
            status="active" if index == 0 else "pending",
        )
        for index, stage in enumerate(workflow_stages(snapshot))
    ]
    campaign = WorkflowCampaign(
        id=uuid4(),
        slug=_SLUG,
        name="Kit joinery",
        owner_id="user-1",
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_name=workflow.name,
        workflow_snapshot=snapshot,
        session_id="session-1",
        session_name="Kit joinery",
        status=status,
        active_stage_id=stage_state[0].stage_id,
        stage_state=stage_state,
        metadata={"question": "Which joinery holds up?", **(metadata or {})},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
    )
    return campaign, InMemoryWorkflowRepository([workflow])


def _session(**overrides: Any) -> VolundrSession:
    return VolundrSession(
        id="session-1",
        name="Kit joinery",
        status=overrides.pop("status", "running"),
        tracker_issue_id="workflow:kit-joinery",
        cluster_name="local",
        repo="",
        branch="",
        base_branch="",
        workload_type="ravn_flock",
        **overrides,
    )


def test_list_fails_a_campaign_whose_stage_runtime_errored(tmp_path: Path) -> None:
    """The live failure: the pod stays Running, the coordinator's Ravn is dead."""
    error = (
        "RuntimeError: Persona requires durable workflow execution tools, but an "
        "owner-bound workflow_execution runtime context is not configured"
    )
    campaign, workflow_repo = _local_campaign(tmp_path)
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    port = RecordingVolundrPort()
    port.sessions["session-1"] = _session(
        activity_state="error",
        activity_metadata={
            "failure_source": "ravn_flock",
            "failure_peer_id": "flock-kvm-research-coordinator",
            "failure_persona": "research-coordinator",
            "failure_workflow_node_id": "research-coordinate",
            "error": error,
        },
    )
    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(port))

    response = client.get("/api/v1/ting/research/campaigns", headers=_headers())

    assert response.status_code == 200
    (body,) = response.json()
    assert body["status"] == "failed"
    assert body["activeStageId"] == "research-coordinate"
    assert body["metadata"]["failure_error"] == error
    assert body["metadata"]["failure_stage_id"] == "research-coordinate"
    coordinate = next(
        stage for stage in body["stageState"] if stage["stageId"] == "research-coordinate"
    )
    assert coordinate == {**coordinate, "status": "failed", "reason": error}


def test_a_failed_campaign_is_not_revived_by_a_running_session(tmp_path: Path) -> None:
    campaign, workflow_repo = _local_campaign(
        tmp_path,
        status=WorkflowCampaignStatus.FAILED,
        metadata={"failure_error": "boom", "failure_stage_id": "research-coordinate"},
    )
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    port = RecordingVolundrPort()
    port.sessions["session-1"] = _session(status="running", activity_state="idle")
    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(port))

    response = client.get("/api/v1/ting/research/campaigns", headers=_headers())

    assert response.json()[0]["status"] == "failed"


class _ExecutionRepo:
    def __init__(self, execution: Any) -> None:
        self.execution = execution
        self.calls: list[tuple] = []

    async def get(self, execution_id, *, owner_id, tenant_id):
        self.calls.append((execution_id, owner_id, tenant_id))
        return self.execution


def test_detail_completes_the_coordinate_stage_from_the_joined_execution(
    tmp_path: Path,
) -> None:
    execution_id = uuid4()
    campaign, workflow_repo = _local_campaign(
        tmp_path, metadata={"workflow_execution_id": str(execution_id)}
    )
    campaign_dir = tmp_path / "wiki" / "research" / "campaigns" / _SLUG
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "brief.md").write_text("# Brief\n\nOne line.", encoding="utf-8")
    (campaign_dir / "plan.md").write_text("# Plan\n\nThreads.", encoding="utf-8")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    port = RecordingVolundrPort()
    port.sessions["session-1"] = _session()
    execution_repo = _ExecutionRepo(_joined_execution())
    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(port))
    client.app.dependency_overrides[resolve_optional_workflow_execution_repo] = lambda: (
        execution_repo
    )

    response = client.get(f"/api/v1/ting/research/campaigns/{_SLUG}", headers=_headers())

    assert response.status_code == 200
    statuses = {stage["stageId"]: stage["status"] for stage in response.json()["stageState"]}
    assert statuses["research-frame"] == "complete"
    assert statuses["research-coordinate"] == "complete"
    assert statuses["research-analysis"] == "active"
    assert response.json()["activeStageId"] == "research-analysis"
    assert execution_repo.calls and execution_repo.calls[0][0] == execution_id


def test_a_failed_campaign_is_not_completed_by_its_stopped_session(tmp_path: Path) -> None:
    """Failing a campaign stops its session; that stop is not a completion."""
    campaign, workflow_repo = _local_campaign(
        tmp_path,
        status=WorkflowCampaignStatus.FAILED,
        metadata={"failure_error": "boom", "failure_stage_id": "research-coordinate"},
    )
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    port = RecordingVolundrPort()
    port.sessions["session-1"] = _session(status="stopped")
    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(port))

    response = client.get("/api/v1/ting/research/campaigns", headers=_headers())

    assert response.json()[0]["status"] == "failed"
