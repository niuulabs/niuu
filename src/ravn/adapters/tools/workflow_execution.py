"""Coordinator-safe tools for the domain-neutral durable workflow execution lifecycle.

Fan-out and join, durable waits, retry, replies, and cancellation serve any
workflow that expands into a bounded child DAG. A workflow that needs
something more specific (code delivery today) layers its own contract,
service, and tool set over this one instead of teaching this module its
vocabulary — see ``ravn.adapters.tools.delivery``.
"""

from __future__ import annotations

import json
from typing import Any

from ravn.domain.models import ToolResult
from ravn.domain.workflow_execution import WorkflowExecutionToolPort
from ravn.ports.tool import ToolPort

_FORBIDDEN_PAYLOAD_FIELDS = frozenset(
    {"command", "commands", "shell", "script", "policy", "acceptance_policy"}
)

_BASE_PROPERTIES: dict[str, Any] = {
    "campaign_id": {"type": "string"},
    "parent_node_id": {"type": "string"},
    "generation": {"type": "integer", "minimum": 1},
}
_BASE_REQUIRED = ["campaign_id", "parent_node_id"]


class WorkflowExecutionToolBase(ToolPort):
    """Shared plumbing for a coordinator-safe tool over Ting's durable execution facade."""

    operation = ""
    tool_name = ""
    """Optional exact tool name override; default is ``workflow_execution_{operation}``."""

    def __init__(self, *, service: WorkflowExecutionToolPort) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return self.tool_name or f"workflow_execution_{self.operation}"

    @property
    def required_permission(self) -> str:
        return "workflow:coordinate"

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        if error := _payload_error(input):
            return _error(error)
        if error := self._unknown_field_error(input):
            return _error(error)
        method = getattr(self._service, self.operation)
        try:
            if self.operation == "cancel":
                result = await method()
            else:
                result = await method(dict(input))
        except Exception as exc:
            return _error(str(exc))
        return _result(result)

    def _unknown_field_error(self, input: dict) -> str:
        allowed = set(self.input_schema["properties"])
        unknown = set(input) - allowed
        if not unknown:
            return ""
        if self.operation in ("cancel", "reconcile") and "child_keys" in unknown:
            return (
                f"{self.name} has no per-child targeting; child_keys is not accepted. "
                "workflow_execution_cancel stops the entire execution, every child, and this "
                "coordinator's own session, irreversibly. There is no per-child cancel — use "
                "workflow_execution_retry to recover one child instead."
            )
        return f"unexpected field(s) for {self.name}: {', '.join(sorted(unknown))}"


class WorkflowExecutionExpandTool(WorkflowExecutionToolBase):
    """Persist and dispatch a validated generation of durable child workflows.

    The input is exactly the generic execution router's expansion body: a
    plan revision and a list of child proposals declaring their own key,
    objective, dependencies, input, budget, deadline, and agent/skill
    identity. Nothing here is workflow-specific — a subworkflow node's own
    ``inputSchema``/``resultSchema`` validate what a child actually carries.
    """

    operation = "expand"

    @property
    def description(self) -> str:
        return "Persist and dispatch a validated generation of durable child workflows."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                **_BASE_PROPERTIES,
                "plan_revision": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                    "description": "Approved plan revision; Ting derives the canonical digest.",
                },
                "children": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": _child_proposal_schema(),
                },
            },
            "required": [*_BASE_REQUIRED, "generation", "plan_revision", "children"],
            "additionalProperties": False,
        }


class WorkflowExecutionReconcileTool(WorkflowExecutionToolBase):
    operation = "reconcile"

    @property
    def description(self) -> str:
        return "Read durable child states and validate completed child result contracts."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": dict(_BASE_PROPERTIES),
            "required": list(_BASE_REQUIRED),
            "additionalProperties": False,
        }


class WorkflowExecutionRetryTool(WorkflowExecutionToolBase):
    operation = "retry"

    @property
    def description(self) -> str:
        return "Retry an eligible child using its exact current durable attempt ID."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "child_key": {"type": "string", "minLength": 1},
                "attempt_id": {"type": "string", "format": "uuid"},
            },
            "required": ["child_key", "attempt_id"],
            "additionalProperties": False,
        }


class WorkflowExecutionCancelTool(WorkflowExecutionToolBase):
    operation = "cancel"

    @property
    def description(self) -> str:
        return (
            "Irreversibly cancel the ENTIRE durable execution: every child attempt, the whole "
            "campaign, and this coordinator's own session. There is no per-child cancel; a "
            "single stuck child cannot be cancelled on its own. Use workflow_execution_retry to "
            "recover one child instead, or workflow_execution_reconcile plus a fresh generation "
            "for a broader repair."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": dict(_BASE_PROPERTIES),
            "required": list(_BASE_REQUIRED),
            "additionalProperties": False,
        }


class WorkflowExecutionMessageTool(WorkflowExecutionToolBase):
    operation = "message"

    @property
    def description(self) -> str:
        return (
            "Reply to one exact outstanding child input using the current attempt_id. Copy "
            "metadata.requestId for a question, or metadata.gateId plus gateDecision for a gate, "
            "from the blocker notification. Reuse message_id unchanged when retrying the same "
            "delivery."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                **_BASE_PROPERTIES,
                "child_key": {"type": "string", "minLength": 1},
                "attempt_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Exact current attemptId from the blocker notification.",
                },
                "answer": {"type": "string", "minLength": 1},
                "metadata": {
                    "type": "object",
                    "description": (
                        "Exact outstanding input identity: requestId for a question; or gateId "
                        "and gateDecision for a gate."
                    ),
                    "properties": {
                        "requestId": {"type": "string", "minLength": 1},
                        "gateId": {"type": "string", "minLength": 1},
                        "gateDecision": {
                            "type": "string",
                            "enum": ["approve", "request_changes"],
                        },
                    },
                    "oneOf": [
                        {"required": ["requestId"]},
                        {"required": ["gateId", "gateDecision"]},
                    ],
                    "additionalProperties": False,
                },
                "message_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Stable idempotency identity; reuse it unchanged for retries.",
                },
            },
            "required": [
                *_BASE_REQUIRED,
                "child_key",
                "attempt_id",
                "answer",
                "metadata",
                "message_id",
            ],
            "additionalProperties": False,
        }


class WorkflowExecutionWaitTool(WorkflowExecutionToolBase):
    operation = "wait"

    @property
    def description(self) -> str:
        return (
            "Persist a restart-safe wait against the exact pinned wait node, for one condition "
            "type it declares. After registration, yield the workflow; the workflow's declared "
            "continuation event resumes the coordinator with the terminal observation. This tool "
            "never merges."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "nodeId": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Exact id of the pinned graph's wait node this wait is registered against."
                    ),
                },
                "conditionType": {
                    "type": "string",
                    "minLength": 1,
                    "description": "One of the wait node's declared condition types.",
                },
                "request": {
                    "type": "object",
                    "description": "Condition-specific identity, exactly as the wait node expects.",
                },
            },
            "required": ["nodeId", "conditionType", "request"],
            "additionalProperties": False,
        }


def _child_proposal_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "key": {"type": "string", "minLength": 1},
            "objective": {"type": "string", "minLength": 1},
            "dependencies": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "input": {
                "type": "object",
                "description": (
                    "Validated against the subworkflow node's declared inputSchema when present."
                ),
            },
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
            "budgetUnits": {"type": "integer", "minimum": 1},
            "deadline": {"type": "string", "format": "date-time"},
            "agentId": {"type": "string", "minLength": 1},
            "skillId": {"type": "string", "minLength": 1},
            "context": {"type": "object"},
        },
        "required": ["key", "objective", "budgetUnits", "deadline", "agentId", "skillId"],
        "additionalProperties": False,
    }


def _payload_error(value: Any, *, path: str = "payload") -> str:
    if isinstance(value, list):
        for index, item in enumerate(value):
            if error := _payload_error(item, path=f"{path}[{index}]"):
                return error
        return ""
    if not isinstance(value, dict):
        return ""
    for key, item in value.items():
        normalized = str(key).casefold().replace("-", "_")
        if normalized in _FORBIDDEN_PAYLOAD_FIELDS:
            return f"{path}.{key} is not accepted; use a pinned contract or policy id"
        if error := _payload_error(item, path=f"{path}.{key}"):
            return error
    return ""


def _result(value: Any) -> ToolResult:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    return ToolResult(tool_call_id="", content=json.dumps(value, indent=2, default=str))


def _error(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)
