"""Research campaigns whose workflow fans out launch as durable parent executions.

The bundled "Research Campaign" graph hands the framed question to a
``research-coordinator`` persona whose only tools are ``workflow_execution_*``.
Ravn refuses to start that persona without an owner-bound
``workflow_execution`` runtime context, and only a durable launch provides one.
``POST /research/campaigns`` used to launch every workflow plainly, so every
research campaign crashed the moment the frame stage handed over. These tests
pin that it reserves an execution and launches the parent through the same
trusted path ``POST /workflow-executions`` uses: the execution takes the campaign's id,
the campaign's run is the parent, and the execution is marked running. A workflow with no
fan-out launches plainly and reserves no execution.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from personas.adapters.loader import FilesystemPersonaAdapter
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
from ting.domain.workflow_execution import ExecutionState
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

    async def get_internal(self, execution_id):
        assert self.execution is not None and self.execution.id == execution_id
        return self.execution

    async def update_execution_state(
        self, execution_id, *, state, suspension_reason, expected_revision
    ):
        assert self.execution is not None and self.execution.id == execution_id
        assert expected_revision == self.execution.revision
        self.execution = replace(
            self.execution, state=ExecutionState(state), suspension_reason=suspension_reason
        )


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


def test_fan_out_research_starts_its_parent_run_under_the_execution() -> None:
    workflow = _bundled_research_campaign()
    port = RecordingVolundrPort(name="valhalla", target_id="volundr-valhalla")
    executions = RecordingExecutionRepository()
    client, campaign_repo = _client(workflow, RecordingVolundrFactory(port), executions)

    response = client.post(
        _CAMPAIGNS_URL,
        headers=_headers(),
        json={**_SMIDJA_BODY, "workflowId": str(workflow.id), "connectionId": "volundr-valhalla"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sessionId"] == ""
    assert body["metadata"]["workflow_execution_id"] == body["id"]
    assert port.requests == []
    [reserved] = executions.reserved
    assert str(reserved.id) == body["id"]
    assert not reserved.parent_session_id
    assert executions.execution.state is ExecutionState.RUNNING
    assert executions.execution.suspension_reason == ""
    [(campaign, payload)] = client.app.state.workflow_runs.launched
    assert str(campaign.id) == body["id"]
    assert campaign.connection_id == "volundr-valhalla"
    assert "workflow_execution" in payload["context"]
    assert list(campaign_repo._campaigns) == [campaign.id]


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


def test_research_without_fan_out_launches_without_reserving_an_execution(
    tmp_path: Path,
) -> None:
    """No subworkflow node: no execution is reserved, and the run starts plainly."""
    workflow = _research_workflow(tmp_path)
    port = RecordingVolundrPort()
    client, campaign_repo = _client(
        workflow, RecordingVolundrFactory(port), RefusingExecutionRepository()
    )

    response = client.post(_CAMPAIGNS_URL, headers=_headers(), json=_SMIDJA_BODY)

    assert response.status_code == 201, response.text
    assert "workflow_execution_id" not in response.json()["metadata"]
    assert port.requests == []
    assert len(client.app.state.workflow_runs.launched) == 1
    assert len(campaign_repo._campaigns) == 1


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
