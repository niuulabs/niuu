"""Research campaigns whose workflow fans out launch as durable parent executions.

The bundled "Research Campaign" graph hands the framed question to a
``research-coordinator`` persona whose only tools are ``workflow_execution_*``.
Ravn refuses to start that persona without an owner-bound
``workflow_execution`` runtime context, and only a durable launch provides one.
``POST /research/campaigns`` used to launch every workflow plainly, so every
research campaign crashed the moment the frame stage handed over. These tests
pin that it now reserves an execution and launches the parent through the same
trusted path ``POST /workflow-executions`` uses — and that a workflow with no
fan-out keeps the plain launch and the same response shape.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from tests.test_ting.test_research_api import (
    InMemoryWorkflowCampaignRepository,
    InMemoryWorkflowRepository,
    RecordingVolundrFactory,
    RecordingVolundrPort,
    _headers,
    _make_client,
    _research_workflow,
)
from ting.api.workflow_execution_launch import durable_parent_node_id
from ting.api.workflow_executions import resolve_optional_workflow_execution_repo
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.system_workflows import load_system_workflows

_CAMPAIGNS_URL = "/api/v1/ting/research/campaigns"

#: Exactly what smidja sends when it starts a research campaign.
_SMIDJA_BODY = {
    "question": "Which laser-cut joinery holds up best in 1:48 board-and-batten kits?",
    "name": "Kit joinery survey",
    "audience": "kit designers",
    "constraints": ["Use public sources only"],
}


class RecordingExecutionRepository:
    """Records the reservation and parent attachment a durable launch makes."""

    def __init__(self) -> None:
        self.reserved = []
        self.execution = None

    async def reserve_parent_launch(self, execution):
        self.reserved.append(execution)
        self.execution = execution
        return execution, True

    async def attach_parent_session(self, execution_id, *, session_id, connection_id):
        assert self.execution is not None and self.execution.id == execution_id
        self.execution = replace(
            self.execution, parent_session_id=session_id, connection_id=connection_id
        )
        return self.execution


class RefusingExecutionRepository:
    async def reserve_parent_launch(self, execution):
        raise AssertionError("a workflow with no fan-out must not reserve an execution")


def _bundled_research_campaign() -> WorkflowDefinition:
    workflows = {workflow.name: workflow for workflow in load_system_workflows()}
    return workflows["Research Campaign"]


def _client(workflow, factory, execution_repo, campaign_repo=None):
    campaign_repo = campaign_repo or InMemoryWorkflowCampaignRepository()
    client = _make_client(InMemoryWorkflowRepository([workflow]), campaign_repo, factory)
    client.app.state.persona_source = FilesystemPersonaAdapter(
        persona_dirs=[], include_builtin=True
    )
    client.app.dependency_overrides[resolve_optional_workflow_execution_repo] = lambda: (
        execution_repo
    )
    return client, campaign_repo


def test_fan_out_research_launches_a_durable_parent_execution() -> None:
    workflow = _bundled_research_campaign()
    port = RecordingVolundrPort(name="valhalla", target_id="volundr-valhalla")
    executions = RecordingExecutionRepository()
    client, campaign_repo = _client(workflow, RecordingVolundrFactory(port), executions)

    response = client.post(
        _CAMPAIGNS_URL,
        headers=_headers(),
        json={**_SMIDJA_BODY, "workflowId": str(workflow.id)},
    )

    assert response.status_code == 201, response.text
    body = response.json()

    # One execution reserved for the graph's one subworkflow node.
    assert len(executions.reserved) == 1
    execution = executions.execution
    assert execution.parent_node_id == "research-threads"
    assert execution.policy.coordinator_id == "research-coordinator"
    assert execution.parent_session_id == body["sessionId"]
    assert execution.connection_id == "volundr-valhalla"
    assert execution.owner_id == "user-1"

    # The parent was launched with trusted provenance, so Forge hands the
    # coordinator's Ravn an owner-bound workflow_execution runtime context.
    assert len(port.requests) == 1
    workload = port.requests[0].workload_config
    provenance = workload["provenance"]
    assert provenance["workflow_execution_id"] == str(execution.id)
    assert provenance["workflow_execution"] == {
        "base_url": "http://testserver",
        "execution_id": str(execution.id),
        "parent_node_id": "research-threads",
        "parent_session_key": f"workflow:execution-{execution.id.hex}",
        "coordinator_id": "research-coordinator",
    }
    ravn_execution = workload["ravn_config"]["workflow_execution"]
    assert ravn_execution["execution_id"] == str(execution.id)
    assert ravn_execution["enabled"] is True
    assert "auth_token" not in ravn_execution
    assert "auth_token_file" not in ravn_execution

    # The campaign record is unchanged apart from the link to its execution.
    assert body["slug"] == "kit-joinery-survey"
    assert port.requests[0].name == "kit-joinery-survey"
    assert port.requests[0].tracker_issue_id == "workflow:kit-joinery-survey"
    assert body["metadata"]["surface"] == "ting.research"
    assert body["metadata"]["workflow_execution_id"] == str(execution.id)
    assert [stage["stageId"] for stage in body["stageState"]][:2] == [
        "research-frame",
        "research-coordinate",
    ]
    (stored,) = campaign_repo._campaigns.values()
    assert str(stored.id) == body["id"]
    assert stored.session_id == execution.parent_session_id
    assert stored.connection_id == "volundr-valhalla"
    assert stored.metadata["workflow_execution_id"] == str(execution.id)
    assert execution.launch_key.startswith("ting.research:")
    assert execution.launch_key.endswith(body["id"])


def test_fan_out_research_honours_the_pinned_connection() -> None:
    workflow = _bundled_research_campaign()
    noatun = RecordingVolundrPort(name="noatun", target_id="volundr-noatun")
    valhalla = RecordingVolundrPort(name="valhalla", target_id="volundr-valhalla")
    executions = RecordingExecutionRepository()
    client, _ = _client(workflow, RecordingVolundrFactory(noatun, valhalla), executions)

    response = client.post(
        _CAMPAIGNS_URL,
        headers=_headers(),
        json={
            **_SMIDJA_BODY,
            "workflowId": str(workflow.id),
            "connectionId": "volundr-valhalla",
        },
    )

    assert response.status_code == 201, response.text
    assert noatun.requests == []
    assert len(valhalla.requests) == 1
    assert executions.execution.connection_id == "volundr-valhalla"
    assert response.json()["metadata"]["connection_id"] == "volundr-valhalla"
    assert response.json()["metadata"]["cluster_name"] == "valhalla"


def test_fan_out_research_needs_the_execution_ledger() -> None:
    workflow = _bundled_research_campaign()
    port = RecordingVolundrPort()
    client, campaign_repo = _client(workflow, RecordingVolundrFactory(port), None)

    response = client.post(
        _CAMPAIGNS_URL,
        headers=_headers(),
        json={**_SMIDJA_BODY, "workflowId": str(workflow.id)},
    )

    assert response.status_code == 503, response.text
    assert port.requests == []
    assert campaign_repo._campaigns == {}


def test_research_without_fan_out_keeps_the_plain_launch(tmp_path: Path) -> None:
    """No subworkflow node and no coordinator tools: no execution, same response shape."""
    workflow = _research_workflow(tmp_path)
    port = RecordingVolundrPort()
    client, _ = _client(workflow, RecordingVolundrFactory(port), RefusingExecutionRepository())

    plain = client.post(_CAMPAIGNS_URL, headers=_headers(), json=_SMIDJA_BODY)

    assert plain.status_code == 201, plain.text
    workload = port.requests[0].workload_config
    assert "workflow_execution" not in workload.get("provenance", {})
    assert "workflow_execution" not in workload.get("ravn_config", {})
    assert "workflow_execution_id" not in plain.json()["metadata"]

    # The durable launch answers with exactly the same fields smidja reads.
    fan_out = _bundled_research_campaign()
    durable_port = RecordingVolundrPort()
    durable_client, _ = _client(
        fan_out, RecordingVolundrFactory(durable_port), RecordingExecutionRepository()
    )
    durable = durable_client.post(
        _CAMPAIGNS_URL,
        headers=_headers(),
        json={**_SMIDJA_BODY, "workflowId": str(fan_out.id)},
    )
    assert durable.status_code == 201, durable.text
    assert set(durable.json()) == set(plain.json())
    for field in ("slug", "sessionId", "chatEndpoint"):
        assert field in durable.json()
    assert set(durable.json()["metadata"]) == set(plain.json()["metadata"]) | {
        "workflow_execution_id"
    }


def _workflow_with_nodes(nodes: list[dict]) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Probe",
        description="",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={"nodes": nodes, "edges": []},
        created_at=now,
        updated_at=now,
        schema_version=2,
    )


def _snapshot_with_tools(tools: list[str]) -> dict:
    return {
        "persona_definitions": {
            "coordinator": {"id": "coordinator", "definition": {"allowed_tools": tools}}
        }
    }


def test_durable_parent_node_is_the_single_subworkflow_node() -> None:
    workflow = _workflow_with_nodes(
        [{"id": "stage", "kind": "stage"}, {"id": "fan-out", "kind": "subworkflow"}]
    )
    assert durable_parent_node_id(workflow, {}) == "fan-out"


def test_durable_parent_node_is_none_without_fan_out_or_coordinator_tools() -> None:
    workflow = _workflow_with_nodes([{"id": "stage", "kind": "stage"}])
    assert durable_parent_node_id(workflow, _snapshot_with_tools(["mimir_read"])) is None
    assert durable_parent_node_id(workflow, None) is None


def test_coordinator_tools_without_a_subworkflow_node_are_rejected() -> None:
    workflow = _workflow_with_nodes([{"id": "stage", "kind": "stage"}])
    with pytest.raises(HTTPException) as caught:
        durable_parent_node_id(workflow, _snapshot_with_tools(["workflow_execution_expand"]))
    assert caught.value.status_code == 422


def test_several_subworkflow_nodes_are_rejected() -> None:
    workflow = _workflow_with_nodes(
        [{"id": "one", "kind": "subworkflow"}, {"id": "two", "kind": "subworkflow"}]
    )
    with pytest.raises(HTTPException) as caught:
        durable_parent_node_id(workflow, {})
    assert caught.value.status_code == 422
