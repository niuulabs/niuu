"""REST adapter for what the memory gateway asks Forge besides a session's own context.

Three routes under ``/api/v1/forge``, none of them a session's: the gateway asks them for
people and for itself, never with a session credential.

* ``GET /projects/{project_id}/role``: a person's role on a project, so the gateway can let
  an approver or owner decide proposals to the project layer. The person's own login.
* ``GET /memory/runs/{run_id}``: who owns a workflow run, so the gateway lets only the run's
  owner promote from its layer. The person's own login.
* ``POST /memory/ended``: whether sessions and runs are still active, so the gateway deletes
  their layers a retention window after they end. The gateway's own service credential.

"A person's own login" excludes every scoped credential: a ``forge_session`` credential,
a scoped workload token or PAT, and a workload acting for someone (an ``act`` claim) get 403.
Their answers are about the caller, and a session or workload speaks for itself elsewhere.

"The gateway's own service credential" is the workload-identity token the gateway presents
for the workload named in ``memory.gateway_service_subject``: the token's ``act.sub`` must
be that subject. Where Forge's identity adapter is allow-all (local mini mode) nothing
verifies callers, so the local caller passes, still without a session or scoped credential.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.adapters.inbound.auth_context import extract_bearer_token
from niuu.domain.services.token_scope import scoped_credential_claims
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.forge_session_auth import forge_session_claims
from volundr.domain.models import Principal
from volundr.domain.projects import ProjectRole
from volundr.domain.services.memory_layers import LayerEnd, MemoryLayerService
from volundr.domain.services.projects import ProjectService

_NO_STORE = "no-store"
_PROJECT_NOT_FOUND = "Project not found"


class ProjectRoleResponse(BaseModel):
    project_id: UUID
    role: ProjectRole = Field(description="viewer, contributor, approver or owner")


class RunOwnerResponse(BaseModel):
    run_id: str
    owner_id: str
    tenant_id: str
    session_ids: list[UUID]


class EndedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions: list[str] = Field(default_factory=list, description="Session ids")
    runs: list[str] = Field(default_factory=list, description="Workflow run ids")


class LayerEndResponse(BaseModel):
    state: Literal["active", "ended", "gone"]
    ended_at: datetime | None = Field(
        description="When it ended: the latest update of the session (of the run's sessions)"
    )


class EndedResponse(BaseModel):
    sessions: dict[str, LayerEndResponse]
    runs: dict[str, LayerEndResponse]


def _end_response(end: LayerEnd) -> LayerEndResponse:
    return LayerEndResponse(state=end.state.value, ended_at=end.ended_at)


def _scoped_refusal() -> HTTPException:
    return HTTPException(
        status.HTTP_403_FORBIDDEN,
        "This route answers only to a person's own login or the memory gateway's service "
        "credential, not to a session or another scoped credential",
    )


async def _unscoped_principal(request: Request) -> Principal:
    """The caller, refused when it presents a session or any other scoped credential."""
    if forge_session_claims(request) is not None:
        raise _scoped_refusal()
    principal = await extract_principal(request)
    if (
        principal.token_use
        or principal.scopes
        or scoped_credential_claims(extract_bearer_token(request) or "") is not None
    ):
        raise _scoped_refusal()
    return principal


async def person_login(request: Request) -> Principal:
    """The caller, who must be a person using their own login."""
    principal = await _unscoped_principal(request)
    if principal.actor:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This route answers only to a person's own login, not to a workload acting for someone",
        )
    return principal


def memory_gateway_service(
    *, service_subject: str, allow_all_identity: bool
) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency admitting only the memory gateway's service credential.

    The identity adapter verifies the bearer (``extract_principal``); the credential is the
    gateway's when it is an unscoped workload-identity token whose actor (``act.sub``) is
    *service_subject*. Without an identity that verifies callers (*allow_all_identity*), the
    local caller passes.
    """

    async def check(request: Request) -> None:
        principal = await _unscoped_principal(request)
        if allow_all_identity:
            return
        if not service_subject:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This Forge does not know which caller is the memory gateway: set "
                "memory.gateway_service_subject",
            )
        if principal.actor != service_subject:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This route answers only to the memory gateway's own service credential",
            )

    return check


def create_memory_gateway_router(
    layers: MemoryLayerService,
    projects: ProjectService | None,
    *,
    service_subject: str,
    allow_all_identity: bool,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Memory"])
    gateway_only = memory_gateway_service(
        service_subject=service_subject, allow_all_identity=allow_all_identity
    )

    @router.get(
        "/projects/{project_id}/role",
        response_model=ProjectRoleResponse,
        responses={
            403: {"description": "A session or other scoped credential"},
            404: {"description": "No such project, or the caller is not on it"},
            503: {"description": "This Forge runs without projects"},
        },
    )
    async def get_project_role(
        response: Response,
        project_id: UUID = Path(description="Project id"),
        principal: Principal = Depends(person_login),
    ) -> ProjectRoleResponse:
        """The caller's role on the project (a tenant admin is an owner)."""
        if projects is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "This Forge runs without projects: set projects.enabled: true",
            )
        project = await projects.repository.get(project_id)
        role = await projects.role_of(project, principal) if project is not None else None
        if project is None or role is None:
            # One answer for "absent" and "not on it", as GET project gives.
            raise HTTPException(status.HTTP_404_NOT_FOUND, _PROJECT_NOT_FOUND)
        response.headers["Cache-Control"] = _NO_STORE
        return ProjectRoleResponse(project_id=project_id, role=role)

    @router.get(
        "/memory/runs/{run_id}",
        response_model=RunOwnerResponse,
        responses={
            403: {"description": "A session or other scoped credential"},
            404: {"description": "No such run, or not the caller's"},
        },
    )
    async def get_run_owner(
        response: Response,
        run_id: str = Path(min_length=1, description="Workflow run id"),
        principal: Principal = Depends(person_login),
    ) -> RunOwnerResponse:
        """Who owns the run: the owner of the sessions that carry its id."""
        owner = await layers.run_owner(run_id, principal)
        if owner is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run not found: {run_id}")
        response.headers["Cache-Control"] = _NO_STORE
        return RunOwnerResponse(
            run_id=owner.run_id,
            owner_id=owner.owner_id,
            tenant_id=owner.tenant_id,
            session_ids=list(owner.session_ids),
        )

    @router.post(
        "/memory/ended",
        response_model=EndedResponse,
        dependencies=[Depends(gateway_only)],
        responses={
            403: {"description": "Not the memory gateway's service credential"},
            422: {"description": "A session id is not a session id"},
        },
    )
    async def post_ended(body: EndedRequest, response: Response) -> EndedResponse:
        """Whether each session and run is active, ended (and when) or gone."""
        parsed: dict[str, UUID] = {}
        for raw in body.sessions:
            try:
                parsed[raw] = UUID(raw)
            except ValueError:
                raise HTTPException(422, f"Not a session id: {raw!r}") from None
        answer = await layers.ended(list(parsed.values()), list(dict.fromkeys(body.runs)))
        response.headers["Cache-Control"] = _NO_STORE
        return EndedResponse(
            sessions={
                raw: _end_response(answer.sessions[str(session_id)])
                for raw, session_id in parsed.items()
            },
            runs={run_id: _end_response(end) for run_id, end in answer.runs.items()},
        )

    return router
