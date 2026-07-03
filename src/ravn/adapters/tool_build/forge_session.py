"""Commission a learned-tool build inside a Volundr Forge session.

ravn never imports volundr — it drives the session over the Forge REST surface
(`/api/v1/forge/sessions`) with workload-authenticated HTTP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.adapters.tool_build._contract import (
    CANONICAL_ARTIFACT_FILENAME,
    build_prompts,
    parse_tool_build_document,
    parse_tool_build_response,
    poll_until,
)
from ravn.adapters.tool_build.http import AsyncJsonHttpClient, client_from_workload_identity
from ravn.ports.tool_build_backend import (
    ToolBuildBackend,
    ToolBuildError,
    ToolBuildRequest,
    ToolBuildResult,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"stopped", "archived", "completed", "failed", "error"})
_FAILED_STATUSES = frozenset({"failed", "error"})


class ForgeSessionToolBuildBackend(ToolBuildBackend):
    """Open a Forge session, task it to build the tool, retrieve the artifact."""

    def __init__(
        self,
        *,
        base_url: str,
        client: AsyncJsonHttpClient | None = None,
        external_token_env: str = "",
        workload_token_file: str = "",
        workload_exchange_url: str = "",
        workload_audiences: list[str] | None = None,
        model: str = "",
        source: dict[str, Any] | None = None,
        max_poll_attempts: int = 60,
        poll_interval_seconds: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = (
            client
            if client is not None
            else client_from_workload_identity(
                base_url=base_url,
                external_token_env=external_token_env,
                workload_token_file=workload_token_file,
                workload_exchange_url=workload_exchange_url,
                workload_audiences=workload_audiences,
            )
        )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._source = source
        self._max_poll_attempts = max_poll_attempts
        self._poll_interval = poll_interval_seconds
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "forge_session"

    @property
    def base_url(self) -> str:
        """Normalized base URL the backend talks to (read-only, for diagnostics)."""
        return self._base_url

    @property
    def client(self) -> AsyncJsonHttpClient:
        """The authenticated HTTP client (read-only, for diagnostics)."""
        return self._client

    async def build(self, request: ToolBuildRequest) -> ToolBuildResult:
        system_prompt, initial_prompt = build_prompts(request)
        session = await self._create_session(request, system_prompt, initial_prompt)
        session_id = str(session.get("id") or session.get("session_id") or "")
        if not session_id:
            raise ToolBuildError("Forge session create returned no session id")

        final = await poll_until(
            lambda: self._get_session(session_id),
            lambda s: _session_status(s) in _TERMINAL_STATUSES,
            max_attempts=self._max_poll_attempts,
            interval_seconds=self._poll_interval,
            sleep=self._sleep,
        )
        status = _session_status(final)
        if status in _FAILED_STATUSES:
            raise ToolBuildError(f"Forge build session {session_id} ended in status {status!r}")
        if status not in _TERMINAL_STATUSES:
            raise ToolBuildError(
                f"Forge build session {session_id} did not finish "
                f"within {self._max_poll_attempts} polls (last status {status!r})"
            )

        result, retrieval = await self._retrieve_artifact(session_id, request)
        return ToolBuildResult(
            manifest=result.manifest,
            tool_code=result.tool_code,
            test_code=result.test_code,
            requirements=result.requirements,
            build_evidence={"retrieval": retrieval},
            provenance={
                "backend": self.name,
                "forge_session_id": session_id,
                "build_request": request.build_request,
            },
        )

    async def _retrieve_artifact(
        self,
        session_id: str,
        request: ToolBuildRequest,
    ) -> tuple[ToolBuildResult, str]:
        """Prefer the canonical ``learned_tool.json`` file; fall back to the chronicle.

        Fails loudly (raises :class:`ToolBuildError`) only when NEITHER path
        yields a manifest + tool_code.
        """
        canonical = await self._get_canonical_file(session_id)
        if canonical is not None:
            return parse_tool_build_document(canonical, tool_name=request.name), "canonical_file"
        logger.warning(
            "Forge structured retrieval of %s for session %s was unavailable; "
            "falling back to chronicle scrape",
            CANONICAL_ARTIFACT_FILENAME,
            session_id,
        )
        chronicle = await self._get_chronicle(session_id)
        return parse_tool_build_response(chronicle, tool_name=request.name), "chronicle_scrape"

    async def _get_canonical_file(self, session_id: str) -> dict[str, Any] | None:
        """Fetch and parse the canonical artifact from the session workspace.

        Returns ``None`` (so the caller can fall back to the chronicle) when the
        file is absent, unreachable, or not a JSON object — never raises for a
        missing file.
        """
        url = (
            f"{self._base_url}/api/v1/forge/sessions/{session_id}/files/download"
            f"?path={CANONICAL_ARTIFACT_FILENAME}&root=workspace"
        )
        try:
            resp = await self._client.get(url)
        except Exception:  # noqa: BLE001 — transport failure = fall back to chronicle
            return None
        if resp.status_code != 200:
            return None
        document = _decode_canonical_body(resp.body)
        if not isinstance(document, dict) or not document:
            return None
        return document

    async def _create_session(
        self,
        request: ToolBuildRequest,
        system_prompt: str,
        initial_prompt: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": _session_name(request),
            "system_prompt": system_prompt,
            "initial_prompt": initial_prompt,
            "workload_type": "default",
        }
        if self._model:
            body["model"] = self._model
        if self._source is not None:
            body["source"] = self._source
        resp = await self._client.post(f"{self._base_url}/api/v1/forge/sessions", body)
        if resp.status_code not in (200, 201):
            raise ToolBuildError(f"Forge session create returned HTTP {resp.status_code}")
        if not isinstance(resp.body, dict):
            raise ToolBuildError("Forge session create returned a non-object body")
        return resp.body

    async def _get_session(self, session_id: str) -> dict[str, Any]:
        resp = await self._client.get(f"{self._base_url}/api/v1/forge/sessions/{session_id}")
        if resp.status_code != 200 or not isinstance(resp.body, dict):
            return {"status": "pending"}
        return resp.body

    async def _get_chronicle(self, session_id: str) -> str:
        resp = await self._client.get(
            f"{self._base_url}/api/v1/forge/sessions/{session_id}/chronicle"
        )
        if resp.status_code != 200:
            raise ToolBuildError(
                f"Forge chronicle fetch for {session_id} returned HTTP {resp.status_code}"
            )
        return _chronicle_text(resp.body)


def _session_name(request: ToolBuildRequest) -> str:
    base = f"tool-build-{request.name}".lower()
    return "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in base)[:63].strip("-")


def _session_status(session: Any) -> str:
    if not isinstance(session, dict):
        return "pending"
    return str(session.get("status") or "pending").lower()


def _chronicle_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        for key in ("content", "summary", "chronicle", "text"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _decode_canonical_body(body: Any) -> dict[str, Any] | None:
    """Decode the canonical file body, which the download surface returns as
    a parsed JSON object or as raw JSON text."""
    if isinstance(body, dict):
        return body
    if not isinstance(body, str) or not body.strip():
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
