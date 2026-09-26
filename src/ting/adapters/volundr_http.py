"""Volundr HTTP adapter — calls the Volundr REST API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx

from niuu.adapters.outbound.guild_transport import build_guild_httpx_client
from niuu.domain.delivery import (
    AcceptancePolicy,
    CandidateEvidence,
    CheckReceipt,
    EvidenceValidationReport,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    MergeReceipt,
    MergeRequest,
    ReviewCandidate,
    WorkspaceAllocation,
)
from niuu.domain.models import Principal
from niuu.ports.http_auth import HttpAuthPort
from ravn.domain.persona_document import PortablePersonaDefinition, parse_portable_persona
from ting.domain.models import PRStatus
from ting.ports.volundr import (
    ActivityEvent,
    ActivityStreamConnected,
    PublicSessionLogEntry,
    PublicSessionLogPage,
    SpawnRequest,
    VolundrPort,
    VolundrSession,
)

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
        config: dict[str, Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._name = name
        self._target_id = target_id or name
        self._tags = list(tags or [])
        self._auth = auth
        # The registered instance's raw config (allow_plaintext,
        # tls_fingerprint, ...), consumed by guild_transport.py via _client()
        # below. VolundrHTTPAdapter itself satisfies GuildTransportTarget
        # (name + config), so it is passed as its own `instance` — no
        # separate fabricated object needed. Empty for a target that has none
        # (e.g. LocalVolundrAdapterFactory's same-process, always-loopback
        # target), which is fine: the loopback exemption in
        # niuu.domain.transport_security applies regardless of config.
        self._config = dict(config or {})

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def target_id(self) -> str:
        return self._target_id

    @property
    def tags(self) -> list[str]:
        return self._tags

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _client(
        self, *, timeout: float | None = None, no_read_timeout: bool = False
    ) -> httpx.AsyncClient:
        """Build the httpx client for one outbound call to this Volundr target.

        Routes through ``guild_transport.build_guild_httpx_client`` — the same
        choke point every other outbound Guild call (Niuu's own aggregate
        REST, health probe, WebSocket proxies, SSE stream) uses — so a
        registered instance's https-unless-allow_plaintext policy and
        optional TLS pin apply here too, instead of this adapter dialling
        whatever scheme/host the row has with a bare ``httpx.AsyncClient``. A
        policy or pin violation raises ``GuildTransportError`` (a
        ``GuildInsecureTransportError``/``GuildTLSPinMismatchError``/
        ``GuildTransportUnreachableError``); callers never catch it here to
        silently skip this target (see ``.claude/rules/no-fallbacks.md`` —
        the caller that dispatches to this target decides what to do with the
        raise, same as any other Volundr HTTP failure).

        *no_read_timeout* is for the one streaming call
        (``subscribe_activity``), which bounds idle reads itself via
        ``asyncio.wait_for``/``_SSE_READ_TIMEOUT`` and must not also be cut
        off by httpx's own read timeout.
        """
        client = await build_guild_httpx_client(
            self,
            dial_url=self._base_url,
            timeout_seconds=timeout if timeout is not None else self._timeout,
        )
        if no_read_timeout:
            client.timeout = httpx.Timeout(None, connect=client.timeout.connect)
        return client

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

        client = await self._client(timeout=self._timeout)
        async with client:
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

    async def resolve_delivery_ref(
        self,
        repository: str,
        ref: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ):
        from niuu.domain.delivery import ResolvedRef  # noqa: PLC0415

        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.post(
                f"{self._base_url}/api/v1/forge/delivery/refs/resolve",
                headers=self._headers(auth_token, principal),
                json={"repository": repository, "ref": ref},
            )
        response.raise_for_status()
        return ResolvedRef.model_validate(response.json())

    async def validate_delivery_evidence(
        self,
        evidence: CandidateEvidence,
        *,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> EvidenceValidationReport:
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.post(
                f"{self._base_url}/api/v1/forge/delivery/evidence/validate",
                headers=self._headers(auth_token, principal),
                json={"evidence": evidence.model_dump(mode="json"), "policy_id": policy_id},
            )
        response.raise_for_status()
        return EvidenceValidationReport.model_validate(response.json())

    async def reconcile_delivery_merge(
        self,
        request: MergeRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> MergeReceipt:
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.post(
                f"{self._base_url}/api/v1/forge/delivery/forge/reconcile",
                headers=self._headers(auth_token, principal),
                json=request.model_dump(mode="json"),
            )
        response.raise_for_status()
        return MergeReceipt.model_validate(response.json())

    async def describe_delivery_policy(
        self,
        *,
        campaign_id: str,
        repository: str,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> AcceptancePolicy:
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.post(
                f"{self._base_url}/api/v1/forge/delivery/evidence/policy",
                headers=self._headers(auth_token, principal),
                json={
                    "campaign_id": campaign_id,
                    "repository": repository,
                    "policy_id": policy_id,
                },
            )
        response.raise_for_status()
        return AcceptancePolicy.model_validate(response.json())

    async def inspect_delivery_candidate(
        self,
        repository: str,
        review_number: int,
        *,
        campaign_id: str,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> tuple[ReviewCandidate, CheckReceipt]:
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.post(
                f"{self._base_url}/api/v1/forge/delivery/forge/inspect",
                headers=self._headers(auth_token, principal),
                json={
                    "campaign_id": campaign_id,
                    "repository": repository,
                    "review_number": review_number,
                    "policy_id": policy_id,
                },
            )
        response.raise_for_status()
        payload = response.json()
        return (
            ReviewCandidate.model_validate(payload["candidate"]),
            CheckReceipt.model_validate(payload["checks"]),
        )

    async def inspect_delivery_integration(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
        *,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> IntegrationCandidateInspection:
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.post(
                f"{self._base_url}/api/v1/forge/delivery/workspaces/integration/inspect",
                headers=self._headers(auth_token, principal),
                json={
                    "allocation": allocation.model_dump(mode="json"),
                    "integration_receipt": receipt.model_dump(mode="json"),
                    "policy_id": policy_id,
                },
            )
        response.raise_for_status()
        return IntegrationCandidateInspection.model_validate(response.json())

    async def inspect_delivery_integration_chain(
        self,
        allocation: WorkspaceAllocation,
        receipts: tuple[IntegrationReceipt, ...],
        *,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> IntegrationCandidateInspection:
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.post(
                f"{self._base_url}/api/v1/forge/delivery/workspaces/integration/inspect-chain",
                headers=self._headers(auth_token, principal),
                json={
                    "allocation": allocation.model_dump(mode="json"),
                    "integration_receipts": [
                        receipt.model_dump(mode="json") for receipt in receipts
                    ],
                    "policy_id": policy_id,
                },
            )
        response.raise_for_status()
        return IntegrationCandidateInspection.model_validate(response.json())

    async def get_current_portable_persona(
        self,
        persona_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> PortablePersonaDefinition | None:
        encoded_id = quote(persona_id, safe="")
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.get(
                f"{self._base_url}/api/v1/personas/{encoded_id}/portable",
                headers=self._headers(auth_token, principal),
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return parse_portable_persona(response.json())

    async def get_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession | None:
        client = await self._client(timeout=self._timeout)
        async with client:
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
        client = await self._client(timeout=self._timeout)
        async with client:
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
        client = await self._client(timeout=self._timeout)
        async with client:
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
        client = await self._client(timeout=self._timeout)
        async with client:
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
        client = await self._client(timeout=self._timeout)
        async with client:
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

        base_url = _session_chat_base_url(
            session.chat_endpoint, gateway_base_url=self._base_url, session_id=session_id
        )
        client = await self._client(timeout=self._timeout)
        async with client:
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

    async def publish_workflow_event(
        self,
        session_id: str,
        event_type: str,
        content: str,
        *,
        payload: dict | None = None,
        request_id: str,
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
        base_url = _session_chat_base_url(
            session.chat_endpoint, gateway_base_url=self._base_url, session_id=session_id
        )
        client = await self._client(timeout=self._timeout)
        async with client:
            resp = await client.post(
                f"{base_url}/api/room/workflow-events",
                headers=self._headers(auth_token, principal),
                json={
                    "event_type": event_type,
                    "content": content,
                    "payload": dict(payload or {}),
                    "request_id": request_id,
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
        client = await self._client(timeout=self._timeout)
        async with client:
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
        client = await self._client(timeout=self._timeout)
        async with client:
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
        client = await self._client(timeout=self._timeout)
        async with client:
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
        client = await self._client(timeout=self._timeout)
        async with client:
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
        headers = self._headers(auth_token, principal)
        url = f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}"
        client = await self._client(timeout=self._timeout)
        async with client:
            resp = await client.post(
                f"{url}/stop",
                headers=headers,
            )
            if resp.status_code == 404:
                return
            if resp.status_code == 409:
                current = await client.get(url, headers=headers)
                current.raise_for_status()
                status = str(current.json().get("status") or "").strip().lower()
                if status == "stopped":
                    return
            resp.raise_for_status()

    async def list_integration_ids(
        self,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[str]:
        """Fetch the user's enabled integration IDs from this Volundr instance."""
        client = await self._client(timeout=15.0)
        async with client:
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
        client = await self._client(timeout=15.0)
        async with client:
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
        client = await self._client(timeout=15.0)
        async with client:
            resp = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/conversation",
                headers=self._headers(auth_token, principal),
            )
            resp.raise_for_status()
            return resp.json()

    async def get_public_session_log_page(
        self,
        session_id: str,
        *,
        after: int,
        limit: int,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> PublicSessionLogPage:
        """Fetch the default, internal-content-hidden Forge event-log view."""
        client = await self._client(timeout=self._timeout)
        async with client:
            response = await client.get(
                f"{self._base_url}{FORGE_SESSIONS_PATH}/{session_id}/log/page",
                headers=self._headers(auth_token, principal),
                params={"after": after, "limit": limit, "show_internal": False},
            )
        response.raise_for_status()
        payload = response.json()
        raw_entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(raw_entries, list):
            raise ValueError("Volundr session log page response must contain entries")
        entries = tuple(
            PublicSessionLogEntry(
                session_id=str(item["session_id"]),
                seq=int(item["seq"]),
                kind=str(item["kind"]),
                role=str(item["role"]) if item.get("role") is not None else None,
                request_id=(
                    str(item["request_id"]) if item.get("request_id") is not None else None
                ),
                payload=dict(item.get("payload") or {}),
                ts=datetime.fromisoformat(str(item["ts"])),
            )
            for item in raw_entries
        )
        return PublicSessionLogPage(
            entries=entries,
            scanned_through=int(payload["scannedThrough"]),
            has_more=bool(payload["hasMore"]),
        )

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

    async def subscribe_activity(
        self,
    ) -> AsyncGenerator[ActivityEvent | ActivityStreamConnected, None]:
        """Subscribe to the Volundr SSE stream and yield activity + session lifecycle events."""
        url = f"{self._base_url}{FORGE_SESSIONS_PATH}/stream"
        client = await self._client(no_read_timeout=True)
        async with client:
            async with client.stream("GET", url, headers=self._headers()) as resp:
                resp.raise_for_status()
                # The request succeeded and the server accepted the stream —
                # the connection is genuinely open now, not just "we started
                # trying to connect". Yield this before anything else so a
                # caller can start its own "how long has this really been
                # connected" clock at the right moment; a connect that hangs
                # to timeout or is refused raises above and never reaches here.
                yield ActivityStreamConnected()
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


def _session_chat_base_url(
    chat_endpoint: str, *, gateway_base_url: str = "", session_id: str = ""
) -> str:
    normalized = chat_endpoint.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid chat endpoint: {chat_endpoint}")
    # Gateway session paths belong to the same Forge connection that resolved
    # the session. Its advertised browser origin need not be reachable from
    # Ting (for example, a host address advertised by a Docker deployment).
    # Direct Skuld endpoints keep their own origin and route.
    if gateway_base_url and session_id and parsed.path == f"/s/{session_id}/session":
        return f"{gateway_base_url.rstrip('/')}/s/{session_id}"
    path = parsed.path
    if path.endswith("/session"):
        path = path[: -len("/session")]
    return parsed._replace(path=path, params="", query="", fragment="").geturl().rstrip("/")
