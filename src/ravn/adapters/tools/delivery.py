"""Coordinator-safe tools for the code-delivery execution specialization.

The domain-neutral fan-out/join, retry, message, cancel, and wait tools live
in ``ravn.adapters.tools.workflow_execution``. This module holds only the
tools shaped by the delivery contract: workstream expansion, typed
merge/candidate completion, signed integration recording, and the narrow
deterministic workspace/forge/evidence operations.
"""

from __future__ import annotations

import inspect
from typing import Any

from niuu.domain.delivery import (
    BranchPublicationRequest,
    CandidateEvidence,
    IntegrationReceipt,
    MergeRequest,
    ReviewRequest,
    WorkspaceAllocation,
    WorkstreamSpec,
)
from ravn.adapters.tools.workflow_execution import (
    WorkflowExecutionToolBase,
    _error,
    _payload_error,
    _result,
)
from ravn.domain.delivery import DeliveryExecutionToolPort
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort


class _DeliveryExecutionTool(WorkflowExecutionToolBase):
    """Shared plumbing for a tool over the delivery execution specialization."""

    def __init__(self, *, service: DeliveryExecutionToolPort) -> None:
        super().__init__(service=service)


class DeliveryExpandWorkstreamsTool(_DeliveryExecutionTool):
    """Persist and dispatch a validated generation of developer workstreams.

    Unlike the generic ``workflow_execution_expand``, each proposed child here
    is a full git-shaped workstream: requirement ids, repository, base SHA,
    allowed paths, expected outputs, test contracts, and an allocated
    workspace. This posts to the code-delivery execution specialization.
    """

    operation = "expand_workstreams"
    tool_name = "delivery_expand_workstreams"

    @property
    def description(self) -> str:
        return "Persist and dispatch a validated generation of developer child workflows."

    @property
    def input_schema(self) -> dict:
        properties: dict[str, Any] = {
            "campaign_id": {"type": "string"},
            "parent_node_id": {"type": "string"},
            "generation": {"type": "integer", "minimum": 1},
            "plan_revision": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "description": "Approved plan revision; Ting derives the canonical plan digest.",
            },
            "workstreams": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": _workstream_proposal_schema(),
            },
        }
        required = ["campaign_id", "parent_node_id", "generation", "plan_revision", "workstreams"]
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }


class DeliveryCompleteTool(_DeliveryExecutionTool):
    tool_name = "delivery_complete"
    operation = "complete"

    @property
    def description(self) -> str:
        return (
            "Finalize only after deterministic merge evidence is verified. Copy the exact "
            "reconciled MergeRequest and provide direct snake_case CandidateEvidence for the "
            "integrated candidate; copy its signed receipt objects unchanged from trusted "
            "tool results."
        )

    @property
    def input_schema(self) -> dict:
        properties = {
            "merge": _copied_object_schema(
                "Copy the exact MergeRequest used for the reconciled provider merge."
            ),
            "evidence": _candidate_evidence_schema(),
        }
        return {
            "type": "object",
            "properties": properties,
            "required": ["merge", "evidence"],
            "additionalProperties": False,
        }


class DeliveryRecordIntegrationTool(_DeliveryExecutionTool):
    tool_name = "delivery_record_integration"
    operation = "record_integration"

    @property
    def description(self) -> str:
        return "Persist a signed, verified integration candidate before independent review."

    @property
    def input_schema(self) -> dict:
        properties = {
            "integration_receipts": {
                "type": "array",
                "minItems": 1,
                "items": _inline_model_schema(IntegrationReceipt),
            },
            "integration_allocation": _inline_model_schema(WorkspaceAllocation),
        }
        return {
            "type": "object",
            "properties": properties,
            "required": ["integration_receipts", "integration_allocation"],
            "additionalProperties": False,
        }


class _DeliveryServiceTool(ToolPort):
    tool_name = ""
    operations: dict[str, str] = {}

    def __init__(self, *, service: Any) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return self.tool_name

    @property
    def description(self) -> str:
        common = (
            "Invoke a narrow deterministic workflow-delivery operation. Raw commands, source "
            "edits, credentials, and policy bodies are rejected."
        )
        if self.tool_name == "delivery_workspace":
            return (
                f"{common} For verify and inspect, copy allocation exactly from "
                "delivery_workspace.allocate; copy integration_receipt exactly from a successful "
                "delivery_workspace.integrate result. For integrate, copy that allocation as "
                "integration and build the direct snake_case CandidateEvidence fields from the "
                "accepted child result and work order: map attemptId, candidateSha, candidateTree, "
                "requirementEvidence, verificationReceipts, and reviewReceipts to attempt_id, "
                "candidate_sha, candidate_tree, requirements, verifications, and reviews. Do not "
                "pass the child result wrapper. Copy signed receipt objects unchanged. Use the "
                "configured workstream/evidence policy_id. expected_integration_head is the "
                "integration allocation base_sha for the first child, then the previous successful "
                "integration receipt resulting_sha."
            )
        if self.tool_name == "delivery_forge":
            return (
                f"{common} Copy allocation and integration_receipt exactly from the trusted "
                "workspace results. Copy request, publication, and direct CandidateEvidence "
                "objects from the current delivery state; never substitute a child result wrapper "
                "or reconstruct signed receipts."
            )
        if self.tool_name == "delivery_evidence":
            return (
                f"{common} validate expects a direct snake_case CandidateEvidence object. Copy "
                "signed verification, review, and forge-check receipt objects unchanged from "
                "trusted tool results."
            )
        return common

    @property
    def input_schema(self) -> dict:
        payload_schemas = {
            operation: _delivery_payload_schema(method_name)
            for operation, method_name in sorted(self.operations.items())
        }
        return {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(self.operations)},
                "payload": {"type": "object"},
            },
            "required": ["operation", "payload"],
            "oneOf": [
                {
                    "properties": {
                        "operation": {"type": "string", "enum": [operation]},
                        "payload": payload_schema,
                    },
                }
                for operation, payload_schema in payload_schemas.items()
            ],
            "additionalProperties": False,
        }

    @property
    def required_permission(self) -> str:
        return "workflow:coordinate"

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        operation = str(input.get("operation") or "")
        method_name = self.operations.get(operation)
        if method_name is None:
            return _error(f"unsupported {self.name} operation: {operation}")
        payload = input.get("payload")
        if not isinstance(payload, dict):
            return _error("payload must be an object")
        if error := _payload_error(payload):
            return _error(error)
        try:
            result = _invoke_delivery_operation(
                self._service,
                method_name,
                dict(payload),
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return _error(str(exc))
        return _result(result)


class DeliveryWorkspaceTool(_DeliveryServiceTool):
    tool_name = "delivery_workspace"
    operations = {
        "allocate": "allocate_workstream",
        "verify": "run_verification",
        "integrate": "integrate_candidate",
        "inspect": "inspect_integration",
    }


class DeliveryForgeTool(_DeliveryServiceTool):
    tool_name = "delivery_forge"
    operations = {
        "resolve": "resolve_ref",
        "open": "open_review",
        "publish": "publish_branch",
        "inspect": "inspect_candidate",
        "merge": "conditional_merge",
        "reconcile": "reconcile_merge",
    }


class DeliveryEvidenceTool(_DeliveryServiceTool):
    tool_name = "delivery_evidence"
    operations = {"validate": "validate_evidence", "describe": "describe_policy"}


def _invoke_delivery_operation(service: Any, method_name: str, payload: dict[str, Any]) -> Any:
    """Bind untrusted JSON to the typed delivery service surface."""
    method = getattr(service, method_name)
    if method_name == "describe_policy":
        return method(
            campaign_id=_required_string(payload, "campaign_id"),
            repository=_required_string(payload, "repository"),
            policy_id=_required_string(payload, "policy_id"),
        )
    if method_name == "allocate_workstream":
        return method(WorkstreamSpec.model_validate(payload))
    if method_name == "run_verification":
        return method(
            WorkspaceAllocation.model_validate(_required_object(payload, "allocation")),
            attempt_id=_required_string(payload, "attempt_id"),
            candidate_sha=_required_string(payload, "candidate_sha"),
            contract_id=_required_string(payload, "contract_id"),
        )
    if method_name == "integrate_candidate":
        return method(
            WorkspaceAllocation.model_validate(_required_object(payload, "integration")),
            expected_integration_head=_required_string(payload, "expected_integration_head"),
            evidence=CandidateEvidence.model_validate(_required_object(payload, "evidence")),
            policy_id=_required_string(payload, "policy_id"),
        )
    if method_name == "inspect_integration":
        return method(
            WorkspaceAllocation.model_validate(_required_object(payload, "allocation")),
            IntegrationReceipt.model_validate(_required_object(payload, "integration_receipt")),
            policy_id=_required_string(payload, "policy_id"),
        )
    if method_name == "inspect_candidate":
        kwargs = {"policy_id": _required_string(payload, "policy_id")}
        if "campaign_id" in inspect.signature(method).parameters:
            kwargs["campaign_id"] = _required_string(payload, "campaign_id")
        return method(
            _required_string(payload, "repository"),
            _required_integer(payload, "review_number"),
            **kwargs,
        )
    if method_name == "resolve_ref":
        return method(
            _required_string(payload, "repository"),
            _required_string(payload, "ref"),
        )
    if method_name == "open_review":
        return method(ReviewRequest.model_validate(payload))
    if method_name == "publish_branch":
        return method(
            WorkspaceAllocation.model_validate(_required_object(payload, "allocation")),
            IntegrationReceipt.model_validate(_required_object(payload, "integration_receipt")),
            BranchPublicationRequest.model_validate(_required_object(payload, "publication")),
            policy_id=_required_string(payload, "policy_id"),
        )
    if method_name == "conditional_merge":
        return method(
            MergeRequest.model_validate(_required_object(payload, "request")),
            evidence=CandidateEvidence.model_validate(_required_object(payload, "evidence")),
            policy_id=_required_string(payload, "policy_id"),
        )
    if method_name == "reconcile_merge":
        return method(MergeRequest.model_validate(_required_object(payload, "request")))
    if method_name == "validate_evidence":
        return method(
            CandidateEvidence.model_validate(_required_object(payload, "evidence")),
            policy_id=_required_string(payload, "policy_id"),
        )
    raise ValueError(f"unsupported delivery service method: {method_name}")


def _delivery_payload_schema(method_name: str) -> dict[str, Any]:
    if method_name == "describe_policy":
        return _strict_object(
            {
                "campaign_id": {"type": "string", "minLength": 1},
                "repository": {"type": "string", "minLength": 1},
                "policy_id": {"type": "string", "minLength": 1},
            }
        )
    if method_name == "allocate_workstream":
        return _inline_model_schema(WorkstreamSpec)
    if method_name == "run_verification":
        return _strict_object(
            {
                "allocation": _copied_object_schema(
                    "Copy the exact WorkspaceAllocation returned by delivery_workspace.allocate."
                ),
                "attempt_id": {"type": "string", "minLength": 1},
                "candidate_sha": {"type": "string", "minLength": 1},
                "contract_id": {"type": "string", "minLength": 1},
            }
        )
    if method_name == "integrate_candidate":
        return _strict_object(
            {
                "integration": _copied_object_schema(
                    "Copy the exact integration WorkspaceAllocation returned by allocate."
                ),
                "expected_integration_head": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Use integration.base_sha for the first child, then the previous "
                        "successful integration receipt resulting_sha."
                    ),
                },
                "evidence": _candidate_evidence_schema(),
                "policy_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Use the configured workstream/evidence policy ID.",
                },
            }
        )
    if method_name == "inspect_integration":
        return _strict_object(
            {
                "allocation": _copied_object_schema(
                    "Copy the exact WorkspaceAllocation returned by delivery_workspace.allocate."
                ),
                "integration_receipt": _copied_object_schema(
                    "Copy the exact signed IntegrationReceipt returned by integrate."
                ),
                "policy_id": {"type": "string", "minLength": 1},
            }
        )
    if method_name == "resolve_ref":
        return _strict_object(
            {
                "repository": {"type": "string", "minLength": 1},
                "ref": {"type": "string", "minLength": 1},
            }
        )
    if method_name == "open_review":
        return _inline_model_schema(ReviewRequest)
    if method_name == "publish_branch":
        return _strict_object(
            {
                "allocation": {"type": "object"},
                "integration_receipt": {"type": "object"},
                "publication": _inline_model_schema(BranchPublicationRequest),
                "policy_id": {"type": "string", "minLength": 1},
            }
        )
    if method_name == "inspect_candidate":
        return _strict_object(
            {
                "repository": {"type": "string", "minLength": 1},
                "review_number": {"type": "integer", "minimum": 1},
                "policy_id": {"type": "string", "minLength": 1},
                "campaign_id": {"type": "string", "minLength": 1},
            }
        )
    if method_name == "conditional_merge":
        return _strict_object(
            {
                "request": _inline_model_schema(MergeRequest),
                "evidence": _candidate_evidence_schema(compact=True),
                "policy_id": {"type": "string", "minLength": 1},
            }
        )
    if method_name == "reconcile_merge":
        return _strict_object({"request": _inline_model_schema(MergeRequest)})
    if method_name == "validate_evidence":
        return _strict_object(
            {
                "evidence": _candidate_evidence_schema(),
                "policy_id": {"type": "string", "minLength": 1},
            }
        )
    raise ValueError(f"unsupported delivery service method: {method_name}")


def _workstream_proposal_schema() -> dict[str, Any]:
    workspace_schema = _inline_model_schema(WorkspaceAllocation)
    workstream_input = _strict_object(
        {
            "childKey": {"type": "string", "minLength": 1},
            "objective": {"type": "string", "minLength": 1},
            "requirementIds": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "repository": {"type": "string", "minLength": 1},
            "baseSha": {"type": "string", "minLength": 1},
            "allowedPaths": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "expectedOutputs": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "testContractIds": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "dependencies": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "workspace": workspace_schema,
        }
    )
    properties: dict[str, Any] = {
        "key": {"type": "string", "minLength": 1},
        "objective": {"type": "string", "minLength": 1},
        "requirementIds": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "dependencies": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "input": workstream_input,
        "inputDigest": {
            "type": "string",
            "pattern": r"^sha256:[0-9a-f]{64}$",
            "description": "Optional assertion; Ting derives this digest from canonical input.",
        },
        "planDigest": {
            "type": "string",
            "pattern": r"^sha256:[0-9a-f]{64}$",
            "description": "Optional assertion; Ting derives this digest from plan_revision.",
        },
        "repository": {"type": "string", "minLength": 1},
        "baseSha": {"type": "string", "minLength": 1},
        "budgetUnits": {"type": "integer", "minimum": 1},
        "deadline": {"type": "string", "format": "date-time"},
        "agentId": {"type": "string", "minLength": 1},
        "skillId": {"type": "string", "minLength": 1},
        "workspace": workspace_schema,
    }
    required = [
        "key",
        "objective",
        "requirementIds",
        "dependencies",
        "input",
        "repository",
        "baseSha",
        "budgetUnits",
        "deadline",
        "agentId",
        "skillId",
        "workspace",
    ]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _copied_object_schema(description: str) -> dict[str, Any]:
    return {"type": "object", "description": description}


def _candidate_evidence_schema(*, compact: bool = False) -> dict[str, Any]:
    """Compact model-facing shape; typed services still validate every nested receipt."""
    scalar = {"type": "string", "minLength": 1}
    requirement = _strict_object(
        {
            "requirement_id": scalar,
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
        }
    )
    if compact:
        requirement.pop("additionalProperties")
    schema = {
        "type": "object",
        "properties": {
            "campaign_id": scalar,
            "workstream_key": scalar,
            "attempt_id": scalar,
            "worker_id": scalar,
            "repository": scalar,
            "base_sha": scalar,
            "candidate_sha": scalar,
            "candidate_tree": scalar,
            "requirements": {"type": "array", "minItems": 1, "items": requirement},
            "verifications": {
                "type": "array",
                "items": (
                    {"type": "object"}
                    if compact
                    else _copied_object_schema(
                        "Copy each signed verification receipt unchanged from verificationReceipts."
                    )
                ),
            },
            "reviews": {
                "type": "array",
                "items": (
                    {"type": "object"}
                    if compact
                    else _copied_object_schema(
                        "Copy each signed review receipt unchanged from reviewReceipts."
                    )
                ),
            },
            "checks": (
                {"anyOf": [{"type": "object"}, {"type": "null"}]}
                if compact
                else {
                    "description": "Copy the signed forge-check receipt unchanged when present.",
                    "anyOf": [{"type": "object"}, {"type": "null"}],
                }
            ),
        },
        "required": [
            "campaign_id",
            "workstream_key",
            "attempt_id",
            "worker_id",
            "repository",
            "base_sha",
            "candidate_sha",
            "candidate_tree",
            "requirements",
        ],
    }
    if compact:
        return schema
    schema["description"] = (
        "Direct snake_case CandidateEvidence. Map accepted child result fields; do not pass "
        "the child result wrapper. Copy signed receipt objects unchanged."
    )
    schema["additionalProperties"] = False
    return schema


def _inline_model_schema(model: Any) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value: Any) -> Any:
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            target = definitions.get(name)
            if not isinstance(target, dict):
                raise ValueError(f"unresolved delivery schema reference: {ref}")
            return inline({**target, **{key: item for key, item in value.items() if key != "$ref"}})
        return {key: inline(item) for key, item in value.items()}

    return inline(schema)


def _required_object(payload: dict[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"payload.{field} must be an object")
    return value


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"payload.{field} must be a non-empty string")
    return value.strip()


def _required_integer(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int:
        raise ValueError(f"payload.{field} must be an integer")
    return value
