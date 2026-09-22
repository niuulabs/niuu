"""Tests for Ting specification campaign REST API."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from niuu.domain.mimir import MimirPage, MimirPageMeta
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.specs import (
    _DEFAULT_SPEC_WORKFLOW_NAME,
    SpecCampaignCreateBody,
    _build_spec_prompt,
    _campaign_name,
    _classify_spec_artifact_kind,
    _derive_spec_stage_state,
    _is_spec_campaign,
    _load_spec_artifacts,
    _request_repos,
    _reserve_spec_slug,
    _resolve_spec_workflow,
    _select_review_gate,
    _selected_version,
    _spec_artifact_response,
    _spec_campaign_owns_path,
    _spec_published_paths,
    _spec_stage_requirement_met,
    _spec_title_from_path,
    _stage_for_pending_gate,
    _stored_spec_gates,
    _sync_pending_spec_gates,
    create_specs_router,
)
from ting.api.workflows import resolve_workflow_repo
from ting.config import AuthConfig, Settings
from ting.domain.models import (
    CampaignStageState,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
    WorkflowScope,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot

from .test_research_api import (
    InMemoryWorkflowCampaignRepository,
    InMemoryWorkflowRepository,
    RecordingVolundrFactory,
    RecordingVolundrPort,
    _headers,
)


def _spec_workflow(root: Path) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Specification Stack",
        description="Spec workflow",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["specification"],
            "nodes": [
                {"id": "spec-request", "kind": "trigger", "dispatchEvent": "spec.requested"},
                {
                    "id": "memory",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "mount_name": "local",
                    "path": str(root),
                },
                {
                    "id": "spec-frame",
                    "kind": "stage",
                    "label": "Frame initiative",
                    "stageMembers": [
                        {
                            "personaId": "specification-framer",
                            "budget": 12,
                            "model": "gpt-5.5",
                        }
                    ],
                },
                {
                    "id": "spec-prd",
                    "kind": "stage",
                    "label": "Draft PRD",
                    "stageMembers": [
                        {
                            "personaId": "specification-prd-author",
                            "budget": 12,
                            "model": "gpt-5.5",
                        }
                    ],
                },
                {
                    "id": "spec-prd-review",
                    "kind": "stage",
                    "label": "Review PRD",
                    "stageMembers": [
                        {
                            "personaId": "specification-prd-critic",
                            "budget": 12,
                            "model": "gpt-5.5",
                        }
                    ],
                },
                {
                    "id": "spec-prd-gate",
                    "kind": "gate",
                    "mode": "human_approval",
                    "pendingBehavior": "help_needed",
                    "approvalEvent": "spec.prd.approved",
                    "changesRequestedEvent": "spec.prd.changes_requested",
                },
            ],
            "edges": [],
            "resourceBindings": [
                {
                    "id": "binding-1",
                    "resourceNodeId": "memory",
                    "targetType": "workflow",
                    "targetId": "specification-stack",
                    "access": "read_write",
                    "writePrefixes": ["specifications/"],
                    "readPriority": 1,
                }
            ],
        },
        created_at=now,
        updated_at=now,
    )


class GateRecordingVolundrPort(RecordingVolundrPort):
    def __init__(self) -> None:
        super().__init__()
        self.gates = [
            {
                "id": "gate-prd",
                "node_id": "spec-prd-gate",
                "status": "pending",
                "summary": "PRD approval gate",
            }
        ]
        self.resolved: list[dict[str, object]] = []

    async def get_workflow_gates(self, session_id: str, *, auth_token=None, principal=None):
        return self.gates

    async def resolve_workflow_gate(
        self,
        session_id: str,
        gate_id: str,
        decision: str,
        *,
        notes: str = "",
        source: str = "ting",
        auth_token=None,
        principal=None,
    ):
        self.resolved.append(
            {
                "session_id": session_id,
                "gate_id": gate_id,
                "decision": decision,
                "notes": notes,
                "source": source,
                "principal": principal.user_id if principal else None,
            }
        )
        return {"status": "resolved"}


def _make_client(
    workflow_repo: InMemoryWorkflowRepository,
    campaign_repo: InMemoryWorkflowCampaignRepository,
    volundr_port: RecordingVolundrPort,
) -> TestClient:
    app = FastAPI()
    app.state.authorization = AllowAllAuthorizationAdapter()
    app.include_router(create_specs_router())
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.event_bus = InMemoryEventBus()
    app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    app.dependency_overrides[resolve_volundr_factory] = lambda: RecordingVolundrFactory(
        volundr_port
    )
    return TestClient(app)


def _campaign_for_workflow(workflow: WorkflowDefinition, slug: str) -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug=slug,
        name=slug.replace("-", " ").title(),
        owner_id="user-1",
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_name=workflow.name,
        workflow_snapshot=build_workflow_snapshot(workflow),
        session_id=f"session-{slug}",
        session_name=slug,
        status=WorkflowCampaignStatus.BLOCKED,
        active_stage_id="spec-prd-review",
        stage_state=[],
        metadata={
            "surface": "ting.specs",
            "prompt": "Specify SDCP operator",
            "pending_workflow_gates": [
                {
                    "id": "gate-prd",
                    "node_id": "spec-prd-gate",
                    "status": "pending",
                    "summary": "PRD approval gate",
                }
            ],
        },
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
    )


def test_create_spec_campaign_launches_specification_stack(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign_repo = InMemoryWorkflowCampaignRepository()
    volundr_port = RecordingVolundrPort()
    client = _make_client(
        InMemoryWorkflowRepository([workflow]),
        campaign_repo,
        volundr_port,
    )

    response = client.post(
        "/api/v1/ting/specs/campaigns",
        headers=_headers(),
        json={
            "prompt": "Plan SDCP v3.0.0 as a Kubernetes operator for 3D printers.",
            "name": "SDCP operator",
            "repos": [
                "https://github.com/niuulabs/volundr.git",
                "https://github.com/niuulabs/ravn.git",
            ],
            "branch": "dev",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workflowName"] == "Specification Stack"
    assert body["metadata"]["surface"] == "ting.specs"
    assert body["metadata"]["repos"] == [
        "https://github.com/niuulabs/volundr.git",
        "https://github.com/niuulabs/ravn.git",
    ]
    assert len(volundr_port.requests) == 1
    request = volundr_port.requests[0]
    assert request.repo == "https://github.com/niuulabs/volundr.git"
    assert "Pause at each review gate" in request.initial_prompt
    assert "https://github.com/niuulabs/ravn.git" in request.initial_prompt


def test_detail_reads_spec_artifacts_from_mimir(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([workflow]),
        campaign_repo,
        RecordingVolundrPort(),
    )
    spec_dir = tmp_path / "wiki" / "specifications" / "sdcp-operator"
    spec_dir.mkdir(parents=True)
    (spec_dir / "00-brief.md").write_text("# Brief\n\nFrame it.", encoding="utf-8")
    (spec_dir / "10-prd.md").write_text("# PRD\n\nProduct needs.", encoding="utf-8")
    (spec_dir / "11-prd-review.md").write_text("# PRD Review\n\nLooks good.", encoding="utf-8")
    (spec_dir / "50-manifest.md").write_text(
        "\n".join(
            [
                "# Manifest",
                "- specifications/sdcp-operator/10-prd.md",
                "- specifications/sdcp-operator/50-manifest.md",
            ]
        ),
        encoding="utf-8",
    )

    response = client.get("/api/v1/ting/specs/campaigns/sdcp-operator", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    paths = {artifact["path"]: artifact for artifact in body["artifacts"]}
    assert paths["specifications/sdcp-operator/10-prd.md"]["kind"] == "prd"
    assert paths["specifications/sdcp-operator/10-prd.md"]["publishState"] == "published"
    assert body["canonicalArtifacts"]["prd"] == "specifications/sdcp-operator/10-prd.md"
    assert (
        body["canonicalArtifacts"]["prd_review"] == "specifications/sdcp-operator/11-prd-review.md"
    )


def test_detail_clears_stored_gate_when_live_gate_is_resolved(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    volundr_port = GateRecordingVolundrPort()
    volundr_port.gates = []
    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, volundr_port)

    response = client.get("/api/v1/ting/specs/campaigns/sdcp-operator", headers=_headers())

    assert response.status_code == 200
    assert response.json()["metadata"]["pending_workflow_gates"] == []


def test_review_resolves_pending_spec_gate(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    volundr_port = GateRecordingVolundrPort()
    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, volundr_port)

    response = client.post(
        "/api/v1/ting/specs/campaigns/sdcp-operator/review",
        headers={**_headers(), "Authorization": "Bearer test-token"},
        json={
            "decision": "changes_requested",
            "notes": "Clarify printer capability discovery.",
            "nodeId": "spec-prd-gate",
        },
    )

    assert response.status_code == 200
    assert volundr_port.resolved == [
        {
            "session_id": "session-sdcp-operator",
            "gate_id": "gate-prd",
            "decision": "CHANGES_REQUESTED",
            "notes": "Clarify printer capability discovery.",
            "source": "ting.specs",
            "principal": "user-1",
        }
    ]
    assert response.json()["metadata"]["latest_spec_review"]["decision"] == "changes_requested"


# ---------------------------------------------------------------------------
# Route coverage: list, delete, artifact listing/detail, review edge cases
# ---------------------------------------------------------------------------


def test_list_campaigns_filters_to_spec_surface_and_refreshes(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    spec_campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    other_campaign = WorkflowCampaign(
        **{
            **spec_campaign.__dict__,
            "id": uuid4(),
            "slug": "not-a-spec",
            "metadata": {},
            "workflow_name": "Something Else",
        }
    )
    campaign_repo = InMemoryWorkflowCampaignRepository([spec_campaign, other_campaign])
    volundr_port = GateRecordingVolundrPort()
    volundr_port.gates = []
    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, volundr_port)

    response = client.get("/api/v1/ting/specs/campaigns", headers=_headers())

    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()}
    assert slugs == {"sdcp-operator"}


def test_delete_campaign_removes_and_then_404s(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([workflow]), campaign_repo, RecordingVolundrPort()
    )

    response = client.delete("/api/v1/ting/specs/campaigns/sdcp-operator", headers=_headers())
    assert response.status_code == 204

    missing = client.delete("/api/v1/ting/specs/campaigns/sdcp-operator", headers=_headers())
    assert missing.status_code == 404


def test_list_campaign_artifacts_route_returns_pages(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([workflow]), campaign_repo, RecordingVolundrPort()
    )
    spec_dir = tmp_path / "wiki" / "specifications" / "sdcp-operator"
    spec_dir.mkdir(parents=True)
    (spec_dir / "00-brief.md").write_text("# Brief", encoding="utf-8")

    response = client.get(
        "/api/v1/ting/specs/campaigns/sdcp-operator/artifacts", headers=_headers()
    )

    assert response.status_code == 200
    paths = {artifact["path"] for artifact in response.json()}
    assert "specifications/sdcp-operator/00-brief.md" in paths


def test_get_campaign_artifact_path_ownership_rejected(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([workflow]), campaign_repo, RecordingVolundrPort()
    )

    response = client.get(
        "/api/v1/ting/specs/campaigns/sdcp-operator/artifact",
        params={"path": "specifications/someone-else/00-brief.md"},
        headers=_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Artifact not found"


def test_get_campaign_artifact_no_mimir_mount_configured(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    # A workflow snapshot without a mimir resource node has no adapter.
    bare_workflow = WorkflowDefinition(
        **{**workflow.__dict__, "id": uuid4(), "graph": {"nodes": [], "edges": []}}
    )
    campaign = _campaign_for_workflow(bare_workflow, "sdcp-operator")
    campaign = WorkflowCampaign(
        **{**campaign.__dict__, "workflow_snapshot": build_workflow_snapshot(bare_workflow)}
    )
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([bare_workflow]), campaign_repo, RecordingVolundrPort()
    )

    response = client.get(
        "/api/v1/ting/specs/campaigns/sdcp-operator/artifact",
        params={"path": "specifications/sdcp-operator/00-brief.md"},
        headers=_headers(),
    )

    assert response.status_code == 503
    assert "No Mimir mount" in response.json()["detail"]


def test_get_campaign_artifact_missing_page_is_404(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([workflow]), campaign_repo, RecordingVolundrPort()
    )
    (tmp_path / "wiki" / "specifications" / "sdcp-operator").mkdir(parents=True)

    response = client.get(
        "/api/v1/ting/specs/campaigns/sdcp-operator/artifact",
        params={"path": "specifications/sdcp-operator/10-prd.md"},
        headers=_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Artifact not found"


def test_get_campaign_artifact_returns_full_content(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([workflow]), campaign_repo, RecordingVolundrPort()
    )
    spec_dir = tmp_path / "wiki" / "specifications" / "sdcp-operator"
    spec_dir.mkdir(parents=True)
    (spec_dir / "10-prd.md").write_text("# PRD\n\nBody text.", encoding="utf-8")

    response = client.get(
        "/api/v1/ting/specs/campaigns/sdcp-operator/artifact",
        params={"path": "specifications/sdcp-operator/10-prd.md"},
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "prd"
    assert "Body text." in body["content"]


def test_review_requires_notes_when_requesting_changes(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        InMemoryWorkflowRepository([workflow]), campaign_repo, GateRecordingVolundrPort()
    )

    response = client.post(
        "/api/v1/ting/specs/campaigns/sdcp-operator/review",
        headers=_headers(),
        json={"decision": "changes_requested", "notes": "   "},
    )

    assert response.status_code == 422
    assert "notes are required" in response.json()["detail"]


def test_review_returns_503_when_no_volundr_adapter_available(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    app = FastAPI()
    app.state.authorization = AllowAllAuthorizationAdapter()
    app.include_router(create_specs_router())
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.event_bus = InMemoryEventBus()
    app.dependency_overrides[resolve_workflow_repo] = lambda: InMemoryWorkflowRepository([workflow])
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo

    class _EmptyFactory:
        async def primary_for_principal(self, principal):
            return None

        async def for_principal(self, principal):
            return []

    app.dependency_overrides[resolve_volundr_factory] = lambda: _EmptyFactory()
    client = TestClient(app)

    response = client.post(
        "/api/v1/ting/specs/campaigns/sdcp-operator/review",
        headers=_headers(),
        json={"decision": "approve"},
    )

    assert response.status_code == 503
    assert "No Volundr connection" in response.json()["detail"]


def test_review_404s_when_no_pending_gate_matches(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    volundr_port = GateRecordingVolundrPort()
    volundr_port.gates = []
    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, volundr_port)

    response = client.post(
        "/api/v1/ting/specs/campaigns/sdcp-operator/review",
        headers=_headers(),
        json={"decision": "approve"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No pending specification gate found"


def test_review_404s_when_matched_gate_has_no_id(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    volundr_port = GateRecordingVolundrPort()
    volundr_port.gates = [
        {"node_id": "spec-prd-gate", "status": "pending", "summary": "no id here"}
    ]
    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, volundr_port)

    response = client.post(
        "/api/v1/ting/specs/campaigns/sdcp-operator/review",
        headers=_headers(),
        json={"decision": "approve"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No pending specification gate found"


def test_review_approve_path_records_approve_decision(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    volundr_port = GateRecordingVolundrPort()
    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, volundr_port)

    response = client.post(
        "/api/v1/ting/specs/campaigns/sdcp-operator/review",
        headers=_headers(),
        json={"decision": "approve"},
    )

    assert response.status_code == 200
    assert volundr_port.resolved[0]["decision"] == "APPROVE"
    assert response.json()["metadata"]["latest_spec_review"]["decision"] == "approve"


def test_review_wraps_gate_resolution_failure_as_502(tmp_path: Path) -> None:
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "sdcp-operator")
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])

    class _FailingGatePort(GateRecordingVolundrPort):
        async def resolve_workflow_gate(self, *args, **kwargs):
            raise RuntimeError("gateway exploded")

    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, _FailingGatePort())

    response = client.post(
        "/api/v1/ting/specs/campaigns/sdcp-operator/review",
        headers=_headers(),
        json={"decision": "approve"},
    )

    assert response.status_code == 502
    assert "gateway exploded" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Direct unit coverage for the module's pure/private helpers
# ---------------------------------------------------------------------------


def _tagged_workflow(tmp_path: Path, *, name: str, tag: bool = True) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name=name,
        description="",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={"tags": ["specification"] if tag else [], "nodes": []},
        created_at=now,
        updated_at=now,
    )


class TestResolveSpecWorkflow:
    async def test_explicit_workflow_id_missing_is_404(self, tmp_path):
        repo = InMemoryWorkflowRepository([])
        with pytest.raises(HTTPException) as exc:
            await _resolve_spec_workflow(repo=repo, owner_id="u", workflow_id=uuid4())
        assert exc.value.status_code == 404

    async def test_explicit_workflow_id_and_version_resolves_via_get_workflow_version(
        self, tmp_path
    ):
        workflow = _tagged_workflow(tmp_path, name="Specification Stack")
        repo = InMemoryWorkflowRepository([workflow])
        resolved = await _resolve_spec_workflow(
            repo=repo, owner_id="u", workflow_id=workflow.id, workflow_version=workflow.version
        )
        assert resolved is workflow

    async def test_prefers_default_named_tagged_workflow(self, tmp_path):
        default = _tagged_workflow(tmp_path, name=_DEFAULT_SPEC_WORKFLOW_NAME)
        other = _tagged_workflow(tmp_path, name="Other Spec")
        repo = InMemoryWorkflowRepository([other, default])
        resolved = await _resolve_spec_workflow(repo=repo, owner_id="u", workflow_id=None)
        assert resolved is default

    async def test_falls_back_to_first_tagged_workflow(self, tmp_path):
        only_tagged = _tagged_workflow(tmp_path, name="Some Spec Flavor")
        repo = InMemoryWorkflowRepository([only_tagged])
        resolved = await _resolve_spec_workflow(repo=repo, owner_id="u", workflow_id=None)
        assert resolved is only_tagged

    async def test_falls_back_to_untagged_default_named_workflow(self, tmp_path):
        untagged_default = _tagged_workflow(tmp_path, name=_DEFAULT_SPEC_WORKFLOW_NAME, tag=False)
        repo = InMemoryWorkflowRepository([untagged_default])
        resolved = await _resolve_spec_workflow(repo=repo, owner_id="u", workflow_id=None)
        assert resolved is untagged_default

    async def test_raises_404_when_nothing_matches(self, tmp_path):
        unrelated = _tagged_workflow(tmp_path, name="Unrelated", tag=False)
        repo = InMemoryWorkflowRepository([unrelated])
        with pytest.raises(HTTPException) as exc:
            await _resolve_spec_workflow(repo=repo, owner_id="u", workflow_id=None)
        assert exc.value.status_code == 404


class TestSelectedVersion:
    async def test_no_version_returns_workflow_unchanged(self, tmp_path):
        workflow = _tagged_workflow(tmp_path, name="X")
        repo = InMemoryWorkflowRepository([workflow])
        assert await _selected_version(repo, workflow, None) is workflow

    async def test_version_not_found_is_404(self, tmp_path):
        workflow = _tagged_workflow(tmp_path, name="X")
        repo = InMemoryWorkflowRepository([workflow])
        with pytest.raises(HTTPException) as exc:
            await _selected_version(repo, workflow, "9.9.9")
        assert exc.value.status_code == 404

    async def test_version_found_returns_selected(self, tmp_path):
        workflow = _tagged_workflow(tmp_path, name="X")
        repo = InMemoryWorkflowRepository([workflow])
        assert await _selected_version(repo, workflow, workflow.version) is workflow


def test_is_spec_campaign_surface_and_fallback(tmp_path):
    workflow = _tagged_workflow(tmp_path, name=_DEFAULT_SPEC_WORKFLOW_NAME)
    campaign = _campaign_for_workflow(workflow, "slug")
    assert _is_spec_campaign(campaign) is True

    non_matching_surface = WorkflowCampaign(
        **{**campaign.__dict__, "metadata": {"surface": "ting.research"}}
    )
    assert _is_spec_campaign(non_matching_surface) is False

    no_surface_matches_name = WorkflowCampaign(**{**campaign.__dict__, "metadata": {}})
    assert _is_spec_campaign(no_surface_matches_name) is True

    no_surface_wrong_name = WorkflowCampaign(
        **{**campaign.__dict__, "metadata": {}, "workflow_name": "Other"}
    )
    assert _is_spec_campaign(no_surface_wrong_name) is False


def test_request_repos_dedupes_and_normalizes_whitespace():
    body = SpecCampaignCreateBody(
        prompt="hello",
        repo="  git@x:a.git  ",
        repos=["git@x:a.git", "git@x:b.git", ""],
    )
    assert _request_repos(body) == ["git@x:a.git", "git@x:b.git"]


def test_campaign_name_uses_prompt_when_no_explicit_name():
    body = SpecCampaignCreateBody(prompt="   Build the thing please   ")
    assert _campaign_name(body) == "Build the thing please"


def test_campaign_name_uses_explicit_name_when_present():
    body = SpecCampaignCreateBody(prompt="hello", name="  My Title  ")
    assert _campaign_name(body) == "My Title"


def test_build_spec_prompt_without_repos_or_branch_or_context():
    body = SpecCampaignCreateBody(prompt="Do the spec work")
    prompt = _build_spec_prompt(body, repos=[])
    assert "Repository: none selected" in prompt
    assert "Branch:" not in prompt
    assert "Additional Context" not in prompt


def test_build_spec_prompt_with_branch_and_context():
    body = SpecCampaignCreateBody(prompt="Do the spec work", branch="dev", context="Extra notes")
    prompt = _build_spec_prompt(body, repos=["repo-a"])
    assert "Branch: dev" in prompt
    assert "Additional Context" in prompt
    assert "Extra notes" in prompt


async def test_reserve_spec_slug_increments_on_collision(tmp_path):
    workflow = _tagged_workflow(tmp_path, name="X")
    existing = _campaign_for_workflow(workflow, "specification")
    repo = InMemoryWorkflowCampaignRepository([existing])
    slug = await _reserve_spec_slug(repo, "Specification")
    assert slug == "specification-2"


class TestSyncPendingSpecGates:
    async def test_returns_campaign_unchanged_when_no_adapter(self, tmp_path):
        workflow = _spec_workflow(tmp_path)
        campaign = _campaign_for_workflow(workflow, "slug")
        repo = InMemoryWorkflowCampaignRepository([campaign])

        class _NoAdapterFactory:
            async def primary_for_principal(self, principal):
                return None

        result = await _sync_pending_spec_gates(
            campaign=campaign,
            repo=repo,
            volundr_factory=_NoAdapterFactory(),
            principal=_principal(),
            bearer_token=None,
        )
        assert result is campaign

    async def test_returns_campaign_unchanged_when_gates_match_stored(self, tmp_path):
        workflow = _spec_workflow(tmp_path)
        campaign = _campaign_for_workflow(workflow, "slug")
        repo = InMemoryWorkflowCampaignRepository([campaign])
        stored = _stored_spec_gates(campaign)

        class _MatchingPort:
            target_id = "local"
            name = "local"

            async def get_workflow_gates(self, *args, **kwargs):
                return stored

        class _Factory:
            async def primary_for_principal(self, principal):
                return _MatchingPort()

        result = await _sync_pending_spec_gates(
            campaign=campaign,
            repo=repo,
            volundr_factory=_Factory(),
            principal=_principal(),
            bearer_token=None,
        )
        assert result is campaign


def _principal():
    from niuu.domain.models import Principal

    return Principal("user-1", "", "tenant", ["volundr:developer"])


def test_stored_spec_gates_ignores_non_list_metadata(tmp_path):
    workflow = _spec_workflow(tmp_path)
    campaign = _campaign_for_workflow(workflow, "slug")
    broken = WorkflowCampaign(
        **{**campaign.__dict__, "metadata": {"pending_workflow_gates": "not-a-list"}}
    )
    assert _stored_spec_gates(broken) == []


def test_select_review_gate_filters_by_gate_id_and_node_id_then_falls_back():
    gates = [
        {"id": "g1", "node_id": "spec-prd-gate", "status": "pending"},
        {"id": "g2", "node_id": "spec-srd-gate", "status": "pending"},
    ]
    # gate_id present but matches nothing among real ids -> skip all, fallback to gates[0]
    assert _select_review_gate(gates, gate_id="does-not-exist", node_id=None) == gates[0]
    # node_id filters out the first, matches on the second
    assert _select_review_gate(gates, gate_id=None, node_id="spec-srd-gate") == gates[1]
    # exact match returns immediately
    assert _select_review_gate(gates, gate_id="g1", node_id=None) == gates[0]
    # empty gates -> None
    assert _select_review_gate([], gate_id=None, node_id=None) is None


class TestLoadSpecArtifacts:
    async def test_returns_empty_when_no_adapter(self, tmp_path):
        workflow = _tagged_workflow(tmp_path, name="X")
        bare = WorkflowDefinition(**{**workflow.__dict__, "graph": {"nodes": [], "edges": []}})
        campaign = _campaign_for_workflow(bare, "slug")
        campaign = WorkflowCampaign(
            **{**campaign.__dict__, "workflow_snapshot": build_workflow_snapshot(bare)}
        )
        settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
        artifacts, canonical = await _load_spec_artifacts(campaign, settings=settings)
        assert artifacts == []
        assert canonical == {}

    async def test_skips_pages_that_disappear_and_unclassified_kind_is_excluded_from_canonical(
        self, tmp_path
    ):
        now = datetime.now(UTC)
        present = MimirPage(
            meta=MimirPageMeta(
                path="specifications/slug/10-prd.md",
                title="PRD",
                summary="",
                category="specifications",
                updated_at=now,
            ),
            content="prd body",
        )
        unclassified = MimirPage(
            meta=MimirPageMeta(
                path="specifications/slug/99-notes.md",
                title="Notes",
                summary="",
                category="specifications",
                updated_at=now,
            ),
            content="notes body",
        )

        class _Adapter:
            async def list_pages(self, *, prefix):
                return [present.meta, unclassified.meta, _MissingMeta()]

            async def get_page(self, path):
                if path == "vanishes.md":
                    raise FileNotFoundError(path)
                return {present.meta.path: present, unclassified.meta.path: unclassified}[path]

            async def read_page(self, path):
                raise FileNotFoundError(path)

        class _MissingMeta:
            path = "vanishes.md"

        workflow = _spec_workflow(tmp_path)
        campaign = _campaign_for_workflow(workflow, "slug")

        import ting.api.specs as specs_module

        original = specs_module._campaign_knowledge
        specs_module._campaign_knowledge = lambda *a, **kw: _Adapter()
        try:
            artifacts, canonical = await _load_spec_artifacts(
                campaign, settings=Settings(auth=AuthConfig(allow_anonymous_dev=False))
            )
        finally:
            specs_module._campaign_knowledge = original

        paths = {artifact.path for artifact in artifacts}
        assert paths == {present.meta.path, unclassified.meta.path}
        assert "prd" in canonical
        assert None not in canonical


class TestSpecPublishedPaths:
    async def test_returns_empty_set_when_manifest_missing(self):
        class _Adapter:
            async def read_page(self, path):
                raise FileNotFoundError(path)

        assert await _spec_published_paths(_Adapter(), "slug") == set()

    async def test_parses_manifest_paths_and_adds_itself(self):
        class _Adapter:
            async def read_page(self, path):
                return "See specifications/slug/10-prd.md and specifications/slug/20-srd.md"

        published = await _spec_published_paths(_Adapter(), "slug")
        assert "specifications/slug/10-prd.md" in published
        assert "specifications/slug/50-manifest.md" in published


def test_spec_artifact_response_publish_states():
    now = datetime.now(UTC)
    published_page = MimirPage(
        meta=MimirPageMeta(
            path="specifications/slug/10-prd.md",
            title="",
            summary="",
            category="specifications",
            updated_at=now,
        ),
        content="",
    )
    published = _spec_artifact_response(
        published_page, {"specifications/slug/10-prd.md"}, manifest_known=True
    )
    assert published.publish_state == "published"
    assert published.title == "PRD"

    unpublished = _spec_artifact_response(published_page, set(), manifest_known=True)
    assert unpublished.publish_state == "unpublished"

    unknown = _spec_artifact_response(published_page, set(), manifest_known=False)
    assert unknown.publish_state == "unknown"


def test_classify_and_title_fallback_for_unrecognized_path():
    assert _classify_spec_artifact_kind("specifications/slug/99-random.md") is None
    assert _spec_title_from_path("specifications/slug/99-random-notes.md") == "99 Random Notes"


def test_spec_campaign_owns_path():
    assert _spec_campaign_owns_path("slug", "specifications/slug/10-prd.md") is True
    assert _spec_campaign_owns_path("slug", "specifications/other/10-prd.md") is False


class TestDeriveSpecStageState:
    def _snapshot(self, stage_ids_and_labels):
        return {
            "graph": {
                "nodes": [
                    {"id": stage_id, "kind": "stage", "label": label}
                    for stage_id, label in stage_ids_and_labels
                ]
            }
        }

    def test_pending_gate_blocks_matching_stage_and_returns_early(self):
        snapshot = self._snapshot(
            [("spec-frame", "Frame"), ("spec-prd", "Draft PRD"), ("spec-prd-review", "Review PRD")]
        )
        gates = [{"node_id": "spec-prd-gate", "status": "pending"}]
        derived = _derive_spec_stage_state(snapshot, [], WorkflowCampaignStatus.BLOCKED, [], gates)
        review = next(s for s in derived if s.stage_id == "spec-prd-review")
        assert review.status == "blocked"
        assert review.reason == "Review required"

    def test_first_incomplete_marked_active_blocked_or_failed(self):
        snapshot = self._snapshot([("spec-frame", "Frame")])
        active = _derive_spec_stage_state(snapshot, [], WorkflowCampaignStatus.RUNNING, [], [])
        assert active[0].status == "active"

        blocked = _derive_spec_stage_state(snapshot, [], WorkflowCampaignStatus.BLOCKED, [], [])
        assert blocked[0].status == "blocked"

        failed = _derive_spec_stage_state(snapshot, [], WorkflowCampaignStatus.FAILED, [], [])
        assert failed[0].status == "failed"

    def test_preserves_prior_started_at_for_completed_stage(self):
        from ting.api.research import CampaignArtifactResponse

        snapshot = self._snapshot([("spec-frame", "Frame")])
        now = datetime.now(UTC)
        artifacts = [
            CampaignArtifactResponse(
                path="specifications/slug/00-brief.md",
                title="Brief",
                updated_at=now,
                kind="brief",
                publish_state="published",
            )
        ]
        prior = [
            CampaignStageState(
                stage_id="spec-frame", label="Frame", status="active", started_at=now
            )
        ]
        derived = _derive_spec_stage_state(
            snapshot, artifacts, WorkflowCampaignStatus.RUNNING, prior, []
        )
        assert derived[0].status == "complete"
        assert derived[0].started_at == now


def test_stage_for_pending_gate_maps_known_ids_and_returns_none_otherwise():
    assert _stage_for_pending_gate([]) is None
    assert _stage_for_pending_gate([{"node_id": "spec-sdd-gate"}]) == "spec-sdd-review"
    assert _stage_for_pending_gate([{"node_id": "unrelated"}]) is None


def test_spec_stage_requirement_met_covers_each_stage_and_the_catch_all():
    kinds = {
        "brief",
        "prd",
        "prd_review",
        "srd",
        "srd_review",
        "sdd",
        "sdd_review",
        "breakdown",
        "breakdown_review",
        "manifest",
    }
    assert _spec_stage_requirement_met("spec-frame", "Frame", kinds, WorkflowCampaignStatus.RUNNING)
    assert _spec_stage_requirement_met(
        "spec-prd-review", "x", kinds, WorkflowCampaignStatus.RUNNING
    )
    assert _spec_stage_requirement_met("spec-prd", "x", kinds, WorkflowCampaignStatus.RUNNING)
    assert _spec_stage_requirement_met(
        "spec-srd-review", "x", kinds, WorkflowCampaignStatus.RUNNING
    )
    assert _spec_stage_requirement_met("spec-srd", "x", kinds, WorkflowCampaignStatus.RUNNING)
    assert _spec_stage_requirement_met(
        "spec-sdd-review", "x", kinds, WorkflowCampaignStatus.RUNNING
    )
    assert _spec_stage_requirement_met("spec-sdd", "x", kinds, WorkflowCampaignStatus.RUNNING)
    assert _spec_stage_requirement_met(
        "spec-breakdown-review", "x", kinds, WorkflowCampaignStatus.RUNNING
    )
    assert _spec_stage_requirement_met("spec-breakdown", "x", kinds, WorkflowCampaignStatus.RUNNING)
    assert _spec_stage_requirement_met("spec-publish", "x", kinds, WorkflowCampaignStatus.RUNNING)
    assert _spec_stage_requirement_met(
        "spec-done", "All Complete", set(), WorkflowCampaignStatus.COMPLETED
    )
    assert not _spec_stage_requirement_met(
        "spec-done", "All Complete", set(), WorkflowCampaignStatus.RUNNING
    )
    assert not _spec_stage_requirement_met(
        "unknown-stage", "Mystery", set(), WorkflowCampaignStatus.RUNNING
    )
