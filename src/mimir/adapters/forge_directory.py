"""ForgeSessionDirectory — the session directory over Forge's REST API (contract C1).

``GET {forge}/api/v1/forge/sessions/{id}/memory-context`` with the caller's own
``forge_session`` credential. Forge verifies the credential, its ``forge:memory``
scope, that it is bound to this session, and that the session is live; its 200
is the gateway's proof of who is calling. Anything else is a refusal.
"""

from __future__ import annotations

from typing import Any

import httpx

from mimir.memory.models import SessionMemoryContext
from mimir.ports.session_directory import SessionDirectory, SessionNotConfirmedError

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
    return SessionMemoryContext(
        session_id=answered.lower(),
        launch_id=body["launch_id"],
        owner_id=body["owner_id"],
        tenant_id=body["tenant_id"],
        project_id=project_id.lower() if project_id else None,
        workflow_run_id=str(run_id) if run_id else None,
        project_write_prefixes=prefixes,
    )
