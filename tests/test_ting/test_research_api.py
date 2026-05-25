"""Tests for Ting research campaign REST API."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import create_research_router, resolve_workflow_campaign_repo
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
from ting.ports.volundr import ActivityEvent, SpawnRequest, VolundrPort, VolundrSession
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository


class InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition]) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows}

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        workflows = list(self._workflows.values())
        if scope == WorkflowScope.SYSTEM:
            return [workflow for workflow in workflows if workflow.scope == WorkflowScope.SYSTEM]
        if scope == WorkflowScope.USER:
            return [
                workflow
                for workflow in workflows
                if workflow.scope == WorkflowScope.USER and workflow.owner_id == owner_id
            ]
        return [
            workflow
            for workflow in workflows
            if workflow.scope == WorkflowScope.SYSTEM or workflow.owner_id == owner_id
        ]

    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        removed = self._workflows.pop(workflow_id, None)
        return removed is not None


class InMemoryWorkflowCampaignRepository(WorkflowCampaignRepository):
    def __init__(self, campaigns: list[WorkflowCampaign] | None = None) -> None:
        self._campaigns = {campaign.id: campaign for campaign in campaigns or []}

    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        return sorted(
            [campaign for campaign in self._campaigns.values() if campaign.owner_id == owner_id],
            key=lambda campaign: campaign.updated_at,
            reverse=True,
        )

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        return [
            campaign
            for campaign in self._campaigns.values()
            if campaign.status
            in {
                WorkflowCampaignStatus.PENDING,
                WorkflowCampaignStatus.RUNNING,
                WorkflowCampaignStatus.BLOCKED,
            }
        ]

    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        return self._campaigns.get(campaign_id)

    async def get_campaign_by_slug(
        self,
        slug: str,
        *,
        owner_id: str | None = None,
    ) -> WorkflowCampaign | None:
        for campaign in self._campaigns.values():
            if campaign.slug != slug:
                continue
            if owner_id is not None and campaign.owner_id != owner_id:
                continue
            return campaign
        return None

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        self._campaigns[campaign.id] = campaign
        return campaign

    async def delete_campaign(self, campaign_id: UUID) -> bool:
        removed = self._campaigns.pop(campaign_id, None)
        return removed is not None


class RecordingVolundrPort(VolundrPort):
    def __init__(self) -> None:
        self.requests: list[SpawnRequest] = []
        self.sessions: dict[str, VolundrSession] = {}

    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession:
        session = VolundrSession(
            id=f"session-{len(self.requests) + 1}",
            name=request.name,
            status="running",
            tracker_issue_id=request.tracker_issue_id,
            cluster_name="local",
            repo=request.repo,
            branch=request.branch,
            base_branch=request.base_branch,
            workload_type=request.workload_type,
        )
        self.requests.append(request)
        self.sessions[session.id] = session
        return session

    async def get_session(self, session_id: str, *, auth_token=None, principal=None):
        return self.sessions.get(session_id)

    async def list_sessions(self, *, auth_token=None, principal=None):
        return list(self.sessions.values())

    async def get_pr_status(self, session_id: str):
        raise NotImplementedError

    async def get_chronicle_summary(self, session_id: str) -> str:
        raise NotImplementedError

    async def send_message(self, session_id: str, message: str, *, auth_token=None, principal=None):
        raise NotImplementedError

    async def stop_session(self, session_id: str, *, auth_token=None, principal=None) -> None:
        raise NotImplementedError

    async def list_integration_ids(self, *, auth_token=None, principal=None):
        return []

    async def list_repos(self, *, auth_token=None, principal=None):
        return []

    async def get_last_assistant_message(self, session_id: str) -> str:
        raise NotImplementedError

    async def get_conversation(self, session_id: str) -> dict:
        raise NotImplementedError

    async def subscribe_activity(self):
        if False:
            yield ActivityEvent(session_id="", state="", metadata={}, owner_id="")


class RecordingVolundrFactory:
    def __init__(self, port: RecordingVolundrPort) -> None:
        self._port = port

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        return [self._port]

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        return self._port

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        return [self._port]


def _headers() -> dict[str, str]:
    return {
        "x-auth-user-id": "user-1",
        "x-auth-roles": "product:user",
    }


def _research_workflow(root: Path) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Research Campaign",
        description="Research workflow",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["research", "campaign"],
            "nodes": [
                {"id": "trigger-1", "kind": "trigger", "dispatchEvent": "research.requested"},
                {
                    "id": "memory",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "mount_name": "local",
                    "path": str(root),
                },
                {
                    "id": "frame",
                    "kind": "stage",
                    "label": "Frame the inquiry",
                    "stageMembers": [
                        {
                            "personaId": "research-framer",
                            "budget": 12,
                            "model": "gpt-5.5",
                            "consumesEventTypes": ["research.requested"],
                        }
                    ],
                },
                {
                    "id": "explore",
                    "kind": "stage",
                    "label": "Explore the evidence",
                    "stageMembers": [{"personaId": "research-explorer", "budget": 18}],
                },
                {
                    "id": "challenge",
                    "kind": "stage",
                    "label": "Challenge the thesis",
                    "stageMembers": [{"personaId": "research-skeptic", "budget": 8}],
                },
                {
                    "id": "publish",
                    "kind": "stage",
                    "label": "Publish to Mimir",
                    "stageMembers": [{"personaId": "research-publisher", "budget": 8}],
                },
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "frame"},
                {"id": "e2", "source": "frame", "target": "explore"},
                {"id": "e3", "source": "explore", "target": "challenge"},
                {"id": "e4", "source": "challenge", "target": "publish"},
            ],
            "resourceBindings": [
                {
                    "id": "binding-1",
                    "resourceNodeId": "memory",
                    "targetType": "workflow",
                    "targetId": "research-campaign",
                    "access": "read_write",
                    "writePrefixes": ["research/", "learnings/", "followups/"],
                    "readPriority": 1,
                }
            ],
        },
        created_at=now,
        updated_at=now,
    )


def _non_research_workflow(root: Path) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Delivery Workflow",
        description="Delivery workflow",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["delivery"],
            "nodes": [
                {"id": "trigger-1", "kind": "trigger", "dispatchEvent": "delivery.requested"},
                {
                    "id": "memory",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "mount_name": "local",
                    "path": str(root),
                },
                {
                    "id": "plan",
                    "kind": "stage",
                    "label": "Plan",
                    "stageMembers": [{"personaId": "planner", "budget": 6}],
                },
            ],
            "edges": [],
            "resourceBindings": [],
        },
        created_at=now,
        updated_at=now,
    )


def _make_client(
    workflow_repo: WorkflowRepository,
    campaign_repo: WorkflowCampaignRepository,
    volundr_factory: RecordingVolundrFactory,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_research_router())
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.event_bus = InMemoryEventBus()
    app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    app.dependency_overrides[resolve_volundr_factory] = lambda: volundr_factory
    return TestClient(app)


def test_create_campaign_launches_workflow_and_persists_record(tmp_path: Path) -> None:
    workflow = _research_workflow(tmp_path)
    workflow_repo = InMemoryWorkflowRepository([workflow])
    campaign_repo = InMemoryWorkflowCampaignRepository()
    volundr_port = RecordingVolundrPort()
    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(volundr_port))

    response = client.post(
        "/api/v1/ting/research/campaigns",
        headers=_headers(),
        json={
            "question": "Map the competitive landscape for retrieval-augmented generation tools.",
            "mode": "exploratory",
            "audience": "product leadership",
            "deliverable": "briefing memo",
            "success": "clear competitors and differentiation",
            "constraints": ["Use public sources only"],
            "repo": "niuulabs/volundr",
            "branch": "dev",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workflowName"] == "Research Campaign"
    assert body["status"] == "running"
    assert len(volundr_port.requests) == 1
    assert volundr_port.requests[0].tracker_issue_id.startswith("workflow:")

    campaigns = client.get("/api/v1/ting/research/campaigns", headers=_headers()).json()
    assert len(campaigns) == 1
    assert campaigns[0]["slug"] == body["slug"]
    assert campaigns[0]["metadata"]["mode"] == "exploratory"


def test_create_campaign_normalizes_long_session_names_for_volundr(tmp_path: Path) -> None:
    workflow = _research_workflow(tmp_path)
    workflow_repo = InMemoryWorkflowRepository([workflow])
    campaign_repo = InMemoryWorkflowCampaignRepository()
    volundr_port = RecordingVolundrPort()
    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(volundr_port))

    response = client.post(
        "/api/v1/ting/research/campaigns",
        headers=_headers(),
        json={
            "question": (
                "Should Ting add a dedicated Research Center tab for workflow-backed research "
                "experiences that keep artifacts visible end-to-end?"
            ),
            "mode": "evaluative",
            "audience": "platform",
            "deliverable": "memo",
            "success": "clear recommendation",
            "constraints": ["avoid tracker coupling"],
            "repo": "",
            "branch": "dev",
        },
    )

    assert response.status_code == 201
    assert len(volundr_port.requests) == 1
    assert volundr_port.requests[0].name.endswith(tuple("abcdefghijklmnopqrstuvwxyz0123456789"))
    assert "-" in volundr_port.requests[0].name
    assert response.json()["name"].startswith("Should Ting add a dedicated Research Center tab")


def test_create_campaign_prefers_research_tagged_workflow_by_default(tmp_path: Path) -> None:
    delivery_workflow = _non_research_workflow(tmp_path)
    research_workflow = _research_workflow(tmp_path)
    workflow_repo = InMemoryWorkflowRepository([delivery_workflow, research_workflow])
    campaign_repo = InMemoryWorkflowCampaignRepository()
    volundr_port = RecordingVolundrPort()
    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(volundr_port))

    response = client.post(
        "/api/v1/ting/research/campaigns",
        headers=_headers(),
        json={
            "question": "Should research launches default to workflows tagged research?",
            "mode": "evaluative",
            "repo": "niuulabs/volundr",
            "branch": "dev",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workflowId"] == str(research_workflow.id)
    assert body["workflowName"] == "Research Campaign"


def test_detail_reads_artifacts_from_mimir_and_uses_manifest_for_publish_state(
    tmp_path: Path,
) -> None:
    workflow = _research_workflow(tmp_path)
    workflow_repo = InMemoryWorkflowRepository([workflow])
    now = datetime.now(UTC)
    campaign = WorkflowCampaign(
        id=uuid4(),
        slug="retrieval-landscape",
        name="Retrieval landscape",
        owner_id="user-1",
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_name=workflow.name,
        workflow_snapshot=build_workflow_snapshot(workflow),
        session_id="session-1",
        session_name="Retrieval landscape",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id="frame",
        stage_state=[
            CampaignStageState(stage_id="frame", label="Frame the inquiry", status="active"),
            CampaignStageState(
                stage_id="explore",
                label="Explore the evidence",
                status="pending",
            ),
            CampaignStageState(
                stage_id="challenge",
                label="Challenge the thesis",
                status="pending",
            ),
            CampaignStageState(stage_id="publish", label="Publish to Mimir", status="pending"),
        ],
        metadata={"question": "What does the retrieval tooling market look like?"},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
    )
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    volundr_port = RecordingVolundrPort()
    volundr_port.sessions["session-1"] = VolundrSession(
        id="session-1",
        name="Retrieval landscape",
        status="running",
        tracker_issue_id="workflow:retrieval-landscape",
        cluster_name="local",
        repo="niuulabs/volundr",
        branch="dev",
        base_branch="",
        workload_type="ravn_flock",
    )

    wiki_root = tmp_path / "wiki"
    research_dir = wiki_root / "research" / "campaigns" / "retrieval-landscape"
    research_dir.mkdir(parents=True)
    (research_dir / "brief.md").write_text("# Brief\n\nOne line.", encoding="utf-8")
    (research_dir / "final.md").write_text("# Final\n\nAnswer.", encoding="utf-8")
    (research_dir / "manifest.md").write_text(
        "\n".join(
            [
                "# Manifest",
                "- research/campaigns/retrieval-landscape/final.md",
                "- research/campaigns/retrieval-landscape/manifest.md",
            ]
        ),
        encoding="utf-8",
    )
    learnings_dir = wiki_root / "learnings" / "research"
    learnings_dir.mkdir(parents=True)
    (learnings_dir / "retrieval-landscape.md").write_text(
        "# Learnings\n\nTakeaways.",
        encoding="utf-8",
    )

    client = _make_client(workflow_repo, campaign_repo, RecordingVolundrFactory(volundr_port))
    response = client.get(
        "/api/v1/ting/research/campaigns/retrieval-landscape",
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    paths = {artifact["path"]: artifact for artifact in body["artifacts"]}
    assert "research/campaigns/retrieval-landscape/final.md" in paths
    assert "research/campaigns/retrieval-landscape/brief.md" in paths
    assert "learnings/research/retrieval-landscape.md" in paths
    assert paths["research/campaigns/retrieval-landscape/final.md"]["publishState"] == "published"
    assert paths["research/campaigns/retrieval-landscape/brief.md"]["publishState"] == "unpublished"
    assert body["canonicalArtifacts"]["final"] == "research/campaigns/retrieval-landscape/final.md"
    assert any(stage["status"] == "complete" for stage in body["stageState"])


def test_artifact_endpoint_rejects_paths_outside_campaign(tmp_path: Path) -> None:
    workflow = _research_workflow(tmp_path)
    workflow_repo = InMemoryWorkflowRepository([workflow])
    now = datetime.now(UTC)
    campaign = WorkflowCampaign(
        id=uuid4(),
        slug="research-one",
        name="Research one",
        owner_id="user-1",
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_name=workflow.name,
        workflow_snapshot=build_workflow_snapshot(workflow),
        session_id="session-1",
        session_name="Research one",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id="frame",
        stage_state=[],
        metadata={},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
    )
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        workflow_repo,
        campaign_repo,
        RecordingVolundrFactory(RecordingVolundrPort()),
    )

    response = client.get(
        "/api/v1/ting/research/campaigns/research-one/artifact",
        headers=_headers(),
        params={"path": "research/campaigns/other/final.md"},
    )

    assert response.status_code == 404


def test_delete_campaign_removes_record(tmp_path: Path) -> None:
    workflow = _research_workflow(tmp_path)
    workflow_repo = InMemoryWorkflowRepository([workflow])
    now = datetime.now(UTC)
    campaign = WorkflowCampaign(
        id=uuid4(),
        slug="delete-me",
        name="Delete me",
        owner_id="user-1",
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_name=workflow.name,
        workflow_snapshot=build_workflow_snapshot(workflow),
        session_id="session-delete",
        session_name="Delete me",
        status=WorkflowCampaignStatus.PENDING,
        active_stage_id="frame",
        stage_state=[],
        metadata={"question": "Delete this test campaign"},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
    )
    campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
    client = _make_client(
        workflow_repo,
        campaign_repo,
        RecordingVolundrFactory(RecordingVolundrPort()),
    )

    response = client.delete("/api/v1/ting/research/campaigns/delete-me", headers=_headers())

    assert response.status_code == 204
    assert client.get("/api/v1/ting/research/campaigns", headers=_headers()).json() == []
