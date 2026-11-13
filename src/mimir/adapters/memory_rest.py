"""REST surface of the memory gateway (contracts C2 and C9).

**Session routes** — ``GET {base}/memory/tools``, ``POST {base}/memory/tools/{name}``
and ``GET {base}/memory/context-pack`` — authenticate through Forge, not through
Mímir's identity adapter: the caller presents its session's ``forge_session``
credential, the gateway reads the session id from the token *without verifying
it* (only to know which session to ask about) and asks Forge for that session's
memory context with the same token. Forge's 200 is the verification; any other
answer, or none, is a 401. A person's login names no session and is refused.
The app's ``enforce_identity`` middleware skips exactly these routes
(:func:`is_session_route`), and nothing else.

**People routes** — ``POST {base}/memory/ask``, ``GET {base}/memory/review``,
``GET {base}/memory/proposals``, ``POST {base}/memory/proposals/{id}/approve`` and
``/reject``, and ``POST {base}/memory/promotions`` — take the person's own login through
Mímir's identity adapter (in mini mode without auth, the configured dev principal). A
scoped credential (a session's, a workload's) is refused on them. Forge is asked
about the person's roles, projects and ownership with their own ``Authorization``
header. ``memory/ask`` answers over the layers the person reads
(:mod:`mimir.memory.readers`) and its answer call goes through Bifröst as the person,
so it is charged to them. Deciding a proposal to a project settles the notification
Forge sent its deciders, with the deciding person's login.

**Connector route** — ``POST {base}/memory/sources`` (contract C11) — takes only a
platform service credential: a verified workload-identity token whose actor
(``act.sub``) is one of ``memory.connector_service_subjects``, the way Forge admits the
memory gateway's own service credential. A person, a session credential, any scoped
credential and any other service are refused (403). With the allow-all identity of local
mini mode nothing verifies callers, so the local caller passes, still never with a
session or scoped credential. Its body names the org (``tenant_id``), so a service
speaks for any org this Mímir serves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from identity.adapters.http_auth import presented_actor
from mimir.memory.answers import Answer, AnswerRefusedError, MemoryAnswers
from mimir.memory.curation import Curation, CurationRefusedError, Decided, DecisionError, Review
from mimir.memory.gateway import LayerUnavailableError, MemoryGateway, Session
from mimir.memory.models import (
    Layer,
    LayerKind,
    MemoryStoreError,
    MemoryValidationError,
    Proposal,
    RecordNotFoundError,
    RevisionConflictError,
)
from mimir.memory.readers import PersonReader
from mimir.memory.retention import RetentionSweeper
from mimir.memory.systems import MemorySystems, TenantNotConfiguredError
from mimir.memory.tools import MemoryTools, UnknownToolError, instructions
from mimir.ports.answer_model import AnswerModelError, ModelCaller
from mimir.ports.attached_memory import AttachedMemories
from mimir.ports.people_directory import (
    ForgeUnavailableError,
    NotVisibleError,
    Person,
    PersonRefusedError,
)
from mimir.ports.proposal_notifier import ProposalNotifier, ProposalNotifyError
from mimir.ports.session_directory import SessionDirectory, SessionNotConfirmedError
from niuu.cap_telemetry import OutputTruncatedError, record_degraded
from niuu.domain.mimir_facts import FactError
from niuu.domain.services.forge_session_token import SESSION_ID_CLAIM
from niuu.domain.services.token_scope import credential_allows_route, scoped_credential_claims
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError

logger = logging.getLogger(__name__)

_PREFIX = r"(?:/api/v1)?/mimir/memory"
_LIST = re.compile(_PREFIX + r"/tools")
_CALL = re.compile(_PREFIX + r"/tools/[A-Za-z0-9_]+")
_PACK = re.compile(_PREFIX + r"/context-pack")
#: A session id is only ever placed in Forge's URL path; nothing else may ride along.
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def is_session_route(method: str, path: str) -> bool:
    """True for exactly the session routes, which authenticate through Forge."""
    if method == "GET":
        return _LIST.fullmatch(path) is not None or _PACK.fullmatch(path) is not None
    return method == "POST" and _CALL.fullmatch(path) is not None


#: Step 1's name for :func:`is_session_route`.
is_memory_tool_route = is_session_route


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="memory tools need the session's Forge credential as a Bearer token",
        )
    return token.strip()


def _claims(token: str) -> dict[str, Any] | None:
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.InvalidTokenError:
        return None
    return claims if isinstance(claims, dict) else None


def _session_id(token: str) -> str:
    claims = _claims(token)
    if claims is None:
        raise HTTPException(status_code=401, detail="the bearer token is not a JWT")
    session_id = claims.get(SESSION_ID_CLAIM)
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise HTTPException(
            status_code=401,
            detail=f"the bearer token names no session ({SESSION_ID_CLAIM}); use the session's "
            "Forge credential",
        )
    return session_id


def _proposal(proposal: Proposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "layer": proposal.target_layer,
        "status": proposal.status.value,
        "action": proposal.action.value,
        "change": proposal.change,
        "evidence": [e.ref for e in proposal.evidence],
        "proposer": proposal.proposer,
        "proposer_session": proposal.proposer_session or None,
        "session_owner": proposal.session_owner,
        "promoted_from": proposal.promoted_from or None,
        "trust": proposal.trust.label,
        "reason": proposal.reason,
        "approver": proposal.approver,
        "created_at": proposal.created_at.isoformat(),
        "attested": proposal.attested,
        "decided_by": proposal.decided_by or None,
        "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
        "decision": proposal.decision or None,
        "applied": proposal.applied or None,
        "why": proposal.why or None,
    }


def _review(review: Review) -> dict[str, Any]:
    return {
        "proposals": [
            {
                "id": item.proposal.id,
                "layer": item.proposal.target_layer,
                "layer_name": review.names.get(item.proposal.target_layer, ""),
                "action": item.proposal.action.value,
                "title": item.title,
                "now": item.now,
                "would": item.would,
                "why": item.proposal.why or None,
                "by": item.by,
                "created_at": item.proposal.created_at.isoformat(),
                "sources": [
                    {
                        "ref": source.ref,
                        "kind": source.kind,
                        "title": source.title,
                        "url": source.url,
                        "kept": source.kept,
                    }
                    for source in item.sources
                ],
            }
            for item in review.items
        ],
        "more": list(review.more),
    }


def _answer(answer: Answer, names: dict[str, str]) -> dict[str, Any]:
    return {
        "answer": answer.answer,
        "gaps": list(answer.gaps),
        "sources": [
            {
                "layer": cited.layer,
                "layer_name": names.get(cited.layer, ""),
                "ref": cited.ref,
                "kind": cited.kind,
                "title": cited.title,
                "url": cited.url,
            }
            for cited in answer.cited
        ],
    }


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="the body must be a JSON object") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    return body


#: What a connector's source write must carry (contract C11); all are text but the times.
_CONNECTOR_TEXT = (
    "tenant_id",
    "layer",
    "connector",
    "external_id",
    "title",
    "content",
    "kind",
    "origin_url",
)
_CONNECTOR_TIMES = ("fetched_at", "upstream_updated_at")


def _connector_fields(body: dict[str, Any]) -> dict[str, Any]:
    """The connector body's fields, each required; a bad one is a 400 naming it."""
    fields: dict[str, Any] = {}
    for name in _CONNECTOR_TEXT:
        value = body.get(name)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(status_code=400, detail=f"{name} is required (a non-empty string)")
        fields[name] = value if name == "content" else value.strip()
    for name in _CONNECTOR_TIMES:
        value = body.get(name)
        try:
            fields[name] = datetime.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            fields[name] = None
        if fields[name] is None:
            raise HTTPException(status_code=400, detail=f"{name} is required (an ISO 8601 time)")
    return fields


class MemoryRouter:
    """Args:
    gateway: the memory gateway.
    directory: asks Forge who a session is.
    tenant_id: when this Mímir belongs to one tenant, callers of any other are refused.
    systems: the memory systems to close with the router.
    answers: memory_ask and the context pack.
    curation: the people routes (proposals and promotions).
    identity: Mímir's identity adapter, for the people routes.
    sweeper: the retention sweep, started with :meth:`start`.
    attached: the attached-memory stores, closed with the router.
    connector_subjects: the workload subjects that may write connector sources
        (``memory.connector_service_subjects``).
    allow_all_identity: Mímir's identity adapter is the allow-all one (local mini mode), so
        nothing verifies callers and the connector route admits the local caller.
    reader: the layers a person reads, for the people ask route.
    notifier: tells a project's deciders through Forge that a proposal waits, and settles
        it when decided.
    """

    def __init__(
        self,
        gateway: MemoryGateway,
        directory: SessionDirectory,
        *,
        tenant_id: str = "",
        systems: MemorySystems | None = None,
        answers: MemoryAnswers | None = None,
        curation: Curation | None = None,
        identity: HeaderAuthenticationPort | None = None,
        sweeper: RetentionSweeper | None = None,
        attached: AttachedMemories | None = None,
        connector_subjects: Sequence[str] = (),
        allow_all_identity: bool = False,
        reader: PersonReader | None = None,
        notifier: ProposalNotifier | None = None,
    ) -> None:
        self._gateway = gateway
        self._directory = directory
        self._systems = systems
        self._answers = answers
        self._curation = curation
        self._identity = identity
        self._sweeper = sweeper
        self._attached = attached
        self._sweep_task: asyncio.Task[None] | None = None
        self._reader = reader
        self._notifier = notifier
        self._tools = MemoryTools(gateway, answers, notifier)
        self._tenant_id = tenant_id
        self._connector_subjects = frozenset(connector_subjects)
        self._allow_all_identity = allow_all_identity

    # -- sessions --------------------------------------------------------

    async def _session(self, request: Request) -> tuple[Session, str]:
        token = _bearer(request)
        session_id = _session_id(token)
        try:
            context = await self._directory.memory_context(session_id, token)
        except SessionNotConfirmedError as exc:
            logger.info("memory: Forge did not confirm session %s: %s", session_id, exc.reason)
            raise HTTPException(
                status_code=401, detail=f"Forge did not confirm this session: {exc.reason}"
            ) from exc
        if self._tenant_id and context.tenant_id != self._tenant_id:
            raise HTTPException(
                status_code=403, detail="Knowledge instance belongs to a different tenant"
            )
        try:
            return await self._gateway.open(context), token
        except TenantNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (LayerUnavailableError, MemoryStoreError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # -- people ----------------------------------------------------------

    async def _person(self, request: Request) -> Person:
        if self._identity is None:
            raise HTTPException(status_code=503, detail="this gateway serves no people routes")
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
        if token:
            claims = _claims(token) or {}
            if SESSION_ID_CLAIM in claims or not credential_allows_route(
                token, request.method, request.url.path
            ):
                raise HTTPException(
                    status_code=403,
                    detail="these routes take a person's own login, not a session or workload "
                    "credential",
                )
        try:
            principal = await self._identity.validate_headers(dict(request.headers))
        except InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail="a person's login is required") from exc
        if principal.token_use or principal.bound_session_id:
            raise HTTPException(status_code=403, detail="these routes take a person's own login")
        if presented_actor(request) in self._connector_subjects:
            raise HTTPException(
                status_code=403,
                detail="these routes take a person's own login, not a connector's service "
                "credential",
            )
        if not principal.tenant_id:
            raise HTTPException(status_code=403, detail="an authenticated org is required")
        if self._tenant_id and principal.tenant_id != self._tenant_id:
            raise HTTPException(
                status_code=403, detail="Knowledge instance belongs to a different tenant"
            )
        return Person(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            roles=tuple(principal.roles),
            authorization=header,
        )

    # -- connectors ------------------------------------------------------

    async def _connector(self, request: Request) -> None:
        """Admit only a platform service credential named in the connector subjects.

        The identity adapter verifies the bearer; the credential is a connector's when it
        is an unscoped workload-identity token whose actor (``act.sub``) is configured.
        """
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
        if token:
            claims = _claims(token) or {}
            if (
                SESSION_ID_CLAIM in claims
                or scoped_credential_claims(token) is not None
                or not credential_allows_route(token, request.method, request.url.path)
            ):
                raise self._not_a_connector()
        if self._identity is None:
            raise HTTPException(status_code=503, detail="this gateway serves no connector routes")
        try:
            principal = await self._identity.validate_headers(dict(request.headers))
        except InvalidTokenError as exc:
            raise HTTPException(
                status_code=401, detail="a connector service credential is required"
            ) from exc
        if principal.token_use or principal.scopes or principal.bound_session_id:
            raise self._not_a_connector()
        if self._allow_all_identity:
            return
        if not self._connector_subjects:
            raise HTTPException(
                status_code=403,
                detail="this gateway admits no connector: set memory.connector_service_subjects",
            )
        if presented_actor(request) not in self._connector_subjects:
            raise self._not_a_connector()

    @staticmethod
    def _not_a_connector() -> HTTPException:
        return HTTPException(
            status_code=403,
            detail="this route answers only to a connector's service credential "
            "(memory.connector_service_subjects), not to a person, a session or another "
            "scoped credential",
        )

    async def _layer_names(self, person: Person, session: Session) -> dict[str, str]:
        """A person-facing name for each layer of a reading view."""
        if self._curation is None:
            raise HTTPException(status_code=503, detail="this gateway serves no people routes")
        projects = {
            r.layer.key: r.name
            for r in await self._curation.people.readable_layers(person)
            if r.layer.kind is LayerKind.PROJECT
        }
        names: dict[str, str] = {}
        for layer in session.layers:
            if layer.kind is LayerKind.ORG:
                names[layer.id] = "the org"
            elif layer.kind is LayerKind.PROJECT:
                names[layer.id] = projects.get(layer.key, "")
            else:
                names[layer.id] = layer.key
        return names

    def _curator(self) -> Curation:
        if self._curation is None:
            raise HTTPException(status_code=503, detail="this gateway serves no people routes")
        return self._curation

    @staticmethod
    def _people_error(exc: Exception) -> HTTPException:
        match exc:
            case NotVisibleError() | RecordNotFoundError():
                return HTTPException(status_code=404, detail=str(exc))
            case PersonRefusedError():
                return HTTPException(status_code=exc.status, detail=str(exc))
            case CurationRefusedError():
                return HTTPException(status_code=403, detail=str(exc))
            case AnswerRefusedError():
                return HTTPException(status_code=422, detail=str(exc))
            case DecisionError() | RevisionConflictError() | FactError():
                return HTTPException(status_code=409, detail=str(exc))
            case MemoryValidationError():
                return HTTPException(status_code=422, detail=str(exc))
            case TenantNotConfiguredError():
                return HTTPException(status_code=503, detail=str(exc))
            case (
                ForgeUnavailableError()
                | MemoryStoreError()
                | AnswerModelError()
                | OutputTruncatedError()
            ):
                return HTTPException(status_code=502, detail=str(exc))
        raise exc

    _PEOPLE_ERRORS = (
        NotVisibleError,
        PersonRefusedError,
        CurationRefusedError,
        ForgeUnavailableError,
        TenantNotConfiguredError,
        MemoryStoreError,
        FactError,
        AnswerModelError,
        OutputTruncatedError,
    )

    async def _settled(self, person: Person, decided: Decided, outcome: str) -> dict[str, Any]:
        """The decision's answer; a project proposal's notifications are settled first.

        The decision is recorded, so a failed settlement does not fail the call: it is
        counted as degraded and said in ``notice``.
        """
        body: dict[str, Any] = {
            "summary": decided.summary,
            "proposal": _proposal(decided.proposal),
        }
        layer = Layer.parse(decided.proposal.target_layer)
        if self._notifier is None or layer.kind is not LayerKind.PROJECT:
            return body
        try:
            await self._notifier.decided(
                layer.key, decided.proposal.id, authorization=person.authorization, outcome=outcome
            )
        except ProposalNotifyError as exc:
            message = (
                f"proposal {decided.proposal.id} was decided, but the notifications Forge sent "
                f"its deciders were not settled: {exc}"
            )
            record_degraded("memory.proposal_notify", message)
            body["notice"] = message
        return body

    # -- routes ----------------------------------------------------------

    def router(self) -> APIRouter:
        router = APIRouter(tags=["Memory"])

        @router.get("/memory/tools")
        async def list_tools(request: Request) -> dict[str, Any]:
            session, _ = await self._session(request)
            return {
                "instructions": instructions(session, asks=self._tools.asks),
                "tools": self._tools.definitions(),
            }

        @router.post("/memory/tools/{name}")
        async def call_tool(name: str, request: Request) -> JSONResponse:
            session, token = await self._session(request)
            if not self._tools.exists(name):
                raise HTTPException(status_code=404, detail=f"no memory tool {name!r}")
            try:
                arguments = await request.json()
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="the body must be the tool's arguments as JSON"
                ) from None
            try:
                result = await self._tools.call(session, name, arguments, credential=token)
            except UnknownToolError:
                raise HTTPException(status_code=404, detail=f"no memory tool {name!r}") from None
            return JSONResponse(result)

        @router.get("/memory/context-pack")
        async def context_pack(request: Request) -> dict[str, Any]:
            session, _ = await self._session(request)
            if self._answers is None:
                raise HTTPException(status_code=503, detail="this gateway builds no context packs")
            try:
                pack = await self._answers.context_pack(session)
            except (MemoryStoreError, FactError) as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {"text": pack.text}

        @router.post("/memory/sources")
        async def connector_source(request: Request) -> dict[str, Any]:
            await self._connector(request)
            fields = _connector_fields(await _json(request))
            if self._tenant_id and fields["tenant_id"] != self._tenant_id:
                raise HTTPException(
                    status_code=403, detail="Knowledge instance belongs to a different tenant"
                )
            try:
                imported = await self._gateway.import_source(**fields)
            except MemoryValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except TenantNotConfiguredError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except MemoryStoreError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {
                "source_id": imported.source_id,
                "layer": imported.layer.id,
                "new": imported.new,
            }

        @router.post("/memory/ask")
        async def person_ask(request: Request) -> dict[str, Any]:
            person = await self._person(request)
            if self._reader is None or self._answers is None or not self._answers.asks:
                raise HTTPException(
                    status_code=503,
                    detail="this gateway answers no questions: set memory.answer_model",
                )
            body = await _json(request)
            question = body.get("question")
            project = body.get("project_id")
            if not isinstance(question, str) or not question.strip():
                raise HTTPException(status_code=422, detail="question is required")
            if project is not None and (not isinstance(project, str) or not project.strip()):
                raise HTTPException(status_code=422, detail="project_id must be a project id")
            try:
                session = await self._reader.open(person, project)
                names = await self._layer_names(person, session)
                answer = await self._answers.ask(
                    session,
                    question,
                    layers=None,
                    caller=ModelCaller.person(
                        authorization=person.authorization,
                        user_id=person.user_id,
                        tenant_id=person.tenant_id,
                    ),
                )
            except self._PEOPLE_ERRORS as exc:
                raise self._people_error(exc) from exc
            return _answer(answer, names)

        @router.get("/memory/review")
        async def review(
            request: Request, layer: str | None = None, limit: int | None = None
        ) -> dict[str, Any]:
            person = await self._person(request)
            count = limit if limit is not None else self._gateway.default_result_count
            if count < 1:
                raise HTTPException(status_code=422, detail="limit must be at least 1")
            try:
                found = await self._curator().review(person, layer or None, limit=count)
            except self._PEOPLE_ERRORS as exc:
                raise self._people_error(exc) from exc
            return _review(found)

        @router.get("/memory/proposals")
        async def proposals(request: Request, layer: str, limit: int | None = None) -> dict:
            person = await self._person(request)
            count = limit if limit is not None else self._gateway.default_result_count
            if count < 1:
                raise HTTPException(status_code=422, detail="limit must be at least 1")
            try:
                found = await self._curator().pending(person, layer, limit=count)
            except self._PEOPLE_ERRORS as exc:
                raise self._people_error(exc) from exc
            return {"layer": layer, "limit": count, "proposals": [_proposal(p) for p in found]}

        @router.post("/memory/proposals/{proposal_id}/approve")
        async def approve(proposal_id: str, request: Request) -> dict:
            person = await self._person(request)
            body = await _json(request)
            try:
                decided = await self._curator().approve(
                    person, str(body.get("layer") or ""), proposal_id
                )
            except self._PEOPLE_ERRORS as exc:
                raise self._people_error(exc) from exc
            return await self._settled(person, decided, "kept")

        @router.post("/memory/proposals/{proposal_id}/reject")
        async def reject(proposal_id: str, request: Request) -> dict:
            person = await self._person(request)
            body = await _json(request)
            try:
                decided = await self._curator().reject(
                    person,
                    str(body.get("layer") or ""),
                    proposal_id,
                    reason=str(body.get("reason") or ""),
                )
            except self._PEOPLE_ERRORS as exc:
                raise self._people_error(exc) from exc
            return await self._settled(person, decided, "discarded")

        @router.post("/memory/promotions")
        async def promote(request: Request) -> dict:
            person = await self._person(request)
            body = await _json(request)
            refs = body.get("refs")
            if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
                raise HTTPException(status_code=422, detail="refs must be a list of record refs")
            try:
                created = await self._curator().promote(
                    person,
                    source_id=str(body.get("from") or ""),
                    refs=refs,
                    target_id=str(body.get("to") or ""),
                )
            except self._PEOPLE_ERRORS as exc:
                raise self._people_error(exc) from exc
            return {"proposals": [_proposal(p) for p in created]}

        return router

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start the retention sweep, when there is one."""
        if self._sweeper is not None and self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweeper.run(), name="memory-retention")

    async def close(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweep_task
            self._sweep_task = None
        await self._directory.close()
        for part in (self._sweeper, self._answers, self._curation, self._attached, self._notifier):
            if part is not None:
                await part.close()
        if self._systems is not None:
            await self._systems.close()
