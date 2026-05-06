"""Volundr REST API methods for the CLI client."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from cli.api.client import APIClient

logger = logging.getLogger(__name__)

V1 = "/api/v1/forge"


@dataclass(frozen=True)
class SessionInfo:
    """Lightweight session representation for the CLI."""

    id: str
    name: str
    status: str
    tracker_issue_id: str | None = None
    chat_endpoint: str | None = None
    repo: str = ""
    branch: str = ""
    base_branch: str = ""


@dataclass(frozen=True)
class ActivityEvent:
    """Activity event from the SSE stream."""

    session_id: str
    state: str
    metadata: dict[str, Any]
    owner_id: str = ""
    session_status: str | None = None


@dataclass(frozen=True)
class TimelineEntry:
    """Single entry in a session timeline."""

    timestamp: str
    event: str
    details: dict[str, Any]


class VolundrAPI:
    """Volundr session endpoint methods."""

    def __init__(self, client: APIClient) -> None:
        self._client = client

    async def list_sessions(self) -> list[SessionInfo]:
        resp = await self._client.get(f"{V1}/sessions")
        resp.raise_for_status()
        return [
            SessionInfo(
                id=s["id"],
                name=s["name"],
                status=s["status"],
                tracker_issue_id=s.get("tracker_issue_id"),
            )
            for s in resp.json()
        ]

    async def create_session(
        self,
        name: str,
        *,
        model: str = "",
        repo: str = "",
        branch: str = "",
        base_branch: str = "",
        system_prompt: str = "",
        initial_prompt: str = "",
        issue_id: str = "",
        issue_url: str = "",
        workload_type: str = "",
        profile_name: str = "",
        integration_ids: list[str] | None = None,
    ) -> SessionInfo:
        payload: dict[str, Any] = {
            "name": name,
            "model": model,
            "source": {"type": "git", "repo": repo, "branch": branch, "base_branch": base_branch},
            "system_prompt": system_prompt,
            "initial_prompt": initial_prompt,
            "issue_id": issue_id,
            "issue_url": issue_url,
            "workload_type": workload_type,
            "profile_name": profile_name,
            "integration_ids": integration_ids or [],
        }
        resp = await self._client.post(f"{V1}/sessions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        source = data.get("source") or {}
        return SessionInfo(
            id=data["id"],
            name=data["name"],
            status=data["status"],
            tracker_issue_id=data.get("tracker_issue_id"),
            chat_endpoint=data.get("chat_endpoint"),
            repo=source.get("repo", ""),
            branch=source.get("branch", ""),
            base_branch=source.get("base_branch", ""),
        )

    async def get_session(self, session_id: str) -> SessionInfo | None:
        resp = await self._client.get(f"{V1}/sessions/{session_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        source = data.get("source") or {}
        return SessionInfo(
            id=data["id"],
            name=data["name"],
            status=data["status"],
            tracker_issue_id=data.get("tracker_issue_id"),
            chat_endpoint=data.get("chat_endpoint"),
            repo=source.get("repo", ""),
            branch=source.get("branch", ""),
            base_branch=source.get("base_branch", ""),
        )

    async def start_session(self, session_id: str) -> None:
        resp = await self._client.post(f"{V1}/sessions/{session_id}/start")
        resp.raise_for_status()

    async def stop_session(self, session_id: str) -> None:
        resp = await self._client.post(f"{V1}/sessions/{session_id}/stop")
        if resp.status_code == 404:
            return
        resp.raise_for_status()

    async def delete_session(self, session_id: str) -> None:
        resp = await self._client.delete(f"{V1}/sessions/{session_id}")
        if resp.status_code == 404:
            return
        resp.raise_for_status()

    async def get_chronicle(self, session_id: str) -> str:
        resp = await self._client.get(f"{V1}/sessions/{session_id}/chronicle")
        resp.raise_for_status()
        return resp.json().get("summary", "")

    async def get_timeline(self, session_id: str) -> list[TimelineEntry]:
        resp = await self._client.get(f"{V1}/chronicles/{session_id}/timeline")
        resp.raise_for_status()
        payload = resp.json()
        return [
            TimelineEntry(
                timestamp=str(e.get("t", "")),
                event=e.get("type", ""),
                details={
                    "label": e.get("label", ""),
                    **({k: v for k, v in e.items() if k not in {"t", "type", "label"}}),
                },
            )
            for e in payload.get("events", [])
        ]

    async def get_stats(self, session_id: str) -> dict[str, Any]:
        del session_id
        resp = await self._client.get(f"{V1}/stats")
        resp.raise_for_status()
        return resp.json()

    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
        """Subscribe to the Volundr SSE stream and yield activity events."""
        async for event_type, raw in self._client.stream_sse(f"{V1}/sessions/stream"):
            if event_type == "session_activity":
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                yield ActivityEvent(
                    session_id=data.get("session_id", ""),
                    state=data.get("state", ""),
                    metadata=data.get("metadata", {}),
                    owner_id=data.get("owner_id", ""),
                )
            elif event_type == "session_updated":
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                status = data.get("status", "")
                if status in ("stopped", "failed"):
                    yield ActivityEvent(
                        session_id=data.get("id", ""),
                        state="",
                        metadata={},
                        owner_id=data.get("owner_id", ""),
                        session_status=status,
                    )
