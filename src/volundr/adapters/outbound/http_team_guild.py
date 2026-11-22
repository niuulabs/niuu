"""The Guild's record of project teams, over its Forge API, with the caller's own login."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from niuu.adapters.inbound.remote_urls import build_remote_url
from niuu.domain.project_team import ProjectTeamRecord
from volundr.domain.project_ports import (
    TeamGuild,
    TeamGuildError,
    TeamGuildReply,
    TeamNotVisibleError,
    TeamPublicationReply,
)

FORGE_API_PREFIX = "/api/v1/forge"
#: FastAPI's answer for a path it has no route for; a domain 404 says what is missing.
ROUTE_MISSING_DETAIL = "Not Found"


class HttpTeamGuild(TeamGuild):
    def __init__(self, guild_url: str, *, timeout_seconds: float) -> None:
        self._guild_url = guild_url
        self._timeout = timeout_seconds

    async def record(
        self, project_id: UUID, bearer: str, *, fresh: bool = False
    ) -> ProjectTeamRecord:
        response = await self._send(
            "GET",
            f"/projects/{project_id}/team/record",
            bearer,
            params={"fresh": "true"} if fresh else None,
        )
        payload = self._payload(response)
        try:
            return ProjectTeamRecord.model_validate(payload)
        except ValidationError as exc:
            raise TeamGuildError(
                f"The Guild at {self._guild_url} returned an unreadable project team; update "
                "the Guild and this Forge to the same version"
            ) from exc

    async def publish(
        self, project_id: UUID, instance_id: str, bearer: str
    ) -> TeamPublicationReply:
        response = await self._send(
            "POST",
            f"/projects/{project_id}/team/publish",
            bearer,
            json={"instance_id": instance_id},
        )
        payload = self._payload(response)
        version = payload.get("version") if isinstance(payload, dict) else None
        if not isinstance(version, int):
            raise TeamGuildError(
                f"The Guild at {self._guild_url} did not say which team version it took"
            )
        return TeamPublicationReply(version=version)

    async def forward(
        self,
        project_id: UUID,
        instance_id: str,
        *,
        method: str,
        operation: str,
        query: str,
        body: bytes,
        bearer: str,
    ) -> TeamGuildReply:
        params = httpx.QueryParams(query).set("instance_id", instance_id)
        response = await self._send(
            method,
            f"/projects/{project_id}/{operation}",
            bearer,
            params=params,
            content=body,
            headers={"content-type": "application/json"} if body else None,
        )
        return _reply(response)

    async def start_install(self, instance_id: str, *, body: bytes, bearer: str) -> TeamGuildReply:
        response = await self._send(
            "POST",
            "/integrations/oauth/authorize-team",
            bearer,
            params={"instance_id": instance_id},
            content=body,
            headers={"content-type": "application/json"},
        )
        return _reply(response)

    async def _send(
        self,
        method: str,
        path: str,
        bearer: str,
        *,
        headers: dict[str, str] | None = None,
        **request: Any,
    ) -> httpx.Response:
        if not bearer:
            raise TeamGuildError("A project's team is exchanged with the caller's own login")
        url = build_remote_url(self._guild_url, FORGE_API_PREFIX, path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method,
                    url,
                    headers={**(headers or {}), "Authorization": f"Bearer {bearer}"},
                    **request,
                )
        except httpx.HTTPError as exc:
            raise TeamGuildError(
                f"The Guild at {self._guild_url} could not be reached "
                f"({type(exc).__name__}); try again when it is"
            ) from exc

    def _payload(self, response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code < 400:
            return payload
        detail = payload.get("detail") if isinstance(payload, dict) else None
        message = f"The Guild at {self._guild_url} answered HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        if response.status_code == 404 and detail != ROUTE_MISSING_DETAIL:
            raise TeamNotVisibleError(message)
        raise TeamGuildError(message, route_missing=response.status_code in (404, 405))


def _reply(response: httpx.Response) -> TeamGuildReply:
    return TeamGuildReply(
        status=response.status_code,
        content=response.content,
        media_type=response.headers.get("content-type"),
    )
