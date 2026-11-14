"""``GET {base}/memory/graph`` — the knowledge graph of the layers a person can read.

A **people route**, authenticated exactly like ``/memory/proposals``: the person's own
login through Mímir's identity adapter (in mini mode without auth, the configured dev
principal). A session credential, any scoped workload credential and a connector's
service credential are refused (403). Forge is asked for the person's member projects
with their own ``Authorization``, so a project they are not on is not in the graph and
``?layers=`` can only narrow what they can already read.

The answer is built by :class:`mimir.memory.graph.KnowledgeGraph`: nodes, edges derived
from page text, and the links that resolve to no readable page.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from identity.adapters.http_auth import presented_actor
from mimir.adapters.memory_rest import memory_unavailable
from mimir.memory.graph import GraphError, KnowledgeGraph
from mimir.memory.models import Layer, MemoryStoreError, MemoryValidationError
from mimir.memory.person_layers import ReadableLayer, readable_layers
from mimir.memory.systems import MemorySystemUnavailableError
from mimir.ports.people_directory import ForgeUnavailableError, Person, PersonRefusedError
from niuu.domain.services.forge_session_token import SESSION_ID_CLAIM
from niuu.domain.services.token_scope import credential_allows_route
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError

logger = logging.getLogger(__name__)


def _claims(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.InvalidTokenError:
        return {}
    return claims if isinstance(claims, dict) else {}


class MemoryGraphRouter:
    """Args:
    graph: builds and caches the graphs.
    identity: Mímir's identity adapter.
    forge_base_url: Forge's base URL, asked for the person's member projects.
    forge_timeout_seconds: timeout of each call to Forge.
    tenant_id: when this Mímir belongs to one tenant, callers of any other are refused.
    connector_subjects: ``memory.connector_service_subjects``; a connector's service
        credential is not a person's.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        *,
        identity: HeaderAuthenticationPort,
        forge_base_url: str,
        forge_timeout_seconds: float,
        tenant_id: str = "",
        connector_subjects: Sequence[str] = (),
    ) -> None:
        if not forge_base_url.strip():
            raise ValueError("memory.forge_base_url is empty: set it to Forge's base URL")
        if forge_timeout_seconds <= 0:
            raise ValueError("memory.forge_timeout_seconds must be positive")
        self._graph = graph
        self._identity = identity
        self._forge = forge_base_url
        self._timeout = float(forge_timeout_seconds)
        self._tenant_id = tenant_id
        self._connector_subjects = frozenset(connector_subjects)
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    # -- who is asking ---------------------------------------------------

    async def _person(self, request: Request) -> Person:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
        if token and (
            SESSION_ID_CLAIM in _claims(token)
            or not credential_allows_route(token, request.method, request.url.path)
        ):
            raise HTTPException(
                status_code=403,
                detail="this route takes a person's own login, not a session or workload "
                "credential",
            )
        try:
            principal = await self._identity.validate_headers(dict(request.headers))
        except InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail="a person's login is required") from exc
        if principal.token_use or principal.bound_session_id:
            raise HTTPException(status_code=403, detail="this route takes a person's own login")
        if presented_actor(request) in self._connector_subjects:
            raise HTTPException(
                status_code=403,
                detail="this route takes a person's own login, not a connector's service "
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

    # -- which layers ----------------------------------------------------

    @staticmethod
    def _narrow(readable: list[ReadableLayer], wanted: str | None) -> list[ReadableLayer]:
        """*readable* narrowed to the layers named in ``?layers=``; never widened."""
        if wanted is None:
            return readable
        names = [name.strip() for name in wanted.split(",")]
        if not all(names):
            raise HTTPException(
                status_code=422,
                detail="layers is a comma-separated list of layer ids, e.g. org,project:<id>",
            )
        by_id = {item.layer.id: item for item in readable}
        chosen: dict[str, ReadableLayer] = {}
        for name in names:
            try:
                layer_id = Layer.parse(name).id
            except MemoryValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            item = by_id.get(layer_id)
            if item is None:
                raise HTTPException(
                    status_code=404, detail=f"layer {layer_id} was not found among your layers"
                )
            chosen[layer_id] = item
        return [item for item in readable if item.layer.id in chosen]

    # -- route -----------------------------------------------------------

    def router(self) -> APIRouter:
        router = APIRouter(tags=["Memory"])

        @router.get("/memory/graph")
        async def knowledge_graph(request: Request, layers: str | None = None) -> JSONResponse:
            person = await self._person(request)
            try:
                readable = await readable_layers(
                    person, forge_base_url=self._forge, client=self._http()
                )
                chosen = self._narrow(readable, layers)
                graph = await self._graph.graph(person.tenant_id, chosen)
            except PersonRefusedError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
            except MemorySystemUnavailableError as exc:
                raise memory_unavailable(exc) from exc
            except (ForgeUnavailableError, GraphError, MemoryStoreError) as exc:
                logger.error("memory graph for %s failed: %s", person.ref, exc)
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return JSONResponse(graph)

        return router

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
