"""Tests for Ting specification campaign REST API."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.specs import create_specs_router
from ting.api.workflows import resolve_workflow_repo
from ting.config import AuthConfig, Settings
from ting.domain.models import (
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
