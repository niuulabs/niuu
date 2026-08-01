"""A2A Agent Card — dynamic projection of Ting workflows as skills.

The card is rendered per request from the workflow store, so a workflow
created in Ting is advertised on the next fetch with no restart or
registration step. Only system-scope workflows appear on the public
well-known card — it is the platform catalog, not any user's private
library. Authenticated callers get their own user-scope workflows too via
the A2A ``GetExtendedAgentCard`` method on the task endpoint.
"""

from __future__ import annotations

import hashlib
import json

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    PROTOCOL_VERSION_CURRENT,
    TransportProtocol,
)
from fastapi import APIRouter, Depends, Request, Response
from google.protobuf.json_format import MessageToDict

from niuu.version import package_version
from ting.api.workflows import resolve_workflow_repo
from ting.config import A2AConfig
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.ports.workflow_repository import WorkflowRepository

A2A_ENDPOINT_PATH = "/api/v1/ting/a2a"
BEARER_SECURITY_SCHEME = "platformBearer"


def build_agent_card(
    *,
    workflows: list[WorkflowDefinition],
    config: A2AConfig,
    endpoint_url: str,
) -> AgentCard:
    """Project workflow definitions onto an A2A Agent Card."""
    card = AgentCard(
        name=config.agent_name,
        description=config.agent_description,
        version=package_version(),
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=config.push_notifications_enabled,
            extended_agent_card=True,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        supported_interfaces=[
            AgentInterface(
                url=endpoint_url,
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        skills=[
            _workflow_skill(workflow)
            for workflow in sorted(workflows, key=lambda workflow: str(workflow.id))
        ],
    )
    bearer = card.security_schemes[BEARER_SECURITY_SCHEME].http_auth_security_scheme
    bearer.scheme = "bearer"
    bearer.description = (
        "Platform-issued JWT (PAT or workload-identity exchange). Launching a "
        "workflow additionally requires the ting:workflow:launch build scope "
        "when the token is a scoped build token."
    )
    card.security_requirements.add().schemes[BEARER_SECURITY_SCHEME]
    return card


def _workflow_skill(workflow: WorkflowDefinition) -> AgentSkill:
    graph = workflow.graph or {}
    tags = [str(tag).strip() for tag in list(graph.get("tags") or []) if str(tag).strip()]
    return AgentSkill(
        id=str(workflow.id),
        name=workflow.name,
        description=(
            f"{workflow.description}\n\n"
            "Launch context may be supplied in A2A message metadata: repo is a "
            "repository URL, branch selects its starting branch, and connectionId "
            "selects an execution connection."
        ),
        tags=tags or ["workflow"],
        examples=[
            (
                "Select a repository from the platform repository catalog, then start "
                "this skill with metadata containing repo and, when needed, branch."
            )
        ],
    )


def render_agent_card(card: AgentCard) -> tuple[str, str]:
    """Serialize a card to canonical JSON plus a content-derived ETag."""
    payload = json.dumps(MessageToDict(card), sort_keys=True, separators=(",", ":"))
    etag = f'"{hashlib.sha256(payload.encode()).hexdigest()[:32]}"'
    return payload, etag


def create_agent_card_router(config: A2AConfig) -> APIRouter:
    router = APIRouter(tags=["A2A"])

    @router.get(AGENT_CARD_WELL_KNOWN_PATH)
    async def get_agent_card(
        request: Request,
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> Response:
        workflows = await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)
        card = build_agent_card(
            workflows=workflows,
            config=config,
            endpoint_url=_endpoint_url(request, config),
        )
        payload, etag = render_agent_card(card)
        headers = {
            "ETag": etag,
            "Cache-Control": f"public, max-age={config.card_max_age_seconds}",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=payload, media_type="application/json", headers=headers)

    return router


def _endpoint_url(request: Request, config: A2AConfig) -> str:
    base = config.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}{A2A_ENDPOINT_PATH}"
