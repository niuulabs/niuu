"""ForgePeopleDirectory — a person's role and ownership, from Forge (contracts C5, C6).

- ``GET /api/v1/forge/projects/{id}/role`` → ``{"project_id", "role"}`` (C5)
- ``GET /api/v1/forge/sessions/{id}`` → the session, with ``owner_id`` and ``tenant_id``
- ``GET /api/v1/forge/memory/runs/{id}`` → ``{"run_id", "owner_id", "tenant_id",
  "session_ids"}`` (C6)
- ``GET /api/v1/forge/projects`` → the projects the person is on, through
  :func:`mimir.memory.person_layers.readable_layers` (one implementation for every caller)
- ``GET /api/v1/forge/projects/{id}/memory`` → the project's memory mounts (its attached
  memories), which every member may list

Each is asked with the person's own ``Authorization`` header. 404 is
:class:`NotVisibleError`, 401/403 :class:`PersonRefusedError`, anything else
:class:`ForgeUnavailableError`.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from mimir.memory.models import Attachment, AttachmentAccess
from mimir.memory.person_layers import ReadableLayer, readable_layers
from mimir.ports.people_directory import (
    ForgeUnavailableError,
    NotVisibleError,
    Owned,
    PeopleDirectory,
    Person,
    PersonRefusedError,
)

_ROLES = frozenset({"viewer", "contributor", "approver", "owner"})
#: Ids only ever go into Forge's URL path; nothing else may ride along.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ForgePeopleDirectory(PeopleDirectory):
    """Args:
    base_url: Forge's base URL, e.g. ``http://volundr:8080``.
    timeout_seconds: per-request timeout.
    """

    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        if not base_url.strip():
            raise ValueError("memory.forge_base_url is empty: set it to Forge's base URL")
        if timeout_seconds <= 0:
            raise ValueError("memory.forge_timeout_seconds must be positive")
        self._base = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _get(self, path: str, what: str, person: Person) -> dict[str, Any]:
        body = await self._get_json(path, what, person)
        if not isinstance(body, dict):
            raise ForgeUnavailableError(f"Forge's answer about {what} is not an object")
        return body

    async def _get_list(self, path: str, what: str, person: Person) -> list[dict[str, Any]]:
        body = await self._get_json(path, what, person)
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise ForgeUnavailableError(f"Forge's answer about {what} is not a list of objects")
        return body

    async def _get_json(self, path: str, what: str, person: Person) -> Any:
        headers = {"Accept": "application/json"}
        if person.authorization:
            headers["Authorization"] = person.authorization
        try:
            response = await self._http().get(self._base + path, headers=headers)
        except httpx.HTTPError as exc:
            raise ForgeUnavailableError(
                f"Forge could not be asked about {what} at {self._base}: {exc}"
            ) from exc
        if response.status_code == 404:
            raise NotVisibleError(f"{what} was not found")
        if response.status_code in (401, 403):
            raise PersonRefusedError(
                response.status_code,
                f"Forge refused this login for {what} ({response.status_code})",
            )
        if response.status_code != 200:
            raise ForgeUnavailableError(f"Forge answered {response.status_code} for {what}")
        try:
            return response.json()
        except ValueError as exc:
            raise ForgeUnavailableError(f"Forge's answer about {what} is not JSON") from exc

    @staticmethod
    def _id(value: str, what: str) -> str:
        if not _ID.fullmatch(value):
            raise NotVisibleError(f"{what} {value!r} is not a valid id")
        return value

    @staticmethod
    def _text(body: dict[str, Any], key: str, what: str) -> str:
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ForgeUnavailableError(f"Forge's answer about {what} has no {key}")
        return value

    async def project_role(self, project_id: str, person: Person) -> str:
        what = f"project {project_id}"
        body = await self._get(
            f"/api/v1/forge/projects/{self._id(project_id, 'project')}/role", what, person
        )
        role = body.get("role")
        if role not in _ROLES:
            raise ForgeUnavailableError(f"Forge answered an unknown role {role!r} for {what}")
        return str(role)

    async def readable_layers(self, person: Person) -> list[ReadableLayer]:
        return await readable_layers(person, forge_base_url=self._base, client=self._http())

    async def project_attachments(self, project_id: str, person: Person) -> tuple[Attachment, ...]:
        what = f"the memories project {project_id} attaches"
        mounts = await self._get_list(
            f"/api/v1/forge/projects/{self._id(project_id, 'project')}/memory", what, person
        )
        found = []
        for body in mounts:
            prefixes = body.get("write_prefixes", [])
            if not isinstance(prefixes, list) or not all(isinstance(p, str) for p in prefixes):
                raise ForgeUnavailableError(f"Forge's answer about {what} has malformed prefixes")
            try:
                access = AttachmentAccess(body.get("access"))
            except ValueError:
                raise ForgeUnavailableError(
                    f"Forge's answer about {what} has access {body.get('access')!r}"
                ) from None
            found.append(
                Attachment(
                    name=self._text(body, "name", what),
                    registry_entry_id=self._text(body, "registry_entry_id", what),
                    access=access,
                    write_prefixes=tuple(prefixes),
                    source="project",
                )
            )
        return tuple(found)

    async def session_owner(self, session_id: str, person: Person) -> Owned:
        what = f"session {session_id}"
        body = await self._get(
            f"/api/v1/forge/sessions/{self._id(session_id, 'session')}", what, person
        )
        return Owned(
            owner_id=self._text(body, "owner_id", what),
            tenant_id=self._text(body, "tenant_id", what),
        )

    async def run_owner(self, run_id: str, person: Person) -> Owned:
        what = f"run {run_id}"
        body = await self._get(f"/api/v1/forge/memory/runs/{self._id(run_id, 'run')}", what, person)
        sessions = body.get("session_ids")
        if not isinstance(sessions, list) or not all(isinstance(s, str) for s in sessions):
            raise ForgeUnavailableError(f"Forge's answer about {what} has no session_ids list")
        return Owned(
            owner_id=self._text(body, "owner_id", what),
            tenant_id=self._text(body, "tenant_id", what),
            session_ids=tuple(sessions),
        )
