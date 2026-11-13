"""ForgeSessionDirectory — the session directory over Forge's REST API (contracts C1, C4).

``GET {forge}/api/v1/forge/sessions/{id}/memory-context`` with the caller's own
``forge_session`` credential. Forge verifies the credential, its ``forge:memory``
scope, that it is bound to this session, and that the session is live; its 200
is the gateway's proof of who is calling. Anything else is a refusal.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

from mimir.memory.models import Attachment, AttachmentAccess, SessionMemoryContext
from mimir.ports.session_directory import SessionDirectory, SessionNotConfirmedError

#: Forge's mount names (volundr.domain.project_memory.MOUNT_NAME_PATTERN).
_MOUNT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

_PATH = "/api/v1/forge/sessions/{session_id}/memory-context"


class ForgeSessionDirectory(SessionDirectory):
    """Args:
    base_url: Forge's base URL, e.g. ``http://volundr:8080``.
    timeout_seconds: per-request timeout.
    """

    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        if not base_url.strip():
            raise ValueError(
                "memory.forge_base_url is empty: the gateway asks Forge who each session is; "
                "set it to Forge's base URL"
            )
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

    async def memory_context(self, session_id: str, credential: str) -> SessionMemoryContext:
        url = self._base + _PATH.format(session_id=session_id)
        try:
            response = await self._http().get(
                url,
                headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise SessionNotConfirmedError(
                f"Forge could not be asked about session {session_id} at {self._base}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise SessionNotConfirmedError(
                f"Forge answered {response.status_code} for session {session_id}'s memory context"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise SessionNotConfirmedError(
                f"Forge's memory context for session {session_id} is not JSON"
            ) from exc
        return _context(session_id, body)


def _context(session_id: str, body: Any) -> SessionMemoryContext:
    if not isinstance(body, dict):
        raise SessionNotConfirmedError("Forge's memory context is not an object")
    answered = str(body.get("session_id") or "")
    if answered.lower() != session_id.lower():
        raise SessionNotConfirmedError(
            f"Forge answered for session {answered!r}, not {session_id!r}"
        )
    for key in ("launch_id", "owner_id", "tenant_id"):
        if not isinstance(body.get(key), str) or not body[key].strip():
            raise SessionNotConfirmedError(f"Forge's memory context has no {key}")
    project_id = body.get("project_id")
    memory = body.get("project_memory")
    prefixes: tuple[str, ...] = ()
    if project_id is not None:
        if not isinstance(project_id, str) or not project_id.strip():
            raise SessionNotConfirmedError("Forge's memory context has a malformed project_id")
        if not isinstance(memory, dict) or not isinstance(memory.get("write_prefixes"), list):
            raise SessionNotConfirmedError(
                "Forge's memory context names a project but no project_memory.write_prefixes"
            )
        prefixes = tuple(str(p) for p in memory["write_prefixes"])
    run_id = body.get("workflow_run_id")
    if "attached" not in body:
        raise SessionNotConfirmedError(
            "Forge's memory context has no 'attached' (contract C4); upgrade Forge "
            "to a version that serves memory-context v2"
        )
    return SessionMemoryContext(
        session_id=answered.lower(),
        launch_id=body["launch_id"],
        owner_id=body["owner_id"],
        tenant_id=body["tenant_id"],
        project_id=project_id.lower() if project_id else None,
        workflow_run_id=str(run_id).lower() if run_id else None,
        project_write_prefixes=prefixes,
        objective=_optional_text(body, "objective"),
        project_brief=_optional_text(body, "project_brief"),
        since=_since(body.get("since")),
        attached=_attached(body["attached"]),
    )


def _optional_text(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SessionNotConfirmedError(f"Forge's memory context has a malformed {key}")
    return value.strip() or None


def _since(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise SessionNotConfirmedError(
            f"Forge's memory context has a malformed since: {value!r}"
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _attached(value: Any) -> tuple[Attachment, ...]:
    if not isinstance(value, list):
        raise SessionNotConfirmedError("Forge's memory context 'attached' is not a list")
    found = []
    for index, item in enumerate(value):
        where = f"attached[{index}]"
        if not isinstance(item, dict):
            raise SessionNotConfirmedError(f"Forge's memory context {where} is not an object")
        name = item.get("name")
        entry = item.get("registry_entry_id")
        prefixes = item.get("write_prefixes", [])
        if not isinstance(name, str) or not _MOUNT_NAME.fullmatch(name):
            raise SessionNotConfirmedError(f"Forge's memory context {where} has a malformed name")
        if not isinstance(entry, str) or not entry.strip():
            raise SessionNotConfirmedError(
                f"Forge's memory context {where} has no registry_entry_id"
            )
        try:
            access = AttachmentAccess(item.get("access"))
        except ValueError:
            raise SessionNotConfirmedError(
                f"Forge's memory context {where} has access {item.get('access')!r}; expected "
                "read or read_write"
            ) from None
        if not isinstance(prefixes, list) or not all(isinstance(p, str) for p in prefixes):
            raise SessionNotConfirmedError(
                f"Forge's memory context {where} has malformed write_prefixes"
            )
        found.append(
            Attachment(
                name=name,
                registry_entry_id=entry.strip(),
                access=access,
                write_prefixes=tuple(prefixes),
                source=str(item.get("source") or ""),
            )
        )
    return tuple(found)
