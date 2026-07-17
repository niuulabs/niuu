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

from ravn.adapters.tool_build._contract import (
    CANONICAL_ARTIFACT_FILENAME,
    build_prompts,
    decode_canonical_document,
    parse_tool_build_document,
    parse_tool_build_response,
)
from ravn.adapters.tool_build.gate_review import GateReviewer
from ravn.adapters.tool_build.http import AsyncJsonHttpClient, client_from_workload_identity
from ravn.domain.capability_catalog import (
    WorkflowCapability,
    WorkflowSelector,
    select_workflow,
)
from ravn.ports.tool_build_backend import (
    ToolBuildBackend,
    ToolBuildError,
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
        # Gate handling: on INPUT_REQUIRED the reviewer decides
        # approve/request_changes within the resident's autonomy grant.
        # Without a reviewer, a gated workflow fails loudly — a build is
        # never silently auto-approved.
        self._gate_reviewer = gate_reviewer
        self._max_gate_rounds = max_gate_rounds
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "a2a"

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
        endpoint, workflow_id = await self._resolve_endpoint_and_workflow()
        _system, initial_prompt = build_prompts(request)

        task = await self._send_message(
            endpoint,
            prompt=initial_prompt,
            workflow_id=workflow_id,
            request=request,
        )
        task_id = str(task.get("id") or "")
        if not task_id:
            raise ToolBuildError("A2A SendMessage returned no task id")

        final, gate_exchanges = await self._poll_answering_gates(endpoint, task_id, request)
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
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Poll the task, answering workflow gates until a terminal state.

        Every INPUT_REQUIRED round is decided by the injected gate reviewer
        (the commissioning Valkyrie's own judgment) and recorded verbatim —
        the question asked and the answer given become part of the build
        evidence. Rounds are bounded so a gate loop cannot spin forever.
        """
        exchanges: list[dict[str, Any]] = []
        rounds = 0
        task: dict[str, Any] = {}
        for _attempt in range(self._max_poll_attempts):
            task = await self._get_task(endpoint, task_id)
            state = _task_state(task)
            if state in _TERMINAL_STATES:
                return task, exchanges
            if state == _INPUT_REQUIRED_STATE:
                rounds += 1
                if rounds > self._max_gate_rounds:
                    raise ToolBuildError(
                        f"A2A task {task_id} exceeded {self._max_gate_rounds} "
                        "gate rounds without completing"
                    )
                exchange = await self._answer_gate(endpoint, task_id, task, request, round=rounds)
                exchanges.append(exchange)
            await self._sleep(self._poll_interval)
        return task, exchanges

    async def _answer_gate(
        self,
        endpoint: str,
        task_id: str,
        task: dict[str, Any],
        request: ToolBuildRequest,
        *,
        round: int,  # noqa: A002
    ) -> dict[str, Any]:
        if self._gate_reviewer is None:
            raise ToolBuildError(
                f"A2A task {task_id} is awaiting a gate decision but no "
                "gate_reviewer is configured — refusing to auto-approve"
            )
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
                return exchange
            raise
        exchange["delivery"] = "delivered"
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
        resp = await self._client.get(self._card_url, headers=_A2A_HEADERS)
        if resp.status_code != 200 or not isinstance(resp.body, dict):
            raise ToolBuildError(f"A2A agent card fetch returned HTTP {resp.status_code}")
        card = resp.body

        endpoint = _jsonrpc_endpoint(card)
        if not endpoint:
            raise ToolBuildError("A2A agent card declares no JSONRPC interface")

        if self._workflow_id:
            return endpoint, self._workflow_id
        if not self._workflow_selector.configured:
            raise ToolBuildError("a2a backend requires workflow_id or workflow_selector")
        skills = [_skill_capability(skill) for skill in card.get("skills") or []]
        workflow = select_workflow(self._workflow_selector, skills)
        if workflow is None:
            raise ToolBuildError("A2A agent card lists no skill matching the tool-builder selector")
        self._workflow_id = workflow.workflow_id
        return endpoint, workflow.workflow_id

    # -- JSON-RPC calls --------------------------------------------------- #

    async def _send_message(
        self,
        endpoint: str,
        *,
        prompt: str,
        workflow_id: str,
        request: ToolBuildRequest,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "workflowId": workflow_id,
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
        result = await self._rpc(
            endpoint,
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
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

    async def _get_task(self, endpoint: str, task_id: str) -> dict[str, Any]:
        result = await self._rpc(endpoint, "GetTask", {"id": task_id})
        return result if isinstance(result, dict) else {}

    async def _rpc(
        self,
        endpoint: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        resp = await self._client.post(
            endpoint,
            {"jsonrpc": "2.0", "id": str(uuid4()), "method": method, "params": params},
            headers=_A2A_HEADERS,
        )
        if resp.status_code != 200 or not isinstance(resp.body, dict):
            raise ToolBuildError(f"A2A {method} returned HTTP {resp.status_code}")
        error = resp.body.get("error")
        if error:
            raise ToolBuildError(
                f"A2A {method} failed: {error.get('message', error)}"
                if isinstance(error, dict)
                else f"A2A {method} failed: {error}"
            )
        result = resp.body.get("result")
        return result if isinstance(result, dict) else {}

    # -- Artifact retrieval ------------------------------------------------ #

    async def _retrieve_artifact(
        self,
        task: dict[str, Any],
        request: ToolBuildRequest,
    ) -> tuple[ToolBuildResult, str]:
        """Prefer the canonical ``learned_tool.json`` artifact; fall back to scrape."""
        artifacts = task.get("artifacts")
        artifacts = artifacts if isinstance(artifacts, list) else []

        canonical = await self._canonical_content(artifacts)
        if canonical is not None:
            document = decode_canonical_document(canonical)
            if document is not None:
                return (
                    parse_tool_build_document(document, tool_name=request.name),
                    "canonical_file",
                )
        logger.warning(
            "A2A task carried no parseable %s artifact; scraping inline text parts",
            CANONICAL_ARTIFACT_FILENAME,
        )
        for artifact in artifacts:
            for part in _parts(artifact):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    try:
                        return parse_tool_build_response(text, tool_name=request.name), (
                            "inline_scrape"
                        )
                    except ToolBuildError:
                        continue
        raise ToolBuildError("A2A task produced no retrievable tool-build artifact")

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
