"""Commission a learned-tool build via an A2A workflow task.

Speaks plain A2A v1.0 JSON-RPC (SendMessage / GetTask) against any agent
that publishes workflows as skills — Ting's A2A facade or a foreign
platform. The agent card replaces Niuu-specific workflow discovery: skills
are selected by explicit id or by tag/name selector. Auth reuses the
workload-identity client, so there is no second credential surface.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from niuu.observability import get_observability
from ravn.adapters.tool_build._contract import (
    CANONICAL_ARTIFACT_FILENAME,
    build_prompts,
    decode_canonical_document,
    parse_tool_build_document,
    parse_tool_build_response,
)
from ravn.adapters.tool_build.gate_review import GateReviewer, QuestionAnswerer
from ravn.adapters.tool_build.http import AsyncJsonHttpClient, client_from_workload_identity
from ravn.domain.capability_catalog import (
    WorkflowCapability,
    WorkflowSelector,
    select_workflow,
)
from ravn.ports.tool_build_backend import (
    ToolBuildBackend,
    ToolBuildError,
    ToolBuildInputRequiredError,
    ToolBuildRequest,
    ToolBuildResult,
)

logger = logging.getLogger(__name__)

#: Scopes the launch and its downstream Forge session spawn enforce.
A2A_BUILD_SCOPES = ("ting:workflow:launch", "forge:session:create")

_A2A_HEADERS = {"A2A-Version": "1.0"}
_JSONRPC_BINDING = "JSONRPC"
_COMPLETED_STATE = "TASK_STATE_COMPLETED"
_INPUT_REQUIRED_STATE = "TASK_STATE_INPUT_REQUIRED"
_FAILED_STATES = frozenset({"TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"})
_TERMINAL_STATES = frozenset({_COMPLETED_STATE, *_FAILED_STATES})

#: Reply errors that mean the gate resolved between poll and reply (projector
#: lag or auto-forward) — benign; keep polling instead of failing the build.
_STALE_GATE_MARKERS = ("no pending gate", "not awaiting input")

#: Reply errors that mean the question resolved between poll and reply
#: (answered elsewhere, e.g. by an operator) — benign; keep polling.
_STALE_QUESTION_MARKERS = ("no pending question", "not awaiting input")


class A2AToolBuildBackend(ToolBuildBackend):
    """Launch a workflow task over A2A, poll it, and retrieve the artifact."""

    def __init__(
        self,
        *,
        card_url: str,
        workflow_id: str = "",
        workflow_selector: dict[str, Any] | None = None,
        client: AsyncJsonHttpClient | None = None,
        external_token_env: str = "",
        workload_token_file: str = "",
        workload_exchange_url: str = "",
        workload_audiences: list[str] | None = None,
        repo: str = "",
        branch: str = "",
        model: str = "",
        connection_id: str = "",
        max_poll_attempts: int = 120,
        poll_interval_seconds: float = 5.0,
        gate_reviewer: GateReviewer | None = None,
        question_answerer: QuestionAnswerer | None = None,
        max_gate_rounds: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not card_url:
            raise ToolBuildError("a2a backend requires card_url")
        self._card_url = card_url
        self._client = (
            client
            if client is not None
            else client_from_workload_identity(
                base_url=_origin(card_url),
                external_token_env=external_token_env,
                workload_token_file=workload_token_file,
                workload_exchange_url=workload_exchange_url,
                workload_audiences=workload_audiences,
                workload_scopes=list(A2A_BUILD_SCOPES),
            )
        )
        self._workflow_id = workflow_id
        self._workflow_selector = _selector_from_dict(workflow_selector)
        self._repo = repo
        self._branch = branch
        self._model = model
        self._connection_id = connection_id
        self._max_poll_attempts = max_poll_attempts
        self._poll_interval = poll_interval_seconds
        # INPUT_REQUIRED handling. A pending peer QUESTION (help_needed) is
        # answered with information by the question_answerer; a pending
        # workflow GATE is decided approve/request_changes by the
        # gate_reviewer — both act within the resident's autonomy grant.
        # Without the matching callable, the build fails loudly; nothing is
        # silently auto-approved or guessed.
        self._gate_reviewer = gate_reviewer
        self._question_answerer = question_answerer
        self._max_gate_rounds = max_gate_rounds
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "a2a"

    @property
    def supports_restart_recovery(self) -> bool:
        return True

    @property
    def card_url(self) -> str:
        """Configured agent-card URL (read-only, for diagnostics)."""
        return self._card_url

    @property
    def client(self) -> AsyncJsonHttpClient:
        """The authenticated HTTP client (read-only, for diagnostics)."""
        return self._client

    @property
    def workflow_id(self) -> str:
        """Configured workflow id, or empty when discovery via selector is used."""
        return self._workflow_id

    @property
    def workflow_selector(self) -> WorkflowSelector:
        """Configured skill selector (names/tags) used to discover the builder."""
        return self._workflow_selector

    async def build(self, request: ToolBuildRequest) -> ToolBuildResult:
        telemetry = get_observability()
        attributes = {
            "ravn.tool_build.backend": self.name,
            "ravn.tool_build.name": request.name,
            "ravn.tool_build.operation.id": request.operation_id,
            "a2a.card.url": self._card_url,
        }
        with telemetry.span("ravn.a2a.tool_build", attributes=attributes) as span:
            telemetry.event("ravn.tool_build.requested", attributes=attributes, content=request)
            try:
                result = await self._build_observed(request)
            except ToolBuildInputRequiredError as exc:
                span.set_attribute("ravn.tool_build.outcome", "input_required")
                span.set_attribute("a2a.task.id", exc.task_id)
                span.set_attribute("a2a.input.kind", exc.input_kind)
                telemetry.event(
                    "ravn.a2a.tool_build.input_required",
                    attributes={
                        **attributes,
                        "ravn.tool_build.outcome": "input_required",
                        "a2a.task.id": exc.task_id,
                        "a2a.input.kind": exc.input_kind,
                    },
                    content={
                        "prompt": exc.prompt,
                        "continuation": exc.continuation,
                    },
                )
                telemetry.count(
                    "ravn.tool_build.operations",
                    attributes={
                        "ravn.tool_build.backend": self.name,
                        "ravn.tool_build.outcome": "input_required",
                    },
                )
                raise
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.event(
                    "ravn.a2a.tool_build.failed",
                    attributes={
                        **attributes,
                        "error.type": type(exc).__name__,
                    },
                    content={"error": str(exc)},
                )
                telemetry.count(
                    "ravn.tool_build.operations",
                    attributes={
                        "ravn.tool_build.backend": self.name,
                        "ravn.tool_build.outcome": "error",
                        "error.type": type(exc).__name__,
                    },
                )
                raise
            span.set_attribute("ravn.tool_build.outcome", "completed")
            telemetry.event(
                "ravn.tool_build.completed",
                attributes={
                    **attributes,
                    "ravn.tool_build.outcome": "completed",
                    "ravn.tool_build.manifest.name": str(result.manifest.get("name") or ""),
                },
                content={
                    "manifest": result.manifest,
                    "build_evidence": result.build_evidence,
                    "provenance": result.provenance,
                },
            )
            telemetry.count(
                "ravn.tool_build.operations",
                attributes={
                    "ravn.tool_build.backend": self.name,
                    "ravn.tool_build.outcome": "completed",
                },
            )
            return result

    async def _build_observed(self, request: ToolBuildRequest) -> ToolBuildResult:
        endpoint, workflow_id = await self._resolve_endpoint_and_workflow()
        telemetry = get_observability()
        continuation = request.continuation
        task_id = str(continuation.get("task_id") or "")
        exchanges = _continuation_exchanges(continuation)
        rounds = int(continuation.get("round") or 0)
        if task_id:
            exchange = await self._resume_task_input(endpoint, request)
            exchanges.append(exchange)
            telemetry.event(
                "ravn.a2a.task.resumed",
                attributes={
                    "a2a.task.id": task_id,
                    "a2a.skill.id": workflow_id,
                    "a2a.input.kind": str(continuation.get("input_kind") or ""),
                },
                content=exchange,
            )
        else:
            _system, initial_prompt = build_prompts(request)
            task = await self._find_task_by_context(endpoint, request.operation_id)
            if task is None:
                task = await self._send_message(
                    endpoint,
                    prompt=initial_prompt,
                    workflow_id=workflow_id,
                    request=request,
                )
            task_id = str(task.get("id") or "")
            if not task_id:
                raise ToolBuildError("A2A SendMessage returned no task id")
            telemetry.event(
                "ravn.a2a.task.started",
                attributes={"a2a.task.id": task_id, "a2a.skill.id": workflow_id},
                content=task,
            )
        telemetry.set_attributes(
            {
                "a2a.task.id": task_id,
                "a2a.skill.id": workflow_id,
                "a2a.endpoint": endpoint,
            }
        )

        final, gate_exchanges = await self._poll_answering_gates(
            endpoint,
            task_id,
            request,
            exchanges=exchanges,
            rounds=rounds,
        )
        state = _task_state(final)
        if state in _FAILED_STATES:
            raise ToolBuildError(f"A2A task {task_id} ended in state {state!r}")
        if state != _COMPLETED_STATE:
            raise ToolBuildError(
                f"A2A task {task_id} did not finish within "
                f"{self._max_poll_attempts} polls (last state {state!r})"
            )

        result, retrieval = await self._retrieve_artifact(final, request)
        build_evidence: dict[str, Any] = {"retrieval": retrieval}
        provenance: dict[str, Any] = {
            "backend": self.name,
            "a2a_task_id": task_id,
            "a2a_card_url": self._card_url,
            "workflow_id": workflow_id,
            "build_request": request.build_request,
        }
        if gate_exchanges:
            # The gate question/answer transcript is part of the build's
            # provenance — reviewers see exactly what the workflow asked and
            # how the commissioning Valkyrie answered.
            build_evidence["gate_exchanges"] = gate_exchanges
            provenance["gate_exchanges"] = gate_exchanges
        return ToolBuildResult(
            manifest=result.manifest,
            tool_code=result.tool_code,
            test_code=result.test_code,
            requirements=result.requirements,
            build_evidence=build_evidence,
            provenance=provenance,
        )

    # -- Gate-aware polling ------------------------------------------------ #

    async def _poll_answering_gates(
        self,
        endpoint: str,
        task_id: str,
        request: ToolBuildRequest,
        *,
        exchanges: list[dict[str, Any]] | None = None,
        rounds: int = 0,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Poll the task, answering workflow gates until a terminal state.

        Every INPUT_REQUIRED round is decided by the injected gate reviewer
        (the commissioning Valkyrie's own judgment) and recorded verbatim —
        the question asked and the answer given become part of the build
        evidence. Rounds are bounded so a gate loop cannot spin forever.
        """
        exchanges = list(exchanges or [])
        task: dict[str, Any] = {}
        telemetry = get_observability()
        attributes = {
            "a2a.task.id": task_id,
            "a2a.endpoint": endpoint,
            "a2a.task.max_poll_attempts": self._max_poll_attempts,
        }
        with telemetry.span("ravn.a2a.task.wait", attributes=attributes) as span:
            try:
                for attempt in range(self._max_poll_attempts):
                    task = await self._get_task(endpoint, task_id)
                    state = _task_state(task)
                    span.set_attribute("a2a.task.state", state)
                    span.set_attribute("a2a.task.poll_attempt", attempt + 1)
                    telemetry.event(
                        "ravn.a2a.task.state",
                        attributes={
                            "a2a.task.id": task_id,
                            "a2a.task.state": state,
                            "a2a.task.poll_attempt": attempt + 1,
                        },
                        content=task,
                    )
                    telemetry.count(
                        "ravn.a2a.task.polls",
                        attributes={"a2a.task.state": state},
                    )
                    if state in _TERMINAL_STATES:
                        return task, exchanges
                    if state == _INPUT_REQUIRED_STATE:
                        rounds += 1
                        if rounds > self._max_gate_rounds:
                            raise ToolBuildError(
                                f"A2A task {task_id} exceeded {self._max_gate_rounds} "
                                "input rounds without completing"
                            )
                        question = _first_pending_question(task)
                        if question:
                            if self._question_answerer is None:
                                raise _input_required(
                                    task_id=task_id,
                                    input_kind="question",
                                    payload=question,
                                    exchanges=exchanges,
                                    round=rounds,
                                )
                            exchange = await self._answer_question(
                                endpoint, task_id, question, request, round=rounds
                            )
                        else:
                            gate = _first_pending_gate(task)
                            if not gate:
                                raise _input_required(
                                    task_id=task_id,
                                    input_kind="input",
                                    payload={},
                                    exchanges=exchanges,
                                    round=rounds,
                                )
                            if self._gate_reviewer is None:
                                raise _input_required(
                                    task_id=task_id,
                                    input_kind="gate",
                                    payload=gate,
                                    exchanges=exchanges,
                                    round=rounds,
                                )
                            exchange = await self._answer_gate(
                                endpoint, task_id, task, request, round=rounds
                            )
                        exchanges.append(exchange)
                    await self._sleep(self._poll_interval)
            except ToolBuildInputRequiredError as exc:
                span.set_attribute("a2a.task.state", _INPUT_REQUIRED_STATE)
                span.set_attribute("a2a.input.kind", exc.input_kind)
                raise
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                raise
            last_state = _task_state(task)
            message = (
                f"A2A task {task_id} exhausted {self._max_poll_attempts} polls "
                f"in state {last_state!r}"
            )
            telemetry.mark_error(span, "poll_exhausted", message)
            telemetry.event(
                "ravn.a2a.task.poll_exhausted",
                attributes={
                    "a2a.task.id": task_id,
                    "a2a.task.state": last_state,
                    "a2a.task.poll_attempt": self._max_poll_attempts,
                },
            )
            return task, exchanges

    async def _resume_task_input(
        self,
        endpoint: str,
        request: ToolBuildRequest,
    ) -> dict[str, Any]:
        continuation = request.continuation
        task_id = str(continuation.get("task_id") or "")
        input_kind = str(continuation.get("input_kind") or "")
        answer = str(continuation.get("answer") or "").strip()
        if not task_id or not answer:
            raise ToolBuildError("A2A build continuation requires task_id and answer")

        metadata = continuation.get("reply_metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if input_kind == "gate":
            decision = str(metadata.get("gateDecision") or "").strip().lower()
            if decision not in {"approve", "request_changes"}:
                raise ToolBuildError(
                    "A2A gate continuation requires gateDecision=approve or request_changes"
                )
            if decision == "request_changes" and not answer:
                raise ToolBuildError("A2A gate change request requires review notes")

        payload = continuation.get("input_payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        exchange: dict[str, Any] = {
            "round": int(continuation.get("round") or 0),
            "kind": input_kind,
            "answer": answer,
            "delivery": "delivered",
        }
        if input_kind == "question":
            exchange["question"] = payload
        elif input_kind == "gate":
            exchange["gate"] = payload
            exchange["decision"] = str(metadata.get("gateDecision") or "")
            exchange["notes"] = answer
        else:
            exchange["input"] = payload
        try:
            await self._rpc(
                endpoint,
                "SendMessage",
                {
                    "message": {
                        "messageId": str(uuid4()),
                        "taskId": task_id,
                        "role": "ROLE_USER",
                        "parts": [{"text": answer}],
                        "metadata": metadata,
                    }
                },
            )
        except ToolBuildError as exc:
            markers = ()
            if input_kind == "question":
                markers = _STALE_QUESTION_MARKERS
            elif input_kind == "gate":
                markers = _STALE_GATE_MARKERS
            if not any(marker in str(exc) for marker in markers):
                raise
            exchange["delivery"] = "stale"
            logger.warning(
                "A2A %s continuation for task %s was stale: %s",
                input_kind,
                task_id,
                exc,
            )
        get_observability().event(
            "ravn.a2a.task.input",
            attributes={
                "a2a.task.id": task_id,
                "a2a.input.kind": input_kind,
                "a2a.input.round": int(continuation.get("round") or 0),
                "a2a.input.delivery": str(exchange["delivery"]),
                "ravn.a2a.input.source": "resident_continuation",
            },
            content=exchange,
        )
        return exchange

    async def _answer_question(
        self,
        endpoint: str,
        task_id: str,
        question: dict[str, Any],
        request: ToolBuildRequest,
        *,
        round: int,  # noqa: A002
    ) -> dict[str, Any]:
        """Answer a peer's genuine question with information, not a verdict."""
        assert self._question_answerer is not None
        answer = str(await self._question_answerer(request, question) or "").strip()
        if not answer:
            raise ToolBuildError("question answerer returned an empty answer")

        metadata: dict[str, Any] = {}
        request_id = str(question.get("requestId") or "")
        if request_id:
            metadata["requestId"] = request_id
        exchange: dict[str, Any] = {
            "round": round,
            "kind": "question",
            "question": question,
            "answer": answer,
        }
        try:
            await self._rpc(
                endpoint,
                "SendMessage",
                {
                    "message": {
                        "messageId": str(uuid4()),
                        "taskId": task_id,
                        "role": "ROLE_USER",
                        "parts": [{"text": answer}],
                        "metadata": metadata,
                    }
                },
            )
        except ToolBuildError as exc:
            if any(marker in str(exc) for marker in _STALE_QUESTION_MARKERS):
                logger.warning("A2A question reply for task %s was stale: %s", task_id, exc)
                exchange["delivery"] = "stale"
                get_observability().event(
                    "ravn.a2a.task.input",
                    attributes={
                        "a2a.task.id": task_id,
                        "a2a.input.kind": "question",
                        "a2a.input.round": round,
                        "a2a.input.delivery": "stale",
                    },
                )
                return exchange
            raise
        exchange["delivery"] = "delivered"
        get_observability().event(
            "ravn.a2a.task.input",
            attributes={
                "a2a.task.id": task_id,
                "a2a.input.kind": "question",
                "a2a.input.round": round,
                "a2a.input.delivery": "delivered",
            },
            content=exchange,
        )
        logger.info(
            "A2A question round %d for task %s: answered %s (%s)",
            round,
            task_id,
            str(question.get("persona") or "peer"),
            str(question.get("question") or "")[:120],
        )
        return exchange

    async def _answer_gate(
        self,
        endpoint: str,
        task_id: str,
        task: dict[str, Any],
        request: ToolBuildRequest,
        *,
        round: int,  # noqa: A002
    ) -> dict[str, Any]:
        assert self._gate_reviewer is not None
        gate = _first_pending_gate(task)
        decision, notes = await self._gate_reviewer(request, gate)
        decision = str(decision or "").strip().lower()
        if decision not in {"approve", "request_changes"}:
            raise ToolBuildError(f"gate reviewer returned invalid decision {decision!r}")
        notes = str(notes or "").strip()
        if decision == "request_changes" and not notes:
            raise ToolBuildError("gate reviewer requested changes without notes")

        metadata: dict[str, Any] = {"gateDecision": decision}
        gate_id = str(gate.get("gateId") or "")
        if gate_id:
            metadata["gateId"] = gate_id
        exchange: dict[str, Any] = {
            "round": round,
            "gate": gate,
            "decision": decision,
            "notes": notes,
        }
        try:
            await self._rpc(
                endpoint,
                "SendMessage",
                {
                    "message": {
                        "messageId": str(uuid4()),
                        "taskId": task_id,
                        "role": "ROLE_USER",
                        "parts": [{"text": notes or "Approved."}],
                        "metadata": metadata,
                    }
                },
            )
        except ToolBuildError as exc:
            if any(marker in str(exc) for marker in _STALE_GATE_MARKERS):
                # The gate resolved between poll and reply (projector lag or
                # auto-forward) — benign; record it and keep polling.
                logger.warning("A2A gate reply for task %s was stale: %s", task_id, exc)
                exchange["delivery"] = "stale"
                get_observability().event(
                    "ravn.a2a.task.input",
                    attributes={
                        "a2a.task.id": task_id,
                        "a2a.input.kind": "gate",
                        "a2a.input.round": round,
                        "a2a.input.delivery": "stale",
                        "a2a.gate.decision": decision,
                    },
                )
                return exchange
            raise
        exchange["delivery"] = "delivered"
        get_observability().event(
            "ravn.a2a.task.input",
            attributes={
                "a2a.task.id": task_id,
                "a2a.input.kind": "gate",
                "a2a.input.round": round,
                "a2a.input.delivery": "delivered",
                "a2a.gate.decision": decision,
            },
            content=exchange,
        )
        logger.info(
            "A2A gate round %d for task %s: %s — %s",
            round,
            task_id,
            decision,
            str(gate.get("label") or "unnamed gate"),
        )
        return exchange

    # -- Card & skill resolution ---------------------------------------- #

    async def _resolve_endpoint_and_workflow(self) -> tuple[str, str]:
        telemetry = get_observability()
        attributes = {"a2a.card.url": self._card_url}
        with telemetry.span("ravn.a2a.discover", attributes=attributes) as span:
            try:
                resp = await self._client.get(self._card_url, headers=_A2A_HEADERS)
                span.set_attribute("http.response.status_code", resp.status_code)
                if resp.status_code != 200 or not isinstance(resp.body, dict):
                    raise ToolBuildError(f"A2A agent card fetch returned HTTP {resp.status_code}")
                card = resp.body

                endpoint = _jsonrpc_endpoint(card)
                if not endpoint:
                    raise ToolBuildError("A2A agent card declares no JSONRPC interface")
                if not _same_origin(endpoint, self._card_url):
                    raise ToolBuildError(
                        "A2A JSONRPC interface must share the configured card origin"
                    )

                if self._workflow_id:
                    selection = "configured"
                    workflow_id = self._workflow_id
                else:
                    if not self._workflow_selector.configured:
                        raise ToolBuildError(
                            "a2a backend requires workflow_id or workflow_selector"
                        )
                    skills = [_skill_capability(skill) for skill in card.get("skills") or []]
                    workflow = select_workflow(self._workflow_selector, skills)
                    if workflow is None:
                        raise ToolBuildError(
                            "A2A agent card lists no skill matching the tool-builder selector"
                        )
                    self._workflow_id = workflow.workflow_id
                    workflow_id = workflow.workflow_id
                    selection = "agent_card"

                span.set_attribute("a2a.endpoint", endpoint)
                span.set_attribute("a2a.skill.id", workflow_id)
                span.set_attribute("ravn.a2a.selection", selection)
                telemetry.event(
                    "ravn.a2a.skill.selected",
                    attributes={
                        "a2a.skill.id": workflow_id,
                        "a2a.endpoint": endpoint,
                        "ravn.a2a.selection": selection,
                    },
                    content=card,
                )
                return endpoint, workflow_id
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.event(
                    "ravn.a2a.discovery.failed",
                    attributes={"error.type": type(exc).__name__},
                    content={"error": str(exc)},
                )
                raise

    # -- JSON-RPC calls --------------------------------------------------- #

    async def _send_message(
        self,
        endpoint: str,
        *,
        prompt: str,
        workflow_id: str,
        request: ToolBuildRequest,
    ) -> dict[str, Any]:
        operation_id = request.operation_id or str(uuid4())
        metadata: dict[str, Any] = {
            "skillId": workflow_id,
            "sessionName": f"tool-build-{request.name}",
        }
        if self._repo:
            metadata["repo"] = self._repo
        if self._branch:
            metadata["branch"] = self._branch
        if self._model:
            metadata["model"] = self._model
        if self._connection_id:
            # Target a specific Volundr connection (e.g. the resident's own
            # cluster) instead of the principal's default.
            metadata["connectionId"] = self._connection_id
        trace_context = get_observability().inject()
        if trace_context:
            metadata["traceContext"] = trace_context
        result = await self._rpc(
            endpoint,
            "SendMessage",
            {
                "message": {
                    "messageId": operation_id,
                    "contextId": operation_id,
                    "role": "ROLE_USER",
                    "parts": [{"text": prompt}],
                    "metadata": metadata,
                }
            },
        )
        task = result.get("task")
        if not isinstance(task, dict):
            raise ToolBuildError("A2A SendMessage returned no task")
        return task

    async def _find_task_by_context(
        self,
        endpoint: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        if not operation_id:
            return None
        try:
            result = await self._rpc(
                endpoint,
                "ListTasks",
                {"contextId": operation_id},
            )
        except ToolBuildError as exc:
            get_observability().event(
                "ravn.a2a.task.recovery_lookup_unavailable",
                attributes={"ravn.tool_build.operation.id": operation_id},
                content={"error": str(exc)},
            )
            return None
        tasks = result.get("tasks")
        if not isinstance(tasks, list):
            return None
        task = next(
            (
                item
                for item in tasks
                if isinstance(item, dict)
                and str(item.get("contextId") or item.get("context_id") or "") == operation_id
            ),
            None,
        )
        if task is not None:
            get_observability().event(
                "ravn.a2a.task.recovered",
                attributes={
                    "ravn.tool_build.operation.id": operation_id,
                    "a2a.task.id": str(task.get("id") or ""),
                },
            )
        return task

    async def _get_task(self, endpoint: str, task_id: str) -> dict[str, Any]:
        result = await self._rpc(endpoint, "GetTask", {"id": task_id})
        return result if isinstance(result, dict) else {}

    async def _rpc(
        self,
        endpoint: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        telemetry = get_observability()
        attributes = {
            "rpc.system": "jsonrpc",
            "rpc.method": method,
            "server.address": endpoint,
        }
        with telemetry.span("ravn.a2a.rpc", attributes=attributes) as span:
            try:
                resp = await self._client.post(
                    endpoint,
                    {
                        "jsonrpc": "2.0",
                        "id": str(uuid4()),
                        "method": method,
                        "params": params,
                    },
                    headers=_A2A_HEADERS,
                )
                span.set_attribute("http.response.status_code", resp.status_code)
                if resp.status_code != 200 or not isinstance(resp.body, dict):
                    raise ToolBuildError(f"A2A {method} returned HTTP {resp.status_code}")
                error = resp.body.get("error")
                if error:
                    message = error.get("message", error) if isinstance(error, dict) else error
                    if isinstance(error, dict) and error.get("code") is not None:
                        span.set_attribute("rpc.jsonrpc.error_code", str(error["code"]))
                    raise ToolBuildError(f"A2A {method} failed: {message}")
                result = resp.body.get("result")
                telemetry.event(
                    "ravn.a2a.rpc.completed",
                    attributes={**attributes, "http.response.status_code": resp.status_code},
                )
                return result if isinstance(result, dict) else {}
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.event(
                    "ravn.a2a.rpc.failed",
                    attributes={**attributes, "error.type": type(exc).__name__},
                    content={"error": str(exc)},
                )
                raise

    # -- Artifact retrieval ------------------------------------------------ #

    async def _retrieve_artifact(
        self,
        task: dict[str, Any],
        request: ToolBuildRequest,
    ) -> tuple[ToolBuildResult, str]:
        """Prefer the canonical ``learned_tool.json`` artifact; fall back to scrape."""
        telemetry = get_observability()
        attributes = {
            "ravn.tool_build.name": request.name,
            "a2a.task.id": str(task.get("id") or ""),
        }
        with telemetry.span("ravn.a2a.artifact.retrieve", attributes=attributes) as span:
            try:
                artifacts = task.get("artifacts")
                artifacts = artifacts if isinstance(artifacts, list) else []
                span.set_attribute("a2a.artifact.count", len(artifacts))

                canonical = await self._canonical_content(artifacts)
                if canonical is not None:
                    document = decode_canonical_document(canonical)
                    if document is not None:
                        result = parse_tool_build_document(document, tool_name=request.name)
                        span.set_attribute("ravn.a2a.artifact.retrieval", "canonical_file")
                        telemetry.event(
                            "ravn.a2a.artifact.selected",
                            attributes={
                                **attributes,
                                "ravn.a2a.artifact.retrieval": "canonical_file",
                            },
                        )
                        return result, "canonical_file"
                    telemetry.event(
                        "ravn.a2a.artifact.canonical_invalid",
                        attributes=attributes,
                    )
                logger.warning(
                    "A2A task carried no parseable %s artifact; scraping inline text parts",
                    CANONICAL_ARTIFACT_FILENAME,
                )
                telemetry.event(
                    "ravn.a2a.artifact.inline_scrape.considered",
                    attributes={
                        **attributes,
                        "ravn.a2a.artifact.reason": "canonical_missing_or_invalid",
                    },
                )
                for artifact in artifacts:
                    for part in _parts(artifact):
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            try:
                                result = parse_tool_build_response(
                                    text,
                                    tool_name=request.name,
                                )
                            except ToolBuildError:
                                continue
                            span.set_attribute(
                                "ravn.a2a.artifact.retrieval",
                                "inline_scrape",
                            )
                            telemetry.event(
                                "ravn.a2a.artifact.selected",
                                attributes={
                                    **attributes,
                                    "ravn.a2a.artifact.retrieval": "inline_scrape",
                                },
                            )
                            return result, "inline_scrape"
                raise ToolBuildError("A2A task produced no retrievable tool-build artifact")
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.event(
                    "ravn.a2a.artifact.retrieval.failed",
                    attributes={**attributes, "error.type": type(exc).__name__},
                    content={"error": str(exc)},
                )
                raise

    async def _canonical_content(self, artifacts: list[Any]) -> str | None:
        for artifact in artifacts:
            for part in _parts(artifact):
                if str(part.get("filename") or "") != CANONICAL_ARTIFACT_FILENAME:
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text
                url = part.get("url")
                if isinstance(url, str) and url:
                    return await self._fetch_url_part(url)
        return None

    async def _fetch_url_part(self, url: str) -> str | None:
        if not _same_origin(url, self._card_url):
            logger.warning("Refusing cross-origin A2A artifact URL: %s", url)
            return None
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None
        if isinstance(resp.body, dict):
            content = resp.body.get("content")
            return content if isinstance(content, str) else None
        return resp.body if isinstance(resp.body, str) else None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _same_origin(left: str, right: str) -> bool:
    try:
        left_parts = urlsplit(left)
        right_parts = urlsplit(right)
        default_ports = {"http": 80, "https": 443}
        left_origin = (
            left_parts.scheme.lower(),
            (left_parts.hostname or "").lower(),
            left_parts.port or default_ports.get(left_parts.scheme.lower()),
        )
        right_origin = (
            right_parts.scheme.lower(),
            (right_parts.hostname or "").lower(),
            right_parts.port or default_ports.get(right_parts.scheme.lower()),
        )
    except ValueError:
        return False
    return bool(left_origin[0] and left_origin[1] and left_origin == right_origin)


def _jsonrpc_endpoint(card: dict[str, Any]) -> str:
    interfaces = card.get("supportedInterfaces") or card.get("supported_interfaces") or []
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        binding = str(interface.get("protocolBinding") or interface.get("protocol_binding") or "")
        if binding and binding != _JSONRPC_BINDING:
            continue
        url = str(interface.get("url") or "")
        if url:
            return url
    return ""


def _skill_capability(skill: Any) -> WorkflowCapability:
    if not isinstance(skill, dict):
        skill = {}
    tags = skill.get("tags")
    if not isinstance(tags, list):
        tags = []
    return WorkflowCapability(
        workflow_id=str(skill.get("id") or ""),
        name=str(skill.get("name") or ""),
        description=str(skill.get("description") or ""),
        version="",
        tags=[str(tag) for tag in tags if str(tag).strip()],
        metadata={},
    )


def _selector_from_dict(value: dict[str, Any] | None) -> WorkflowSelector:
    if not isinstance(value, dict):
        value = {}
    names = value.get("names")
    tags = value.get("tags")
    return WorkflowSelector(
        names=[str(item) for item in names if str(item).strip()] if isinstance(names, list) else [],
        tags=[str(item) for item in tags if str(item).strip()] if isinstance(tags, list) else [],
        require_all_tags=bool(value.get("require_all_tags")),
    )


def _task_state(task: Any) -> str:
    if not isinstance(task, dict):
        return ""
    status = task.get("status")
    if not isinstance(status, dict):
        return ""
    return str(status.get("state") or "")


def _continuation_exchanges(continuation: dict[str, Any]) -> list[dict[str, Any]]:
    raw = continuation.get("exchanges")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _input_required(
    *,
    task_id: str,
    input_kind: str,
    payload: dict[str, Any],
    exchanges: list[dict[str, Any]],
    round: int,  # noqa: A002
) -> ToolBuildInputRequiredError:
    reply_metadata: dict[str, Any] = {}
    if input_kind == "question":
        request_id = str(payload.get("requestId") or "")
        if request_id:
            reply_metadata["requestId"] = request_id
        prompt = str(payload.get("question") or payload.get("summary") or "").strip()
    else:
        gate_id = str(payload.get("gateId") or "")
        if gate_id:
            reply_metadata["gateId"] = gate_id
        prompt = " — ".join(
            str(payload.get(key) or "").strip()
            for key in ("label", "condition", "instructions", "summary")
            if str(payload.get(key) or "").strip()
        )
    if not prompt:
        prompt = f"Remote workflow requires {input_kind} input."
    continuation = {
        "task_id": task_id,
        "input_kind": input_kind,
        "input_payload": payload,
        "reply_metadata": reply_metadata,
        "exchanges": exchanges,
        "round": round,
    }
    return ToolBuildInputRequiredError(
        task_id=task_id,
        input_kind=input_kind,
        prompt=prompt,
        continuation=continuation,
    )


def _first_pending_question(task: dict[str, Any]) -> dict[str, Any]:
    """The first pending peer question attached to an INPUT_REQUIRED task."""
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    questions = metadata.get("pendingQuestions")
    if not isinstance(questions, list):
        return {}
    for question in questions:
        if isinstance(question, dict):
            return question
    return {}


def _first_pending_gate(task: dict[str, Any]) -> dict[str, Any]:
    """The first pending gate context attached to an INPUT_REQUIRED task.

    Empty when the server exposes no gate context — the reviewer then decides
    from the build request alone.
    """
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    gates = metadata.get("pendingGates")
    if not isinstance(gates, list):
        return {}
    for gate in gates:
        if isinstance(gate, dict):
            return gate
    return {}


def _parts(artifact: Any) -> list[dict[str, Any]]:
    if not isinstance(artifact, dict):
        return []
    parts = artifact.get("parts")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]
