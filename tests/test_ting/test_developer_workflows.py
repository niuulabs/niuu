from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from personas.adapters.loader import FilesystemPersonaAdapter
from ting.adapters.parent_workflow_continuation import VolundrParentWorkflowContinuation
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDependency
from ting.domain.workflow_document import (
    load_workflow_document,
    workflow_document_revision,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.domain.workflow_wait import (
    WaitObservation,
    WaitObservationStatus,
    WorkflowWait,
    WorkflowWaitState,
)
from ting.system_workflows import BUNDLED_SYSTEM_WORKFLOWS_PATH, load_system_workflows


def _developer_workflows():
    return {
        workflow.name: workflow
        for workflow in load_system_workflows()
        if workflow.name.startswith("Developer ")
    }


def test_developer_workflow_package_has_exact_transitive_closure() -> None:
    workflows = _developer_workflows()
    assert set(workflows) == {
        "Developer Delivery",
        "Developer Planning",
        "Developer Workstream",
        "Developer Integration",
    }

    root = workflows["Developer Delivery"]
    assert root.schema_version == 2
    assert root.graph["executionContract"] == "developer-delivery/v1"
    assert set(root.workflow_dependencies) == {"workstream", "planning", "integration"}
    snapshot = build_workflow_snapshot(root)
    assert set(snapshot["workflow_definitions"]) == set(root.workflow_dependencies)
    for alias, pin in root.workflow_dependencies.items():
        child = snapshot["workflow_definitions"][alias]
        document = load_workflow_document(
            __import__("yaml").safe_dump(child["document"], sort_keys=False)
        )
        assert document.id == pin.id
        assert workflow_document_revision(document) == pin.revision == pin.digest


def test_developer_workflows_pin_current_integration_reviewer_content() -> None:
    reviewer = FilesystemPersonaAdapter(
        persona_dirs=[], include_builtin=True
    ).load_current_portable("developer-integration-verifier")
    assert reviewer is not None

    workflows = _developer_workflows()
    for workflow_name in ("Developer Delivery", "Developer Integration"):
        pin = workflows[workflow_name].persona_dependencies["developer-integration-verifier"]
        assert pin.revision == reviewer.revision
        assert pin.digest == reviewer.digest


def test_developer_personas_and_templates_are_provider_neutral_and_coordinator_is_bounded() -> None:
    files = [
        *Path("src/personas/builtin").glob("developer-*.yaml"),
        *BUNDLED_SYSTEM_WORKFLOWS_PATH.glob("developer-*.yaml"),
    ]
    rendered = "\n".join(path.read_text(encoding="utf-8").casefold() for path in files)
    assert "github" not in rendered
    assert "gitlab" not in rendered

    coordinator = Path("src/personas/builtin/developer-coordinator.yaml").read_text(
        encoding="utf-8"
    )
    for tool in (
        "delivery_expand_workstreams",
        "workflow_execution_reconcile",
        "workflow_execution_message",
        "workflow_execution_cancel",
        "delivery_complete",
        "delivery_record_integration",
        "workflow_execution_wait",
        "delivery_workspace",
        "delivery_forge",
        "delivery_evidence",
    ):
        assert f"- {tool}" in coordinator
    for forbidden in ("terminal", "bash", "file_write", "file_edit"):
        assert f"- {forbidden}" in coordinator

    coordinator_persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-coordinator"
    )
    assert coordinator_persona is not None
    handoff_fields = {
        "attempt_id": "string",
        "candidate_sha": "string",
        "candidate_tree": "string",
        "integration_allocation": "object",
        "integration_candidate": "object",
        "integration_receipts": "array",
        "integration_receipt": "object",
        "verification_receipts": "array",
    }
    for field_name, field_type in handoff_fields.items():
        field = coordinator_persona.produces.schema[field_name]
        assert field.type == field_type
        assert field.required is False
    for field_name in ("plan_revision", "generation", "workstreams", "task_handles"):
        assert coordinator_persona.produces.schema[field_name].required is False
    for field_name in ("verdict", "rationale", "result"):
        assert coordinator_persona.produces.schema[field_name].required is True
    assert "Copy these typed objects unchanged" in coordinator_persona.system_prompt_template
    assert "expand current_generation + 1" in coordinator_persona.system_prompt_template
    assert "Completed children are immutable evidence" in coordinator_persona.system_prompt_template
    assert "fresh generation-qualified integration allocation" in (
        coordinator_persona.system_prompt_template
    )
    assert "emit wait_delivery" in coordinator_persona.system_prompt_template
    assert "workflow_execution_wait with" in coordinator_persona.system_prompt_template
    assert "conditionType set to forge.checks or forge.merge" in (
        coordinator_persona.system_prompt_template
    )

    integration_reviewer = Path(
        "src/personas/builtin/developer-integration-verifier.yaml"
    ).read_text(encoding="utf-8")
    assert "delivery_workspace.inspect" in integration_reviewer
    assert "bounded exact-candidate patch" in integration_reviewer
    assert "allowed_tools: [delivery_workspace]" in integration_reviewer
    assert "delivery_forge, delivery_evidence" in integration_reviewer
    assert "final delivery evidence policy" in integration_reviewer
    assert "Remote CI and full-policy" in integration_reviewer


@pytest.mark.parametrize(
    "workflow_name",
    ["developer-delivery.yaml", "developer-workstream.yaml", "developer-integration.yaml"],
)
def test_developer_workflows_pin_current_coordinator_content(workflow_name: str) -> None:
    source = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    coordinator = source.load_current_portable("developer-coordinator")
    assert coordinator is not None
    workflow = load_workflow_document(
        (BUNDLED_SYSTEM_WORKFLOWS_PATH / workflow_name).read_text(encoding="utf-8")
    )

    pin = workflow.persona_dependencies["developer-coordinator"]
    assert pin.revision == coordinator.revision
    assert pin.digest == coordinator.digest


def test_delivery_pins_current_workstream_content() -> None:
    root = load_workflow_document(
        (BUNDLED_SYSTEM_WORKFLOWS_PATH / "developer-delivery.yaml").read_text(encoding="utf-8")
    )
    child = load_workflow_document(
        (BUNDLED_SYSTEM_WORKFLOWS_PATH / "developer-workstream.yaml").read_text(encoding="utf-8")
    )

    revision = workflow_document_revision(child)
    pin = root.workflow_dependencies["workstream"]
    assert pin.revision == revision
    assert pin.digest == revision

    expansion = next(node for node in root.graph["nodes"] if node["kind"] == "subworkflow")
    input_schema = expansion["inputSchema"]
    assert "expectedOutputs" in input_schema["required"]
    assert input_schema["properties"]["expectedOutputs"] == {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
    }
    requirement_evidence = expansion["resultSchema"]["properties"]["requirementEvidence"]
    assert requirement_evidence == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "requirement_id": {"type": "string", "minLength": 1},
                "implementation_paths": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
                "verification_contract_ids": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
            },
            "required": [
                "requirement_id",
                "implementation_paths",
                "verification_contract_ids",
            ],
            "additionalProperties": False,
        },
    }
    verification_receipt = expansion["resultSchema"]["properties"]["verificationReceipts"]["items"]
    receipt_fields = {
        "receipt_id",
        "campaign_id",
        "workstream_key",
        "attempt_id",
        "repository",
        "candidate_sha",
        "candidate_tree",
        "base_sha",
        "contract_id",
        "command_digest",
        "exit_code",
        "stdout_digest",
        "stderr_digest",
        "started_at",
        "completed_at",
        "provenance",
    }
    assert set(verification_receipt["properties"]) == receipt_fields
    assert set(verification_receipt["required"]) == receipt_fields
    assert verification_receipt["additionalProperties"] is False
    assert verification_receipt["properties"]["exit_code"] == {"type": "integer"}
    for field_name in receipt_fields - {"exit_code", "provenance"}:
        assert verification_receipt["properties"][field_name] == {
            "type": "string",
            "minLength": 1,
        }
    assert verification_receipt["properties"]["provenance"] == {
        "type": "object",
        "properties": {
            "producer_id": {"type": "string", "minLength": 1},
            "key_id": {"type": "string", "minLength": 1},
            "signature": {"type": "string", "minLength": 1},
        },
        "required": ["producer_id", "key_id", "signature"],
        "additionalProperties": False,
    }


def test_workflow_revision_ignores_transport_paths_for_both_dependency_kinds() -> None:
    document = load_workflow_document(
        (BUNDLED_SYSTEM_WORKFLOWS_PATH / "developer-delivery.yaml").read_text()
    )
    changed = copy.deepcopy(document)
    changed.persona_dependencies["developer-coordinator"] = replace(
        changed.persona_dependencies["developer-coordinator"],
        path="personas/elsewhere.yaml",
    )
    changed.workflow_dependencies["workstream"] = replace(
        changed.workflow_dependencies["workstream"],
        path="workflows/elsewhere.yaml",
    )
    assert workflow_document_revision(changed) == workflow_document_revision(document)


@pytest.mark.parametrize("mutation", ["missing", "extra", "tamper", "cycle"])
def test_execution_snapshot_rejects_invalid_workflow_dependency_closure(mutation: str) -> None:
    root = _developer_workflows()["Developer Delivery"]
    definitions = copy.deepcopy(root.workflow_definitions)
    dependencies = dict(root.workflow_dependencies)
    if mutation == "missing":
        definitions.pop("workstream")
    elif mutation == "extra":
        definitions["undeclared"] = copy.deepcopy(definitions["workstream"])
    elif mutation == "tamper":
        definitions["workstream"]["document"]["description"] += " altered"
    else:
        definitions["workstream"]["document"]["id"] = str(root.id)
        child = load_workflow_document(
            __import__("yaml").safe_dump(definitions["workstream"]["document"], sort_keys=False)
        )
        revision = workflow_document_revision(child)
        dependencies["workstream"] = WorkflowDependency(
            id=root.id,
            revision=revision,
            digest=revision,
            path="workflows/developer-planning.yaml",
        )

    with pytest.raises(WorkflowDocumentError):
        build_workflow_snapshot(
            replace(
                root,
                workflow_dependencies=dependencies,
                workflow_definitions=definitions,
            )
        )


def _subworkflow_snapshot() -> dict:
    """A minimal pinned graph naming the joined/blocked events for one node.

    Deliberately uses event names distinct from any bundled workflow's own
    vocabulary, to prove the continuation adapter reads them from the graph
    rather than assuming a fixed name.
    """
    return {
        "graph": {
            "nodes": [
                {
                    "id": "delivery-workstreams",
                    "kind": "subworkflow",
                    "blockedEvent": "workstreams.blocked",
                },
            ],
            "edges": [
                {
                    "id": "workstreams-joined",
                    "source": "delivery-workstreams",
                    "target": "delivery-integrate",
                    "label": "workstreams.verified -> workstreams.verified",
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_verified_children_resume_parent_as_deterministic_mesh_event() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.calls = []

        async def publish_workflow_event(self, *args, **kwargs) -> None:
            self.calls.append((args, kwargs))

    adapter = Adapter()

    class Factory:
        async def primary_for_owner(self, owner_id):
            assert owner_id == "owner-1"
            return adapter

        async def for_connection(self, owner_id, connection_id):
            raise AssertionError("no explicit connection expected")

    execution_id = uuid4()
    execution = SimpleNamespace(
        id=execution_id,
        owner_id="owner-1",
        connection_id="",
        parent_session_id="session-1",
        parent_node_id="delivery-workstreams",
        workflow_snapshot=_subworkflow_snapshot(),
    )
    continuation = VolundrParentWorkflowContinuation(volundr_factory=Factory())

    await continuation.resume_parent(
        execution,
        generation=2,
        results=[{"childKey": "api", "candidateSha": "a" * 40}],
    )

    assert len(adapter.calls) == 1
    args, kwargs = adapter.calls[0]
    assert args[:2] == ("session-1", "workstreams.verified")
    assert kwargs["payload"]["parentNodeId"] == "delivery-workstreams"
    assert kwargs["payload"]["generation"] == 2
    assert kwargs["request_id"] == kwargs["payload"]["continuationId"]


@pytest.mark.asyncio
async def test_parent_continuation_never_falls_back_from_explicit_connection() -> None:
    class Factory:
        async def primary_for_owner(self, owner_id):
            raise AssertionError("explicit connection must not fall back to primary")

        async def for_connection(self, owner_id, connection_id):
            assert (owner_id, connection_id) == ("owner-1", "connection-1")
            return None

    execution = SimpleNamespace(
        id=uuid4(),
        owner_id="owner-1",
        connection_id="connection-1",
        parent_session_id="session-1",
        parent_node_id="delivery-workstreams",
    )
    continuation = VolundrParentWorkflowContinuation(volundr_factory=Factory())

    with pytest.raises(RuntimeError, match="connection is unavailable"):
        await continuation.resume_parent(execution, generation=1, results=[])


@pytest.mark.asyncio
async def test_parent_continuation_stops_exact_session_through_existing_connection() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.stopped = []

        async def stop_session(self, session_id) -> None:
            self.stopped.append(session_id)

    adapter = Adapter()

    class Factory:
        async def primary_for_owner(self, owner_id):
            raise AssertionError("explicit connection must not fall back to primary")

        async def for_connection(self, owner_id, connection_id):
            assert (owner_id, connection_id) == ("owner-1", "connection-1")
            return adapter

    execution = SimpleNamespace(
        id=uuid4(),
        owner_id="owner-1",
        connection_id="connection-1",
        parent_session_id="session-1",
    )
    continuation = VolundrParentWorkflowContinuation(volundr_factory=Factory())

    await continuation.stop_parent(execution)

    assert adapter.stopped == ["session-1"]


@pytest.mark.asyncio
async def test_blocked_children_notify_parent_with_stable_correlation() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.calls = []

        async def publish_workflow_event(self, *args, **kwargs) -> None:
            self.calls.append((args, kwargs))

    adapter = Adapter()

    class Factory:
        async def primary_for_owner(self, owner_id):
            return adapter

    execution = SimpleNamespace(
        id=uuid4(),
        owner_id="owner-1",
        connection_id="",
        parent_session_id="session-1",
        parent_node_id="delivery-workstreams",
        workflow_snapshot=_subworkflow_snapshot(),
    )
    continuation = VolundrParentWorkflowContinuation(volundr_factory=Factory())
    children = [
        {
            "childKey": "api",
            "attempt": 2,
            "attemptId": str(uuid4()),
            "taskId": "task-1",
            "state": "blocked",
            "failureKind": "missing_credentials",
            "detail": "Which credential should I use?",
        }
    ]

    await continuation.notify_parent(
        execution,
        generation=3,
        correlation_revision=7,
        children=children,
    )

    args, kwargs = adapter.calls[0]
    assert args[:2] == ("session-1", "workstreams.blocked")
    assert kwargs["payload"]["children"] == children
    assert kwargs["payload"]["revision"] == 7
    assert kwargs["request_id"] == kwargs["payload"]["continuationId"]


def _wait_snapshot() -> dict:
    """A minimal pinned graph naming the exact wait node's observed event.

    Uses an event name distinct from any bundled workflow's own vocabulary,
    to prove the continuation adapter reads it from the exact wait node
    rather than assuming a single graph-wide wait.
    """
    return {
        "graph": {
            "nodes": [
                {"id": "publication-wait", "kind": "wait", "conditions": ["timer"]},
            ],
            "edges": [
                {
                    "id": "wait-observed",
                    "source": "publication-wait",
                    "target": "delivery-publish",
                    "label": "publication.observed -> publication.observed",
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_terminal_wait_observation_notifies_parent_with_exact_wait_binding() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.calls = []

        async def publish_workflow_event(self, *args, **kwargs) -> None:
            self.calls.append((args, kwargs))

    adapter = Adapter()

    class Factory:
        async def primary_for_owner(self, owner_id):
            assert owner_id == "owner-1"
            return adapter

    execution = SimpleNamespace(
        id=uuid4(),
        owner_id="owner-1",
        connection_id="",
        parent_session_id="session-1",
        parent_node_id="publication-wait",
        workflow_snapshot=_wait_snapshot(),
    )
    wait = WorkflowWait(
        id=uuid4(),
        execution_id=execution.id,
        node_id="publication-wait",
        condition_type="timer",
        request={"until": "2026-01-01T00:00:00+00:00"},
        request_digest="a" * 64,
        execution_generation=2,
        execution_revision=1,
        state=WorkflowWaitState.READY,
        next_poll_at=datetime.now(UTC),
    )
    observation = WaitObservation(
        status=WaitObservationStatus.SATISFIED,
        observed_at=datetime.now(UTC),
        reason="The configured deadline has passed",
        detail={"until": "2026-01-01T00:00:00+00:00"},
    )
    continuation = VolundrParentWorkflowContinuation(volundr_factory=Factory())

    await continuation.notify_wait_observation(execution, wait, observation)
    await continuation.notify_wait_observation(execution, wait, observation)

    first_args, first_kwargs = adapter.calls[0]
    second_args, second_kwargs = adapter.calls[1]
    assert first_args[:2] == ("session-1", "publication.observed")
    assert first_kwargs["payload"]["waitId"] == str(wait.id)
    assert first_kwargs["payload"]["nodeId"] == "publication-wait"
    assert first_kwargs["payload"]["conditionType"] == "timer"
    assert first_kwargs["payload"]["generation"] == 2
    assert first_kwargs["payload"]["status"] == "satisfied"
    assert first_kwargs["payload"]["detail"] == {"until": "2026-01-01T00:00:00+00:00"}
    assert first_kwargs["request_id"] == second_kwargs["request_id"]
    assert first_args == second_args
