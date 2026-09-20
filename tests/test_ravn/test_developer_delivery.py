from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from niuu.domain.delivery import (
    BranchPublicationRequest,
    CandidateEvidence,
    IntegrationReceipt,
    MergeRequest,
    RequirementEvidence,
    ReviewRequest,
    WorkspaceAllocation,
    WorkstreamSpec,
)
from ravn.adapters.developer_a2a import (
    ConfiguredRavnDeveloperA2AGateway,
    RavnDeveloperA2AGateway,
    _tool_payload,
)
from ravn.adapters.developer_delivery_http import (
    HttpDeliveryServiceClient,
    HttpDeveloperExecutionClient,
)
from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.adapters.tool_build.http import HttpResponse
from ravn.adapters.tools.developer_delivery import (
    DeliveryEvidenceTool,
    DeliveryForgeTool,
    DeliveryWorkspaceTool,
    DeveloperExecutionCancelTool,
    DeveloperExecutionCompleteTool,
    DeveloperExecutionExpandTool,
    DeveloperExecutionMessageTool,
    DeveloperExecutionReconcileTool,
    DeveloperExecutionRecordIntegrationTool,
    DeveloperExecutionRetryTool,
    DeveloperExecutionWaitDeliveryTool,
)
from ravn.cli.commands import _build_tool_mcp_tools
from ravn.cli.tool_builders import _build_tools
from ravn.config import Settings
from ravn.domain.developer_delivery import ChildLaunchRequest, ChildTaskHandle
from ravn.domain.models import Session, ToolResult


class _TaskTool:
    def __init__(self) -> None:
        self.starts: list[tuple[dict, str]] = []
        self.replies: list[tuple[dict, str]] = []
        self.auth_tokens: list[str] = []

    async def execute_persisted_start(
        self, value: dict, *, message_id: str, auth_token: str = ""
    ) -> ToolResult:
        self.auth_tokens.append(auth_token)
        self.starts.append((value, message_id))
        return ToolResult(
            tool_call_id="",
            content=json.dumps(
                {"task_id": "task-1", "context_id": "context-1", "state": "TASK_STATE_SUBMITTED"}
            ),
        )

    async def execute_persisted_reply(self, value: dict, *, message_id: str) -> ToolResult:
        self.replies.append((value, message_id))
        return self._observation()

    async def execute(self, value: dict) -> ToolResult:
        return self._observation()

    async def execute_persisted_task_operation(
        self, value: dict, *, message_id: str = "", auth_token: str = ""
    ) -> dict:
        self.auth_tokens.append(auth_token)
        if value["operation"] == "reply":
            self.replies.append((value, message_id))
        return json.loads(self._observation().content)

    @staticmethod
    def _observation() -> ToolResult:
        return ToolResult(
            tool_call_id="",
            content=json.dumps(
                {
                    "task_id": "task-1",
                    "context_id": "context-1",
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "status_message": "Need an interface decision",
                    "artifacts": [{"artifactId": "question-1"}],
                }
            ),
        )


@pytest.mark.asyncio
async def test_developer_gateway_preserves_terminal_failure_metadata() -> None:
    tool = SimpleNamespace(
        execute_persisted_task_operation=AsyncMock(
            return_value={
                "id": "task-1",
                "status": {"state": "TASK_STATE_FAILED"},
                "metadata": {"error": "Pinned workspace configuration changed"},
            }
        )
    )
    gateway = RavnDeveloperA2AGateway(task_tool=tool)
    observation = await gateway.get_child(
        ChildTaskHandle(agent_id="worker-agent", task_id="task-1", context_id="context-1")
    )
    assert observation.state == "failed"
    assert observation.error == "Pinned workspace configuration changed"


@pytest.mark.asyncio
async def test_developer_gateway_preserves_launch_and_reply_idempotency() -> None:
    task_tool = _TaskTool()
    gateway = RavnDeveloperA2AGateway(
        task_tool=task_tool,  # type: ignore[arg-type]
        developer_execution_base_url="https://ting.example",
    )
    handle = await gateway.launch_child(
        ChildLaunchRequest(
            intent_id=str(uuid4()),
            message_id="launch-1",
            agent_id="worker-agent",
            skill_id="developer-workstream",
            work_order={
                "childKey": "api",
                "repository": "group/repo",
                "baseSha": "a" * 40,
                "workspace": {
                    "workspace_path": "/workspace/api",
                    "branch_name": "delivery/api",
                },
            },
            metadata={"executionId": "execution-1"},
        ),
        auth_token="owner-token",
    )
    first = await gateway.get_child(handle, auth_token="owner-token")
    second = await gateway.get_child(handle, auth_token="owner-token")
    reply = await gateway.reply_child(
        handle,
        answer="Use the existing interface.",
        metadata={"findingId": "question-1"},
        message_id="reply-1",
        auth_token="owner-token",
    )

    assert task_tool.starts[0][1] == "launch-1"
    assert json.loads(task_tool.starts[0][0]["prompt"])["childKey"] == "api"
    launch_metadata = task_tool.starts[0][0]["metadata"]
    assert launch_metadata["repo"] == "/workspace/api"
    assert launch_metadata["branch"] == "delivery/api"
    assert launch_metadata["baseSha"] == "a" * 40
    assert launch_metadata["developerExecution"] == {"executionId": "execution-1"}
    assert first.state == "blocked"
    assert first.event_id == second.event_id == reply.event_id
    assert task_tool.replies[0][1] == "reply-1"
    assert task_tool.auth_tokens == ["owner-token"] * 4

    with pytest.raises(ValueError, match="non-empty answer"):
        await gateway.reply_child(handle, answer="", metadata={}, message_id="reply-2")
    with pytest.raises(ValueError, match="persisted message_id"):
        await gateway.reply_child(handle, answer="answer", metadata={}, message_id="")


@pytest.mark.asyncio
async def test_developer_gateway_observes_exact_metadata_delivery_result() -> None:
    class RawTaskTool(_TaskTool):
        async def execute_persisted_task_operation(
            self, value: dict, *, message_id: str = "", auth_token: str = ""
        ) -> dict:
            del value, message_id, auth_token
            return {
                "id": "task-1",
                "contextId": "context-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "metadata": {
                    "deliveryResult": {
                        "candidateSha": "a" * 40,
                        "reviews": [{"summary": "x" * 8_000}],
                    }
                },
            }

    gateway = RavnDeveloperA2AGateway(task_tool=RawTaskTool())  # type: ignore[arg-type]
    observation = await gateway.get_child(
        ChildTaskHandle(agent_id="worker-agent", task_id="task-1", context_id="context-1")
    )

    assert observation.state == "completed"
    assert observation.result is not None
    assert len(observation.result["reviews"][0]["summary"]) == 8_000


@pytest.mark.asyncio
async def test_developer_gateway_preserves_pending_question_and_gate_identity() -> None:
    class RawTaskTool(_TaskTool):
        request_id = "request-1"

        async def execute_persisted_task_operation(
            self, value: dict, *, message_id: str = "", auth_token: str = ""
        ) -> dict:
            del value, message_id, auth_token
            return {
                "id": "task-1",
                "contextId": "context-1",
                "status": {
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "message": "Input required",
                },
                "metadata": {
                    "pendingQuestions": [
                        {
                            "requestId": self.request_id,
                            "persona": "developer-coder",
                            "question": "Which branch?",
                            "reason": "Two targets exist",
                            "recommendation": "Use main",
                            "attempted": ["inspect refs"],
                        }
                    ],
                    "pendingGates": [
                        {
                            "gateId": "gate-1",
                            "nodeId": "review",
                            "label": "Approve review",
                            "condition": "review passed",
                            "instructions": "Inspect findings",
                            "summary": "One finding",
                        }
                    ],
                },
            }

    tool = RawTaskTool()
    gateway = RavnDeveloperA2AGateway(task_tool=tool)  # type: ignore[arg-type]
    handle = ChildTaskHandle(agent_id="worker-agent", task_id="task-1", context_id="context-1")
    first = await gateway.get_child(handle)
    tool.request_id = "request-2"
    changed = await gateway.get_child(handle)

    assert first.pending_questions[0].to_a2a_metadata() == {
        "requestId": "request-1",
        "persona": "developer-coder",
        "question": "Which branch?",
        "reason": "Two targets exist",
        "recommendation": "Use main",
        "attempted": ["inspect refs"],
    }
    assert first.pending_gates[0].to_a2a_metadata()["gateId"] == "gate-1"
    assert first.event_id != changed.event_id


def test_configured_gateway_composes_existing_a2a_stack(monkeypatch) -> None:
    http = _HttpClient()
    captured: dict = {}

    def _client_factory(**kwargs):
        captured.update(kwargs)
        return http

    monkeypatch.setattr(
        "ravn.adapters.developer_a2a.client_from_workload_identity",
        _client_factory,
    )
    gateway = ConfiguredRavnDeveloperA2AGateway(
        base_url="https://platform.example",
        trusted_origins=["https://peer.example"],
        agent_card_urls=["https://peer.example/card"],
        external_token="token",
    )

    assert gateway._developer_execution_base_url == "https://platform.example"
    assert captured["external_token"] == "token"
    assert captured["allowed_origins"] == [
        "https://platform.example",
        "https://peer.example",
    ]
    assert gateway._task_tool._directory._agent_card_urls == [
        "https://peer.example/card",
        "https://platform.example/.well-known/agent-card.json",
    ]


def test_configured_gateway_anonymous_dev_mode_has_no_base_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "ravn.adapters.developer_a2a.client_from_workload_identity",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("auth factory called")),
    )

    gateway = ConfiguredRavnDeveloperA2AGateway(
        base_url="http://niuu:8180",
        agent_card_urls=["http://ravn-child:8080/.well-known/agent-card.json"],
        trusted_origins=["http://ravn-child:8080"],
        anonymous_dev_mode=True,
    )

    assert gateway._task_tool._client._auth is None


def test_a2a_tool_payload_rejects_transport_errors_and_invalid_json() -> None:
    with pytest.raises(RuntimeError, match="denied"):
        _tool_payload("denied", is_error=True)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _tool_payload("not-json", is_error=False)
    with pytest.raises(RuntimeError, match="non-object"):
        _tool_payload("[]", is_error=False)


@dataclass
class _ExecutionService:
    payload: dict | None = None

    async def message(self, payload: dict) -> dict:
        self.payload = payload
        return {"state": "waiting"}


@pytest.mark.asyncio
async def test_message_tool_calls_durable_execution_facade() -> None:
    service = _ExecutionService()
    tool = DeveloperExecutionMessageTool(service=service)  # type: ignore[arg-type]
    result = await tool.execute(
        {
            "campaign_id": "campaign-1",
            "parent_node_id": "workstreams",
            "child_key": "api",
            "attempt_id": "00000000-0000-0000-0000-000000000001",
            "answer": "Use the existing interface.",
            "metadata": {"requestId": "question-1"},
            "message_id": "reply-1",
        }
    )
    assert not result.is_error
    assert service.payload is not None
    assert service.payload["message_id"] == "reply-1"
    metadata_schema = tool.input_schema["properties"]["metadata"]
    assert metadata_schema["properties"]["requestId"]["minLength"] == 1
    assert metadata_schema["properties"]["gateDecision"]["enum"] == [
        "approve",
        "request_changes",
    ]
    assert metadata_schema["oneOf"] == [
        {"required": ["requestId"]},
        {"required": ["gateId", "gateDecision"]},
    ]
    assert "Reuse message_id" in tool.description


@pytest.mark.asyncio
async def test_complete_tool_rejects_policy_bodies_before_service_call() -> None:
    service = MagicMock()
    tool = DeveloperExecutionCompleteTool(service=service)

    result = await tool.execute(
        {"merge": {}, "evidence": {"nested": {"acceptance_policy": {"unsafe": True}}}}
    )

    assert result.is_error
    assert "pinned contract or policy id" in result.content
    service.complete.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_forge_publish_binds_exact_typed_contracts() -> None:
    sha = "a" * 40
    allocation = WorkspaceAllocation(
        allocation_id="allocation-1",
        campaign_id="campaign-1",
        workstream_key="integration",
        repository="https://git.example/org/repo",
        workspace_path="/workspace/integration",
        repository_path="/workspace/repo",
        base_sha=sha,
        branch_name="delivery/integration",
        worker_id="worker-1",
        allowed_paths=("src/",),
        test_contract_ids=("tests",),
    )
    receipt = IntegrationReceipt(
        receipt_id="integration-1",
        campaign_id="campaign-1",
        integration_allocation_id="allocation-1",
        workstream_key="integration",
        attempt_id="attempt-1",
        repository="https://git.example/org/repo",
        base_sha=sha,
        candidate_sha=sha,
        candidate_tree=sha,
        previous_integration_sha=sha,
        integrated_commits=(sha,),
        resulting_sha=sha,
        resulting_tree=sha,
        completed_at=datetime.now(UTC),
    )
    publication = BranchPublicationRequest(
        campaign_id="campaign-1",
        repository="https://git.example/org/repo",
        branch="delivery/integration",
        expected_head_sha=sha,
    )

    class Service:
        async def publish_branch(self, got_allocation, got_receipt, got_publication, *, policy_id):
            assert got_allocation == allocation
            assert got_receipt == receipt
            assert got_publication == publication
            assert policy_id == "strict"
            return {"resulting_remote_sha": sha}

    result = await DeliveryForgeTool(service=Service()).execute(
        {
            "operation": "publish",
            "payload": {
                "allocation": allocation.model_dump(mode="json"),
                "integration_receipt": receipt.model_dump(mode="json"),
                "publication": publication.model_dump(mode="json"),
                "policy_id": "strict",
            },
        }
    )

    assert not result.is_error
    assert json.loads(result.content)["resulting_remote_sha"] == sha


@pytest.mark.asyncio
async def test_child_workspace_tool_rejects_operations_outside_runtime_allowlist() -> None:
    tool = DeliveryWorkspaceTool(service=MagicMock())
    tool.operations = {"verify": "run_verification"}

    result = await tool.execute({"operation": "integrate", "payload": {}})

    assert result.is_error
    assert "unsupported delivery_workspace operation" in result.content


@pytest.mark.asyncio
async def test_delivery_tool_rejects_malformed_payloads_and_service_failures() -> None:
    class Service:
        async def resolve_ref(self, repository, ref):
            del repository, ref
            raise RuntimeError("provider unavailable")

        async def inspect_candidate(self, repository, review_number, *, policy_id, campaign_id):
            raise AssertionError((repository, review_number, policy_id, campaign_id))

    tool = DeliveryForgeTool(service=Service())
    non_object = await tool.execute({"operation": "resolve", "payload": []})
    missing_string = await tool.execute(
        {"operation": "resolve", "payload": {"repository": "repo", "ref": ""}}
    )
    invalid_integer = await tool.execute(
        {
            "operation": "inspect",
            "payload": {
                "repository": "repo",
                "review_number": True,
                "policy_id": "strict",
                "campaign_id": "campaign-1",
            },
        }
    )
    failed = await tool.execute(
        {"operation": "resolve", "payload": {"repository": "repo", "ref": "main"}}
    )

    assert non_object.is_error and "must be an object" in non_object.content
    assert missing_string.is_error and "non-empty string" in missing_string.content
    assert invalid_integer.is_error and "must be an integer" in invalid_integer.content
    assert failed.is_error and failed.content == "provider unavailable"


@pytest.mark.asyncio
async def test_delivery_tools_dispatch_every_provider_neutral_typed_operation() -> None:
    sha = "a" * 40
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="api",
        repository="https://git.example/org/repo",
        repository_path="/workspace/repo",
        base_sha=sha,
        branch_name="delivery/api",
        worker_id="worker-1",
        allowed_paths=("src/",),
        test_contract_ids=("tests",),
    )
    allocation = WorkspaceAllocation(
        allocation_id="allocation-1",
        campaign_id="campaign-1",
        workstream_key="api",
        repository=spec.repository,
        workspace_path="/workspace/api",
        repository_path=spec.repository_path,
        base_sha=sha,
        branch_name=spec.branch_name,
        worker_id=spec.worker_id,
        allowed_paths=spec.allowed_paths,
        test_contract_ids=spec.test_contract_ids,
    )
    evidence = CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="api",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=spec.repository,
        base_sha=sha,
        candidate_sha=sha,
        candidate_tree=sha,
        requirements=(
            RequirementEvidence(
                requirement_id="req-1",
                implementation_paths=("src/api.py",),
                verification_contract_ids=("tests",),
            ),
        ),
    )
    merge = MergeRequest(
        campaign_id="campaign-1",
        repository=spec.repository,
        review_number=7,
        expected_head_sha=sha,
        expected_base_sha=sha,
        expected_target_branch="main",
        method="squash",
    )
    review = ReviewRequest(
        campaign_id="campaign-1",
        repository=spec.repository,
        title="Delivery",
        description="Typed review",
        source_branch="delivery/api",
        target_branch="main",
        expected_head_sha=sha,
    )

    class Service:
        async def allocate_workstream(self, value):
            assert isinstance(value, WorkstreamSpec)
            return {"operation": "allocate"}

        async def run_verification(self, value, **kwargs):
            assert isinstance(value, WorkspaceAllocation) and kwargs["contract_id"] == "tests"
            return {"operation": "verify"}

        async def integrate_candidate(self, value, **kwargs):
            assert isinstance(value, WorkspaceAllocation)
            assert isinstance(kwargs["evidence"], CandidateEvidence)
            return {"operation": "integrate"}

        async def inspect_integration(self, value, receipt, *, policy_id):
            assert isinstance(value, WorkspaceAllocation)
            assert isinstance(receipt, IntegrationReceipt) and policy_id == "strict"
            return {"operation": "inspect"}

        async def resolve_ref(self, repository, ref):
            assert (repository, ref) == (spec.repository, "main")
            return {"operation": "resolve"}

        async def open_review(self, value):
            assert isinstance(value, ReviewRequest)
            return {"operation": "open"}

        async def inspect_candidate(self, repository, review_number, *, policy_id, campaign_id):
            assert policy_id == "strict"
            assert (repository, review_number, campaign_id) == (
                spec.repository,
                7,
                "campaign-1",
            )
            return {"operation": "inspect"}

        async def conditional_merge(self, value, **kwargs):
            assert isinstance(value, MergeRequest)
            assert isinstance(kwargs["evidence"], CandidateEvidence)
            return {"operation": "merge"}

        async def reconcile_merge(self, value):
            assert isinstance(value, MergeRequest)
            return {"operation": "reconcile"}

        async def validate_evidence(self, value, **kwargs):
            assert isinstance(value, CandidateEvidence) and kwargs["policy_id"] == "strict"
            return {"operation": "validate"}

    service = Service()
    workspace = DeliveryWorkspaceTool(service=service)
    forge = DeliveryForgeTool(service=service)
    evidence_tool = DeliveryEvidenceTool(service=service)
    for tool in (workspace, forge, evidence_tool):
        schema = tool.input_schema
        variants = schema["oneOf"]
        assert {item["properties"]["operation"]["enum"][0] for item in variants} == set(
            tool.operations
        )
        assert all(
            item["properties"]["payload"].get("additionalProperties") is False for item in variants
        )
        assert tool.required_permission == "developer:coordinate"
        assert tool.parallelisable is False
        assert "narrow deterministic" in tool.description
    calls = [
        (workspace, "allocate", spec.model_dump(mode="json")),
        (
            workspace,
            "verify",
            {
                "allocation": allocation.model_dump(mode="json"),
                "attempt_id": "attempt-1",
                "candidate_sha": sha,
                "contract_id": "tests",
            },
        ),
        (
            workspace,
            "integrate",
            {
                "integration": allocation.model_dump(mode="json"),
                "expected_integration_head": sha,
                "evidence": evidence.model_dump(mode="json"),
                "policy_id": "strict",
            },
        ),
        (
            workspace,
            "inspect",
            {
                "allocation": allocation.model_dump(mode="json"),
                "integration_receipt": IntegrationReceipt(
                    receipt_id="integration-1",
                    campaign_id="campaign-1",
                    integration_allocation_id="allocation-1",
                    workstream_key="api",
                    attempt_id="attempt-1",
                    repository=spec.repository,
                    base_sha=sha,
                    candidate_sha=sha,
                    candidate_tree=sha,
                    previous_integration_sha=sha,
                    integrated_commits=(sha,),
                    resulting_sha=sha,
                    resulting_tree=sha,
                    completed_at=datetime.now(UTC),
                ).model_dump(mode="json"),
                "policy_id": "strict",
            },
        ),
        (forge, "resolve", {"repository": spec.repository, "ref": "main"}),
        (forge, "open", review.model_dump(mode="json")),
        (
            forge,
            "inspect",
            {
                "repository": spec.repository,
                "review_number": 7,
                "campaign_id": "campaign-1",
                "policy_id": "strict",
            },
        ),
        (
            forge,
            "merge",
            {
                "request": merge.model_dump(mode="json"),
                "evidence": evidence.model_dump(mode="json"),
                "policy_id": "strict",
            },
        ),
        (forge, "reconcile", {"request": merge.model_dump(mode="json")}),
        (
            evidence_tool,
            "validate",
            {"evidence": evidence.model_dump(mode="json"), "policy_id": "strict"},
        ),
    ]
    for tool, operation, payload in calls:
        Draft202012Validator(tool.input_schema).validate(
            {"operation": operation, "payload": payload}
        )
        result = await tool.execute({"operation": operation, "payload": payload})
        assert not result.is_error
        assert json.loads(result.content)["operation"] == operation

    inspect_payload = calls[3][2]
    mismatch_errors = list(
        Draft202012Validator(workspace.input_schema).iter_errors(
            {"operation": "integrate", "payload": inspect_payload}
        )
    )
    assert mismatch_errors


@pytest.mark.asyncio
async def test_developer_execution_tools_dispatch_and_expose_strict_schemas() -> None:
    class Service:
        async def expand(self, payload):
            return {"operation": "expand", **payload}

        async def reconcile(self, payload):
            return {"operation": "reconcile", **payload}

        async def cancel(self):
            return {"operation": "cancel"}

        async def message(self, payload):
            return {"operation": "message", **payload}

        async def complete(self, payload):
            return {"operation": "complete", **payload}

        async def record_integration(self, payload):
            return {"operation": "record_integration", **payload}

    cases = [
        (
            DeveloperExecutionExpandTool,
            {
                "campaign_id": "campaign-1",
                "parent_node_id": "node",
                "generation": 1,
                "plan_revision": "plan-7",
                "workstreams": [{}],
            },
        ),
        (DeveloperExecutionReconcileTool, {"campaign_id": "campaign-1", "parent_node_id": "node"}),
        (DeveloperExecutionCancelTool, {"campaign_id": "campaign-1", "parent_node_id": "node"}),
        (
            DeveloperExecutionMessageTool,
            {
                "campaign_id": "campaign-1",
                "parent_node_id": "node",
                "child_key": "api",
                "attempt_id": "00000000-0000-0000-0000-000000000001",
                "answer": "continue",
                "metadata": {"requestId": "request-1"},
                "message_id": "message-1",
            },
        ),
        (DeveloperExecutionCompleteTool, {"merge": {}, "evidence": {}}),
        (
            DeveloperExecutionRecordIntegrationTool,
            {
                "integration_receipts": [{}],
                "integration_allocation": {},
            },
        ),
    ]
    for tool_type, payload in cases:
        tool = tool_type(service=Service())
        assert tool.input_schema["additionalProperties"] is False
        result = await tool.execute(payload)
        assert not result.is_error
        assert json.loads(result.content)["operation"] == tool.operation

    expand_schema = DeveloperExecutionExpandTool(service=Service()).input_schema
    assert "plan_revision" in expand_schema["required"]
    proposal = expand_schema["properties"]["workstreams"]["items"]
    assert "inputDigest" not in proposal["required"]
    assert "planDigest" not in proposal["required"]
    message_schema = DeveloperExecutionMessageTool(service=Service()).input_schema
    assert "metadata" in message_schema["required"]
    assert set(proposal["required"]) >= {
        "key",
        "objective",
        "requirementIds",
        "input",
        "agentId",
        "skillId",
        "workspace",
    }
    workstream_input = proposal["properties"]["input"]
    assert "expectedOutputs" in workstream_input["required"]
    assert workstream_input["properties"]["expectedOutputs"]["minItems"] == 1
    assert "repository" in proposal["properties"]["workspace"]["required"]


@pytest.mark.asyncio
async def test_developer_execution_cancel_is_truthful_and_rejects_child_targeting() -> None:
    class Service:
        async def cancel(self):
            return {"operation": "cancel"}

    tool = DeveloperExecutionCancelTool(service=Service())

    assert "ENTIRE" in tool.description
    assert "every child attempt" in tool.description
    assert "coordinator's own session" in tool.description
    assert "child_keys" not in tool.input_schema["properties"]

    result = await tool.execute(
        {
            "campaign_id": "campaign-1",
            "parent_node_id": "node",
            "child_keys": ["api"],
        }
    )

    assert result.is_error
    assert "no per-child" in result.content
    assert "developer_execution_retry" in result.content

    ok = await tool.execute({"campaign_id": "campaign-1", "parent_node_id": "node"})
    assert not ok.is_error


@pytest.mark.asyncio
async def test_developer_execution_reconcile_rejects_child_targeting() -> None:
    class Service:
        async def reconcile(self, payload):
            return {"operation": "reconcile", **payload}

    tool = DeveloperExecutionReconcileTool(service=Service())

    assert "child_keys" not in tool.input_schema["properties"]

    result = await tool.execute(
        {
            "campaign_id": "campaign-1",
            "parent_node_id": "node",
            "child_keys": ["api"],
        }
    )

    assert result.is_error
    assert "no per-child" in result.content


@pytest.mark.asyncio
async def test_http_execution_client_cancel_takes_no_payload() -> None:
    http = _HttpClient()
    client = HttpDeveloperExecutionClient(
        base_url="https://ting.example",
        execution_id="execution-1",
        client=http,  # type: ignore[arg-type]
    )

    result = await client.cancel()

    assert result == {"ok": True}
    assert http.posts == [
        ("https://ting.example/api/v1/ting/developer-executions/execution-1/cancel", {})
    ]


def _codex_supported_schema_size(schema: object) -> int:
    """Mirror the JsonSchema fields retained by Codex 0.154 before compaction."""
    supported = {
        "$ref",
        "type",
        "description",
        "encrypted",
        "enum",
        "items",
        "minItems",
        "properties",
        "required",
        "additionalProperties",
        "anyOf",
        "oneOf",
        "allOf",
        "$defs",
        "definitions",
    }

    def project(value: object) -> object:
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        projected: dict[str, object] = {}
        for key, item in value.items():
            if key == "const":
                projected["enum"] = [item]
            elif key in {"properties", "$defs", "definitions"} and isinstance(item, dict):
                projected[key] = {name: project(child) for name, child in item.items()}
            elif key in supported:
                projected[key] = project(item)
        return projected

    return len(json.dumps(project(schema), separators=(",", ":"), sort_keys=True))


def test_coordinator_tool_schemas_stay_below_native_compaction_budget() -> None:
    service = object()
    tools = [
        DeveloperExecutionExpandTool(service=service),
        DeveloperExecutionReconcileTool(service=service),
        DeveloperExecutionRetryTool(service=service),
        DeveloperExecutionCancelTool(service=service),
        DeveloperExecutionMessageTool(service=service),
        DeveloperExecutionCompleteTool(service=service),
        DeveloperExecutionRecordIntegrationTool(service=service),
        DeveloperExecutionWaitDeliveryTool(service=service),
        DeliveryWorkspaceTool(service=service),
        DeliveryForgeTool(service=service),
        DeliveryEvidenceTool(service=service),
    ]

    sizes = {tool.name: _codex_supported_schema_size(tool.input_schema) for tool in tools}

    assert all(size < 4_500 for size in sizes.values()), sizes


def test_delivery_schema_exposes_exact_copy_and_policy_guidance() -> None:
    workspace = DeliveryWorkspaceTool(service=object())
    complete = DeveloperExecutionCompleteTool(service=object())

    assert "Do not pass the child result wrapper" in workspace.description
    assert "configured workstream/evidence policy_id" in workspace.description
    assert "previous successful integration receipt resulting_sha" in workspace.description
    assert "direct snake_case CandidateEvidence" in complete.description

    integrate = next(
        branch["properties"]["payload"]
        for branch in workspace.input_schema["oneOf"]
        if branch["properties"]["operation"]["enum"] == ["integrate"]
    )
    evidence = integrate["properties"]["evidence"]
    assert set(evidence["properties"]) >= {
        "campaign_id",
        "workstream_key",
        "attempt_id",
        "worker_id",
        "repository",
        "base_sha",
        "candidate_sha",
        "candidate_tree",
        "requirements",
        "verifications",
        "reviews",
        "checks",
    }
    assert evidence["properties"]["requirements"]["items"]["required"] == [
        "requirement_id",
        "implementation_paths",
        "verification_contract_ids",
    ]
    assert evidence["properties"]["verifications"]["items"]["type"] == "object"
    assert "properties" not in evidence["properties"]["verifications"]["items"]


@pytest.mark.asyncio
async def test_compact_candidate_schema_keeps_typed_nested_validation() -> None:
    service = AsyncMock()
    tool = DeliveryWorkspaceTool(service=service)

    result = await tool.execute(
        {
            "operation": "integrate",
            "payload": {
                "integration": {},
                "expected_integration_head": "a" * 40,
                "evidence": {
                    "campaign_id": "campaign-1",
                    "workstream_key": "api",
                    "attempt_id": "attempt-1",
                    "worker_id": "worker-1",
                    "repository": "https://git.example/org/repo",
                    "base_sha": "a" * 40,
                    "candidate_sha": "a" * 40,
                    "candidate_tree": "a" * 40,
                    "requirements": [],
                },
                "policy_id": "developer-workstream",
            },
        }
    )

    assert result.is_error
    assert "WorkspaceAllocation" in result.content
    service.integrate_candidate.assert_not_awaited()


def _coordinator_settings() -> Settings:
    settings = Settings()
    settings.gateway.platform.enabled = True
    settings.gateway.platform.base_url = "https://platform.example"
    settings.gateway.platform.pat_token = "platform-token"
    settings.developer_execution.execution_id = str(uuid4())
    settings.developer_execution.base_url = "https://ting.example"
    settings.developer_execution.auth_token_file = "/projected/developer/token"
    return settings


def test_coordinator_runtime_has_only_bounded_delivery_tools(tmp_path) -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-coordinator"
    )
    assert persona is not None
    settings = _coordinator_settings()
    token_file = tmp_path / "developer-token"
    token_file.write_text("execution-token")
    settings.developer_execution.auth_token_file = str(token_file)
    tools = _build_tools(
        settings,
        tmp_path,
        Session(),
        MagicMock(),
        None,
        None,
        persona_config=persona,
    )
    names = {tool.name for tool in tools}
    assert names == {
        "developer_execution_expand",
        "developer_execution_reconcile",
        "developer_execution_retry",
        "developer_execution_message",
        "developer_execution_cancel",
        "developer_execution_complete",
        "developer_execution_record_integration",
        "developer_execution_wait_delivery",
        "delivery_workspace",
        "delivery_forge",
        "delivery_evidence",
    }
    assert not names & {"bash", "terminal", "write_file", "edit_file", "a2a_task"}

    execution = next(tool for tool in tools if tool.name == "developer_execution_expand")
    delivery = next(tool for tool in tools if tool.name == "delivery_workspace")
    assert delivery._service._client is execution._service._client
    assert execution._service._client._headers()["Authorization"] == "Bearer execution-token"
    token_file.write_text("rotated-token")
    assert execution._service._client._headers()["Authorization"] == "Bearer rotated-token"


def test_coordinator_tool_mcp_builds_bounded_delivery_tools(tmp_path) -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-coordinator"
    )
    assert persona is not None
    settings = _coordinator_settings()
    settings.permission.workspace_root = str(tmp_path)

    tools = _build_tool_mcp_tools(settings, persona_config=persona)

    assert {tool.name for tool in tools} == {
        "developer_execution_expand",
        "developer_execution_reconcile",
        "developer_execution_retry",
        "developer_execution_message",
        "developer_execution_cancel",
        "developer_execution_complete",
        "developer_execution_record_integration",
        "developer_execution_wait_delivery",
        "delivery_workspace",
        "delivery_forge",
        "delivery_evidence",
    }


def test_coordinator_runtime_rejects_missing_execution_credential(tmp_path) -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-coordinator"
    )
    assert persona is not None
    settings = _coordinator_settings()
    settings.developer_execution.auth_token_file = ""
    with pytest.raises(RuntimeError, match="auth_token_file"):
        _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            persona_config=persona,
        )


def test_coordinator_runtime_explicit_anonymous_dev_avoids_platform_base_auth(tmp_path) -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-coordinator"
    )
    assert persona is not None
    settings = _coordinator_settings()
    settings.gateway.platform.pat_token = ""
    settings.gateway.platform.workload_token_file = "/missing/projected-token"
    settings.gateway.platform.anonymous_dev_mode = True
    settings.developer_execution.auth_token_file = ""

    tools = _build_tools(
        settings,
        tmp_path,
        Session(),
        MagicMock(),
        None,
        None,
        persona_config=persona,
    )

    delivery = next(tool for tool in tools if tool.name == "delivery_workspace")
    execution = next(tool for tool in tools if tool.name == "developer_execution_expand")
    assert delivery._service._client._auth is None
    assert execution._service._client._auth is None
    assert delivery._service._client is execution._service._client
    assert "Authorization" not in execution._service._client._headers()


def test_child_workstream_coordinator_exposes_only_child_credential_operations(tmp_path) -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-coordinator"
    )
    assert persona is not None
    settings = _coordinator_settings()
    settings.workflow.graph = {"executionContract": "developer-workstream/v1"}

    tools = _build_tools(
        settings,
        tmp_path,
        Session(),
        MagicMock(),
        None,
        None,
        persona_config=persona,
    )

    assert {tool.name for tool in tools} == {"delivery_workspace"}
    workspace = next(tool for tool in tools if tool.name == "delivery_workspace")
    assert workspace.input_schema["properties"]["operation"]["enum"] == ["verify"]
    verify = workspace.input_schema["oneOf"][0]["properties"]["payload"]
    assert set(verify["required"]) == {
        "allocation",
        "attempt_id",
        "candidate_sha",
        "contract_id",
    }
    assert (
        "WorkspaceAllocation returned by delivery_workspace.allocate"
        in verify["properties"]["allocation"]["description"]
    )


def test_integration_reviewer_exposes_only_read_only_candidate_inspection(tmp_path) -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-integration-verifier"
    )
    assert persona is not None

    tools = _build_tools(
        _coordinator_settings(),
        tmp_path,
        Session(),
        MagicMock(),
        None,
        None,
        persona_config=persona,
    )

    assert {tool.name for tool in tools} == {"delivery_workspace"}
    workspace = next(tool for tool in tools if tool.name == "delivery_workspace")
    assert workspace.input_schema["properties"]["operation"]["enum"] == ["inspect"]
    inspect_schema = workspace.input_schema["oneOf"][0]["properties"]["payload"]
    assert set(inspect_schema["required"]) == {
        "allocation",
        "integration_receipt",
        "policy_id",
    }
    assert (
        "signed IntegrationReceipt returned by integrate"
        in inspect_schema["properties"]["integration_receipt"]["description"]
    )


def test_aliased_integration_reviewer_still_gets_inspect_only(tmp_path) -> None:
    """Narrowing must survive a workflow-local alias, not just the raw name.

    InlinePersonaAdapter.load only overwrites `name` when a workflow maps a
    dependency to this persona under a local alias; every other field
    (including `delivery_workspace_actions`) comes through unchanged from the
    real developer-integration-verifier document. Keying the narrowing on
    `name` would miss this and leave the aliased persona with every
    delivery_workspace operation.
    """
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-integration-verifier"
    )
    assert persona is not None
    aliased = replace(persona, name="workstream-verifier")
    assert aliased.delivery_workspace_actions == ["inspect"]

    tools = _build_tools(
        _coordinator_settings(),
        tmp_path,
        Session(),
        MagicMock(),
        None,
        None,
        persona_config=aliased,
    )

    workspace = next(tool for tool in tools if tool.name == "delivery_workspace")
    assert workspace.input_schema["properties"]["operation"]["enum"] == ["inspect"]


def test_delivery_workspace_with_no_declared_actions_gets_none(tmp_path) -> None:
    """Fail closed: granting the tool without a declared allowlist grants nothing."""
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-integration-verifier"
    )
    assert persona is not None
    undeclared = replace(persona, delivery_workspace_actions=[])

    tools = _build_tools(
        _coordinator_settings(),
        tmp_path,
        Session(),
        MagicMock(),
        None,
        None,
        persona_config=undeclared,
    )

    workspace = next(tool for tool in tools if tool.name == "delivery_workspace")
    assert workspace.operations == {}
    assert workspace.input_schema["properties"]["operation"]["enum"] == []


class _HttpClient:
    def __init__(self, body: object | None = None, status_code: int = 200) -> None:
        self.body = body if body is not None else {"ok": True}
        self.status_code = status_code
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url: str, json_body: dict, *, headers=None) -> HttpResponse:
        del headers
        self.posts.append((url, json_body))
        return HttpResponse(self.status_code, self.body)


@pytest.mark.asyncio
async def test_http_execution_client_uses_owner_bound_routes() -> None:
    http = _HttpClient()
    client = HttpDeveloperExecutionClient(
        base_url="https://ting.example/",
        execution_id="execution-1",
        client=http,  # type: ignore[arg-type]
    )
    await client.expand({"generation": 1})
    await client.reconcile({"ignored": True})
    await client.message({"child_key": "api", "message_id": "message-1"})
    await client.cancel()
    await client.complete({"merge": {}, "evidence": {}})
    await client.record_integration({"integration_receipts": [{}], "integration_allocation": {}})
    await client.wait_delivery({"mode": "checks"})

    assert [url.rsplit("/", 1)[-1] for url, _body in http.posts] == [
        "expansions",
        "reconcile",
        "messages",
        "cancel",
        "complete",
        "integration-candidate",
        "delivery-waits",
    ]
    assert http.posts[1][1] == http.posts[3][1] == {}


@pytest.mark.asyncio
async def test_http_delivery_client_maps_all_provider_neutral_operations() -> None:
    http = _HttpClient()
    client = HttpDeliveryServiceClient(
        base_url="https://platform.example/",
        client=http,  # type: ignore[arg-type]
    )
    sha = "a" * 40
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="api",
        repository="https://git.example/org/repo",
        repository_path="/workspace/repo",
        base_sha=sha,
        branch_name="delivery/api",
        worker_id="worker-1",
        allowed_paths=("src/",),
    )
    allocation = WorkspaceAllocation.model_construct(
        allocation_id="allocation-1",
        campaign_id="campaign-1",
        workstream_key="api",
        workspace_path="/workspace/api",
        repository_path="/workspace/repo",
        base_sha=sha,
        branch_name="delivery/api",
        worker_id="worker-1",
        allowed_paths=("src/",),
        test_contract_ids=(),
        repository="https://git.example/org/repo",
    )
    evidence = CandidateEvidence.model_construct(campaign_id="campaign-1")
    merge = MergeRequest(
        campaign_id="campaign-1",
        repository="group/repo",
        review_number=7,
        expected_head_sha=sha,
        expected_base_sha=sha,
        expected_target_branch="main",
        method="squash",
    )
    review = ReviewRequest(
        campaign_id="campaign-1",
        repository="group/repo",
        title="Delivery",
        description="Evidence-backed delivery",
        source_branch="delivery/api",
        target_branch="main",
        expected_head_sha=sha,
    )
    integration = IntegrationReceipt.model_construct(
        receipt_id="integration-1",
        campaign_id="campaign-1",
        integration_allocation_id="allocation-1",
        workstream_key="integration",
        repository="https://git.example/org/repo",
        base_sha=sha,
        candidate_sha=sha,
        candidate_tree=sha,
        previous_integration_sha=sha,
        integrated_commits=(sha,),
        resulting_sha=sha,
        resulting_tree=sha,
        completed_at=datetime(2026, 9, 18, tzinfo=UTC),
    )
    publication = BranchPublicationRequest(
        campaign_id="campaign-1",
        repository="https://git.example/org/repo",
        branch="delivery/integration",
        expected_head_sha=sha,
    )

    await client.resolve_ref("group/repo", "main")
    await client.open_review(review)
    await client.publish_branch(allocation, integration, publication, policy_id="default")
    await client.allocate_workstream(spec)
    await client.run_verification(
        allocation,
        attempt_id="attempt-1",
        candidate_sha=sha,
        contract_id="tests",
    )
    await client.integrate_candidate(
        allocation,
        expected_integration_head=sha,
        evidence=evidence,
        policy_id="default",
    )
    await client.inspect_integration(allocation, integration, policy_id="default")
    await client.inspect_candidate("group/repo", 7, policy_id="default", campaign_id="campaign-1")
    await client.conditional_merge(merge, evidence=evidence, policy_id="default")
    await client.reconcile_merge(merge)
    await client.validate_evidence(evidence, policy_id="default")

    paths = [
        url.removeprefix("https://platform.example/api/v1/forge/delivery") for url, _ in http.posts
    ]
    assert paths == [
        "/refs/resolve",
        "/forge/reviews",
        "/forge/branches",
        "/workspaces/allocate",
        "/workspaces/verify",
        "/workspaces/integrate",
        "/workspaces/integration/inspect",
        "/forge/inspect",
        "/forge/merge",
        "/forge/reconcile",
        "/evidence/validate",
    ]
    assert http.posts[7][1]["campaign_id"] == "campaign-1"


@pytest.mark.asyncio
async def test_http_clients_reject_error_and_non_object_responses() -> None:
    error_client = HttpDeveloperExecutionClient(
        base_url="https://ting.example",
        execution_id="execution-1",
        client=_HttpClient({"detail": "forbidden"}, 403),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="HTTP 403: forbidden"):
        await error_client.reconcile({})

    invalid_client = HttpDeliveryServiceClient(
        base_url="https://platform.example",
        client=_HttpClient(["invalid"]),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="non-object"):
        await invalid_client.resolve_ref("group/repo", "main")


@pytest.mark.asyncio
async def test_policy_discovery_tool_calls_authorized_http_surface() -> None:
    http = _HttpClient({"required_test_contract_ids": ["unit"]})
    client = HttpDeliveryServiceClient(base_url="https://platform.example", client=http)
    tool = DeliveryEvidenceTool(service=client)
    payload = {"campaign_id": "campaign-1", "repository": "repo", "policy_id": "strict"}
    result = await tool.execute({"operation": "describe", "payload": payload})
    assert not result.is_error
    assert json.loads(result.content)["required_test_contract_ids"] == ["unit"]
    assert http.posts == [
        ("https://platform.example/api/v1/forge/delivery/evidence/policy", payload)
    ]
    rejected = await tool.execute(
        {"operation": "describe", "payload": {**payload, "policy": {"require_forge_checks": False}}}
    )
    assert rejected.is_error
    assert len(http.posts) == 1


@pytest.mark.asyncio
async def test_retry_tool_retains_exact_attempt_identity() -> None:
    http = _HttpClient()
    client = HttpDeveloperExecutionClient(
        base_url="https://platform.example", execution_id="execution-1", client=http
    )
    tool = DeveloperExecutionRetryTool(service=client)
    attempt = str(uuid4())
    result = await tool.execute({"child_key": "api-client", "attempt_id": attempt})
    assert not result.is_error
    assert http.posts == [
        (
            "https://platform.example/api/v1/ting/developer-executions/execution-1/children/api-client/retry",
            {"attempt_id": attempt},
        )
    ]
    missing = await tool.execute({"child_key": "api-client"})
    assert missing.is_error
    assert len(http.posts) == 1


@pytest.mark.asyncio
async def test_wait_delivery_tool_preserves_exact_candidate_and_rejects_policy_override():
    service = SimpleNamespace(
        wait_delivery=AsyncMock(return_value={"waitId": "wait-1", "state": "pending"})
    )
    tool = DeveloperExecutionWaitDeliveryTool(service=service)
    payload = {
        "mode": "checks",
        "repository": "https://git.example/org/repo",
        "reviewNumber": 7,
        "expectedHeadSha": "a" * 40,
        "expectedBaseSha": "b" * 40,
        "expectedTargetBranch": "topic",
        "policyId": "integration",
    }
    result = await tool.execute(payload)
    assert not result.is_error
    service.wait_delivery.assert_awaited_once_with(payload)
    assert set(tool.input_schema["required"]) == set(payload)
    assert tool.input_schema["additionalProperties"] is False
    assert "developer.delivery.observed" in tool.description
    merge_contract = tool.input_schema["oneOf"][1]
    assert set(merge_contract["required"]) == {"method", "providerOperationId"}
    rejected = await tool.execute({**payload, "policy": {"required_checks": []}})
    assert rejected.is_error
    assert service.wait_delivery.await_count == 1
