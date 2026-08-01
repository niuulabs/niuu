"""Volundr HTTP adapter — calls the Volundr REST API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import httpx

from niuu.domain.models import Principal
from niuu.ports.http_auth import HttpAuthPort
from ting.domain.models import PRStatus
from ting.ports.volundr import ActivityEvent, SpawnRequest, VolundrPort, VolundrSession

logger = logging.getLogger(__name__)

FORGE_SESSIONS_PATH = "/api/v1/forge/sessions"
INTEGRATIONS_PATH = "/api/v1/integrations"
WORKFLOW_GATE_INTENT_HEADER = "x-niuu-workflow-gate-intent"
WORKFLOW_GATE_INTENT_RESOLVE = "resolve"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _looks_like_local_path(value: str) -> bool:
    trimmed = value.strip()
    return trimmed.startswith(("/", "~", "./", "../"))


def _local_mount_path(value: str) -> str | None:
    """Return an absolute local workspace path when a repo field is a path."""
    if not _looks_like_local_path(value):
        return None
    return str(Path(value).expanduser().resolve())


def _public_chat_endpoint(chat_endpoint: str | None, base_url: str) -> str | None:
    if not chat_endpoint:
        return chat_endpoint
    parsed = urlparse(chat_endpoint)
    if parsed.hostname not in _LOOPBACK_HOSTS:
        return chat_endpoint
    base = urlparse(base_url)
    if not base.scheme or not base.netloc:
        return chat_endpoint
    scheme = "wss" if base.scheme == "https" else "ws"
    return urlunparse(
        (scheme, base.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


class VolundrHTTPAdapter(VolundrPort):
    """Calls Volundr's REST API to manage sessions."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        name: str = "",
        target_id: str | None = None,
        tags: list[str] | None = None,
        auth: HttpAuthPort | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._name = name
        self._target_id = target_id or name
        self._tags = list(tags or [])
        self._auth = auth

    @property
    def name(self) -> str:
        return self._name

    @property
    def target_id(self) -> str:
        return self._target_id

    @property
    def tags(self) -> list[str]:
        return self._tags

    def _headers(
        self,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = self._auth.headers() if self._auth else {}
        token = auth_token or self._api_key
        if token:
            headers = {
                key: value for key, value in headers.items() if key.lower() != "authorization"
            }
            headers["Authorization"] = f"Bearer {token}"
        if principal is not None:
            headers["x-auth-user-id"] = principal.user_id
            headers["x-auth-email"] = principal.email
            headers["x-auth-tenant"] = principal.tenant_id
            headers["x-auth-roles"] = ",".join(principal.roles)
        return headers

    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession:
        repo = request.repo
        local_path = _local_mount_path(repo)
        # Resolve bare org/repo shorthands to full URLs so Volundr's
        # GitContributor can produce an authenticated clone URL.
        if local_path is None and repo and "://" not in repo and "@" not in repo:
            resolved = await self._resolve_repo_url(
                repo,
                auth_token=auth_token,
                principal=principal,
            )
            if resolved:
                logger.info("Resolved repo shorthand %s → %s", repo, resolved)
                repo = resolved
        source_payload = (
            {
                "type": "local_mount",
                "local_path": local_path,
            }
            if local_path is not None
            else {
                "type": "git",
                "repo": repo,
                "branch": request.branch,
                "base_branch": request.base_branch,
            }
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}{FORGE_SESSIONS_PATH}",
                headers=self._headers(auth_token, principal),
                json={
                    "name": request.name,
                    "model": request.model,
                    "source": source_payload,
                    "system_prompt": request.system_prompt,
                    "initial_prompt": request.initial_prompt,
                    "issue_id": request.tracker_issue_id,
                    "issue_url": request.tracker_issue_url,
                    "definition": request.definition,
                    "workload_type": request.workload_type,
                    "workload_config": request.workload_config,
                    "launch_spec": request.profile,
                    "integration_ids": request.integration_ids,
                    "credential_names": request.credential_names,
                },
            )
            if resp.status_code >= 400:
                logger.error("spawn_session %d: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()
            source = data.get("source") or {}
            return VolundrSession(
                id=data["id"],
                name=data["name"],
                status=data["status"],
                tracker_issue_id=data.get("tracker_issue_id"),
                chat_endpoint=_public_chat_endpoint(data.get("chat_endpoint"), self._base_url),
                cluster_name=self._name,
                repo=source.get("repo") or source.get("local_path", ""),
                branch=source.get("branch", ""),
                base_branch=source.get("base_branch", ""),
                workload_type=data.get("workload_type", "default"),
                activity_state=data.get("activity_state"),
                activity_metadata=data.get("activity_metadata") or {},
            )

    async def get_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession | None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}",
                headers=self._headers(auth_token, principal),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            source = data.get("source") or {}
            return VolundrSession(
                id=data["id"],
                name=data["name"],
                status=data["status"],
                tracker_issue_id=data.get("tracker_issue_id"),
                chat_endpoint=_public_chat_endpoint(data.get("chat_endpoint"), self._base_url),
                cluster_name=self._name,
                repo=source.get("repo") or source.get("local_path", ""),
                branch=source.get("branch", ""),
                base_branch=source.get("base_branch", ""),
                workload_type=data.get("workload_type", "default"),
                activity_state=data.get("activity_state"),
                activity_metadata=data.get("activity_metadata") or {},
            )

    async def list_sessions(
        self,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[VolundrSession]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}",
                headers=self._headers(auth_token, principal),
            )
            resp.raise_for_status()
            if not resp.content:
                raise ValueError(
                    f"Volundr returned empty response (status={resp.status_code}, url={resp.url})"
                )
            return [
                VolundrSession(
                    id=s["id"],
                    name=s["name"],
                    status=s["status"],
                    tracker_issue_id=s.get("tracker_issue_id"),
                    chat_endpoint=_public_chat_endpoint(s.get("chat_endpoint"), self._base_url),
                    cluster_name=self._name,
                    workload_type=s.get("workload_type", "default"),
                    activity_state=s.get("activity_state"),
                    activity_metadata=s.get("activity_metadata") or {},
                )
                for s in resp.json()
            ]

    async def get_pr_status(self, session_id: str) -> PRStatus:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/pr",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return PRStatus(
                pr_id=data["pr_id"],
                url=data.get("url", ""),
                state=data["state"],
                mergeable=data["mergeable"],
                ci_passed=data.get("ci_passed"),
            )

    async def get_chronicle_summary(self, session_id: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/chronicle",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json().get("summary", "")

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/messages",
                headers=self._headers(auth_token, principal),
                json={"content": message},
            )
            resp.raise_for_status()

    async def send_directed_room_message(
        self,
        session_id: str,
        target_peer_id: str,
        message: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        session = await self.get_session(
            session_id,
            auth_token=auth_token,
            principal=principal,
        )
        if session is None or not session.chat_endpoint:
            raise LookupError(f"Session {session_id} has no active room endpoint")

        base_url = _session_chat_base_url(session.chat_endpoint)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{base_url}/api/room/direct",
                headers=self._headers(auth_token, principal),
                json={
                    "target_peer_id": target_peer_id,
                    "content": message,
                    "source": "ting",
                },
            )
            resp.raise_for_status()

    async def get_workflow_gates(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/workflow/gates",
                headers=self._headers(auth_token, principal),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                gates = data.get("gates", [])
                return gates if isinstance(gates, list) else []
            return data if isinstance(data, list) else []

    async def resolve_workflow_gate(
        self,
        session_id: str,
        gate_id: str,
        decision: str,
        *,
        notes: str = "",
        source: str = "ting",
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> dict:
        headers = self._headers(auth_token, principal)
        headers[WORKFLOW_GATE_INTENT_HEADER] = WORKFLOW_GATE_INTENT_RESOLVE
        encoded_gate_id = quote(gate_id, safe="")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/workflow/gates/"
                f"{encoded_gate_id}/resolve",
                headers=headers,
                json={"decision": decision, "notes": notes, "source": source},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_help_requests(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/help/requests",
                headers=self._headers(auth_token, principal),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                requests = data.get("requests", [])
                return requests if isinstance(requests, list) else []
            return data if isinstance(data, list) else []

    async def answer_help_request(
        self,
        session_id: str,
        request_id: str,
        answer: str,
        *,
        source: str = "ting",
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> dict:
        encoded_request_id = quote(request_id, safe="")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/help/requests/"
                f"{encoded_request_id}/answer",
                headers=self._headers(auth_token, principal),
                json={"answer": answer, "source": source},
            )
            resp.raise_for_status()
            return resp.json()

    async def stop_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.delete(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}",
                headers=self._headers(auth_token, principal),
            )
            if resp.status_code == 404:
                return
            resp.raise_for_status()

    async def list_integration_ids(
        self,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[str]:
        """Fetch the user's enabled integration IDs from this Volundr instance."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self._base_url}{INTEGRATIONS_PATH}",
                headers=self._headers(auth_token, principal),
            )
            resp.raise_for_status()
            return [c["id"] for c in resp.json() if c.get("enabled", True)]

    async def _resolve_repo_url(
        self,
        shorthand: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> str | None:
        """Resolve a bare org/repo shorthand to a full URL via the repos listing."""
        try:
            repos = await self.list_repos(auth_token=auth_token, principal=principal)
            parts = shorthand.strip("/").split("/")
            if len(parts) != 2:
                return None
            org, name = parts
            for repo in repos:
                if repo.get("org") == org and repo.get("name") == name:
                    return repo.get("url")
        except Exception:
            logger.warning("Failed to resolve repo shorthand %s", shorthand, exc_info=True)
        return None

    async def list_repos(
        self,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[dict]:
        """Fetch configured repos from Volundr's shared niuu endpoint."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/niuu/repos",
                headers=self._headers(auth_token, principal),
            )
            resp.raise_for_status()
            repos = []
            for provider_repos in resp.json().values():
                repos.extend(provider_repos)
            return repos

    async def get_conversation(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> dict:
        """Fetch the full conversation history for a session."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/conversation",
                headers=self._headers(auth_token, principal),
            )
            resp.raise_for_status()
            return resp.json()

    async def get_last_assistant_message(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> str:
        """Fetch the most recent assistant message containing a JSON assessment.

        Scans the last 3 assistant messages for a JSON block with a
        ``confidence`` key (the reviewer's final output).  Falls back to
        the very last assistant message if no JSON assessment is found.
        """
        data = await self.get_conversation(
            session_id,
            auth_token=auth_token,
            principal=principal,
        )
        turns = data.get("turns", [])
        assistant_turns = [t for t in turns if t.get("role") == "assistant"]
        if not assistant_turns:
            raise ValueError(f"No assistant message found in conversation for session {session_id}")

        # Scan last 3 assistant messages for the JSON assessment
        for turn in reversed(assistant_turns[-3:]):
            content = turn.get("content", "")
            if '"confidence"' in content:
                return content

        # Fall back to the very last assistant message
        return assistant_turns[-1].get("content", "")

    # Volundr sends heartbeats every 30s; if we receive nothing for 90s the
    # connection is dead and we should break so the caller can reconnect.
    _SSE_READ_TIMEOUT: float = 90.0

    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
        """Subscribe to the Volundr SSE stream and yield activity + session lifecycle events."""
        url = f"{self._base_url}{FORGE_SESSIONS_PATH}/stream"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=self._headers()) as resp:
                resp.raise_for_status()
                event_type = ""
                line_iter = resp.aiter_lines().__aiter__()
                while True:
                    try:
                        line = await asyncio.wait_for(
                            line_iter.__anext__(), timeout=self._SSE_READ_TIMEOUT
                        )
                    except StopAsyncIteration:
                        return
                    except TimeoutError:
                        logger.warning(
                            "SSE read timeout (%.0fs with no data) — "
                            "connection to %s presumed dead, reconnecting",
                            self._SSE_READ_TIMEOUT,
                            self._base_url,
                        )
                        return

                    if line.startswith("event:"):
                        event_type = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        raw = line[len("data:") :].strip()
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
                        event_type = ""
                    elif line == "":
                        event_type = ""


def _session_chat_base_url(chat_endpoint: str) -> str:
    normalized = chat_endpoint.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid chat endpoint: {chat_endpoint}")
    path = parsed.path
    if path.endswith("/session"):
        path = path[: -len("/session")]
    return parsed._replace(path=path, params="", query="", fragment="").geturl().rstrip("/")
