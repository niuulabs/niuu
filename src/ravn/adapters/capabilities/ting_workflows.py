"""Workflow capability adapter backed by Ting's existing workflow catalog.

The adapter talks to Ting over HTTP and authenticates by exchanging the
resident's projected workload token for the same short-lived Niuu workload JWT
that Envoy already accepts. Ravn does not import Ting internals.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from ravn.domain.capability_catalog import WorkflowCapability
from ravn.ports.capability import (
    WorkflowArtifact,
    WorkflowArtifactContent,
    WorkflowCapabilityPort,
    WorkflowLaunchRequest,
    WorkflowLaunchResult,
    WorkflowRunEvent,
    WorkflowRunReference,
    WorkflowRunStatus,
)


class TingWorkflowCapabilityAdapter(WorkflowCapabilityPort):
    """Discover and launch workflows through Ting's REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        workload_token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
        exchange_url: str = "",
        audiences: list[str] | None = None,
        bearer_token: str = "",
        external_token_env: str = "",
        allow_anonymous: bool = False,
        token_ttl_skew_seconds: int = 30,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._workload_token_file = Path(workload_token_file)
        self._exchange_url = exchange_url.rstrip("/") or (
            f"{self._base_url}/api/v1/tokens/workload/exchange"
        )
        self._audiences = audiences or ["volundr-api", "forge", "ting", "mimir", "guild"]
        self._bearer_token = bearer_token
        self._external_token_env = external_token_env
        self._allow_anonymous = allow_anonymous
        self._token_ttl_skew_seconds = token_ttl_skew_seconds
        self._timeout_seconds = timeout_seconds
        self._cached_token = ""
        self._cached_expires_at = 0
        self._cached_identity: dict[str, Any] = {}

    async def list_workflows(self) -> list[WorkflowCapability]:
        resp = await self._request("GET", f"{self._base_url}/api/v1/ting/workflows")
        if resp.status_code != 200:
            raise RuntimeError(f"Ting workflow discovery returned HTTP {resp.status_code}")
        body = resp.json()
        if not isinstance(body, list):
            raise RuntimeError("Ting workflow discovery returned a non-list body")
        return [_workflow_from_body(item) for item in body if isinstance(item, dict)]

    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        identity = await self._workload_identity_metadata()
        body: dict[str, Any] = {"prompt": request.prompt}
        if request.session_name:
            body["sessionName"] = request.session_name
        if request.repo:
            body["repo"] = request.repo
        if request.branch:
            body["branch"] = request.branch
        if request.connection_id:
            body["connectionId"] = request.connection_id
        provenance = dict(request.provenance)
        if identity:
            provenance["workload_identity"] = dict(identity)
        if provenance:
            body["provenance"] = provenance

        resp = await self._request(
            "POST",
            f"{self._base_url}/api/v1/ting/workflows/{request.workflow_id}/launch",
            json=body,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Ting workflow launch returned HTTP {resp.status_code}")
        raw = resp.json()
        if not isinstance(raw, dict):
            raise RuntimeError("Ting workflow launch returned a non-object body")
        return WorkflowLaunchResult(
            workflow_id=str(raw.get("workflowId") or raw.get("workflow_id") or request.workflow_id),
            workflow_name=str(raw.get("workflowName") or raw.get("workflow_name") or ""),
            session_id=str(raw.get("sessionId") or raw.get("session_id") or ""),
            session_name=str(raw.get("sessionName") or raw.get("session_name") or ""),
            status=str(raw.get("status") or ""),
            slug=str(raw.get("slug") or ""),
            cluster_name=str(raw.get("clusterName") or raw.get("cluster_name") or ""),
            owner_id=str(identity.get("owner_id") or ""),
            tenant_id=str(identity.get("tenant_id") or ""),
            workload_subject=str(identity.get("workload_subject") or ""),
            workload_name=str(identity.get("workload_name") or ""),
            raw=raw,
        )

    async def get_workflow_status(self, reference: WorkflowRunReference) -> WorkflowRunStatus:
        campaign = await self._resolve_campaign(reference)
        if campaign is not None:
            return _status_from_campaign(campaign)

        if reference.session_id:
            session = await self._get_session(reference.session_id)
            if session is not None:
                return _status_from_session(session, reference=reference)

        raise RuntimeError("Workflow run not found")

    async def list_workflow_events(
        self,
        reference: WorkflowRunReference,
        *,
        limit: int = 100,
    ) -> list[WorkflowRunEvent]:
        events: list[WorkflowRunEvent] = []
        campaign = await self._resolve_campaign(reference)
        if campaign is not None:
            events.append(_event_from_campaign(campaign))

        resp = await self._request(
            "GET",
            f"{self._base_url}/api/v1/ting/dispatcher/log",
            params={"limit": max(1, min(1000, int(limit)))},
        )
        if resp.status_code == 404:
            return events[:limit]
        if resp.status_code != 200:
            raise RuntimeError(f"Ting workflow event lookup returned HTTP {resp.status_code}")
        raw = resp.json()
        items = raw.get("events") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return events[:limit]
        for item in items:
            if not isinstance(item, dict):
                continue
            event = _event_from_log_item(item)
            if _event_matches_reference(event, reference, campaign):
                events.append(event)
        return events[:limit]

    async def list_workflow_artifacts(
        self,
        reference: WorkflowRunReference,
    ) -> list[WorkflowArtifact]:
        campaign = await self._resolve_campaign(reference)
        if campaign is None:
            session = await self._get_session(reference.session_id) if reference.session_id else None
            artifacts = _local_session_artifacts(session)
            if not artifacts and reference.session_id:
                artifacts = _local_session_artifacts(
                    await self._get_forge_session(reference.session_id)
                )
            return artifacts
        slug = str(campaign.get("slug") or reference.slug)
        resp = await self._request(
            "GET",
            f"{self._base_url}/api/v1/ting/research/campaigns/{slug}/artifacts",
        )
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            raise RuntimeError(f"Ting workflow artifact lookup returned HTTP {resp.status_code}")
        body = resp.json()
        if not isinstance(body, list):
            return []
        canonical = _canonical_artifact_paths(campaign)
        return [
            _artifact_from_body(item, canonical=canonical)
            for item in body
            if isinstance(item, dict)
        ]

    async def read_workflow_artifact(
        self,
        reference: WorkflowRunReference,
        *,
        path: str,
    ) -> WorkflowArtifactContent:
        campaign = await self._resolve_campaign(reference)
        if campaign is None:
            session = await self._get_session(reference.session_id) if reference.session_id else None
            try:
                return _read_local_session_artifact(session, path=path)
            except RuntimeError:
                if not reference.session_id:
                    raise
                return _read_local_session_artifact(
                    await self._get_forge_session(reference.session_id),
                    path=path,
                )
        slug = str(campaign.get("slug") or reference.slug)
        resp = await self._request(
            "GET",
            f"{self._base_url}/api/v1/ting/research/campaigns/{slug}/artifact",
            params={"path": path},
        )
        if resp.status_code == 404:
            raise RuntimeError("Workflow artifact not found")
        if resp.status_code != 200:
            raise RuntimeError(f"Ting workflow artifact read returned HTTP {resp.status_code}")
        body = resp.json()
        if not isinstance(body, dict):
            raise RuntimeError("Ting workflow artifact read returned a non-object body")
        canonical = _canonical_artifact_paths(campaign)
        artifact = _artifact_from_body(body, canonical=canonical)
        return WorkflowArtifactContent(
            artifact=artifact,
            content=str(body.get("content") or ""),
            raw=body,
        )

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = await self._workload_bearer_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def _workload_bearer_token(self) -> str:
        static = self._static_bearer_token()
        if static:
            return static

        now = int(time.time())
        if self._cached_token and self._cached_expires_at - self._token_ttl_skew_seconds > now:
            return self._cached_token

        if not self._workload_token_file.exists():
            if self._allow_anonymous:
                return ""
            raise RuntimeError(f"workload token file does not exist: {self._workload_token_file}")
        proof = self._workload_token_file.read_text(encoding="utf-8").strip()
        if not proof:
            if self._allow_anonymous:
                return ""
            raise RuntimeError(f"workload token file is empty: {self._workload_token_file}")
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            resp = await client.post(
                self._exchange_url,
                json={"token": proof, "audiences": self._audiences},
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"workload token exchange returned HTTP {resp.status_code}")
        body = resp.json()
        token = str(body.get("token") or "")
        if not token:
            raise RuntimeError("workload token exchange returned no token")
        self._cached_token = token
        self._cached_expires_at = int(body.get("expiresAt") or body.get("expires_at") or now)
        self._cached_identity = _identity_from_exchange(body)
        return token

    async def _workload_identity_metadata(self) -> dict[str, Any]:
        if self._static_bearer_token():
            return {}
        await self._workload_bearer_token()
        return dict(self._cached_identity)

    def _static_bearer_token(self) -> str:
        if self._bearer_token:
            return self._bearer_token
        if not self._external_token_env:
            return ""
        token = os.environ.get(self._external_token_env, "")
        if not token:
            raise RuntimeError(
                f"external token env var is not set: {self._external_token_env}"
            )
        return token

    async def _resolve_campaign(self, reference: WorkflowRunReference) -> dict[str, Any] | None:
        if reference.slug:
            campaign = await self._get_campaign_by_slug(reference.slug)
            if campaign is not None:
                return campaign

        if not reference.session_id:
            return None

        resp = await self._request("GET", f"{self._base_url}/api/v1/ting/research/campaigns")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"Ting campaign lookup returned HTTP {resp.status_code}")
        body = resp.json()
        if not isinstance(body, list):
            return None
        for item in body:
            if not isinstance(item, dict):
                continue
            if str(item.get("sessionId") or item.get("session_id") or "") != reference.session_id:
                continue
            slug = str(item.get("slug") or "")
            if slug:
                return await self._get_campaign_by_slug(slug) or item
            return item
        return None

    async def _get_campaign_by_slug(self, slug: str) -> dict[str, Any] | None:
        resp = await self._request(
            "GET",
            f"{self._base_url}/api/v1/ting/research/campaigns/{slug}",
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"Ting campaign lookup returned HTTP {resp.status_code}")
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def _get_session(self, session_id: str) -> dict[str, Any] | None:
        for path in (
            f"/api/v1/ting/sessions/{session_id}",
            f"/api/v1/forge/sessions/{session_id}",
        ):
            resp = await self._request("GET", f"{self._base_url}{path}")
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"Ting session lookup returned HTTP {resp.status_code}")
            body = resp.json()
            return body if isinstance(body, dict) else None
        return None

    async def _get_forge_session(self, session_id: str) -> dict[str, Any] | None:
        resp = await self._request("GET", f"{self._base_url}/api/v1/forge/sessions/{session_id}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"Forge session lookup returned HTTP {resp.status_code}")
        body = resp.json()
        return body if isinstance(body, dict) else None


def _workflow_from_body(body: dict[str, Any]) -> WorkflowCapability:
    tags = body.get("tags")
    if not isinstance(tags, list):
        tags = []
    return WorkflowCapability(
        workflow_id=str(body.get("id") or ""),
        name=str(body.get("name") or ""),
        description=str(body.get("description") or ""),
        version=str(body.get("version") or ""),
        tags=[str(tag) for tag in tags if str(tag).strip()],
        metadata={
            "scope": body.get("scope"),
            "owner_id": body.get("ownerId") or body.get("owner_id"),
        },
    )


def _status_from_campaign(body: dict[str, Any]) -> WorkflowRunStatus:
    state = str(body.get("status") or "")
    return WorkflowRunStatus(
        state=state,
        session_id=str(body.get("sessionId") or body.get("session_id") or ""),
        slug=str(body.get("slug") or ""),
        workflow_id=str(body.get("workflowId") or body.get("workflow_id") or ""),
        workflow_name=str(body.get("workflowName") or body.get("workflow_name") or ""),
        session_name=str(body.get("sessionName") or body.get("session_name") or ""),
        cluster_name=str(body.get("clusterName") or body.get("cluster_name") or ""),
        active_stage_id=str(body.get("activeStageId") or body.get("active_stage_id") or ""),
        stage_state=_dict_list(body.get("stageState") or body.get("stage_state")),
        terminal=state.casefold() in {"completed", "failed"},
        raw=body,
    )


def _status_from_session(
    body: dict[str, Any],
    *,
    reference: WorkflowRunReference,
) -> WorkflowRunStatus:
    state = str(body.get("status") or body.get("state") or "")
    session_id = str(
        body.get("sessionId") or body.get("session_id") or body.get("id") or reference.session_id
    )
    session_name = str(
        body.get("sessionName") or body.get("session_name") or body.get("name") or ""
    )
    cluster_name = str(
        body.get("clusterName") or body.get("cluster_name") or body.get("instance_name") or ""
    )
    terminal = state.casefold() in {"completed", "complete", "failed", "stopped", "cancelled"}
    return WorkflowRunStatus(
        state=state,
        session_id=session_id,
        slug=reference.slug,
        workflow_id=reference.workflow_id,
        session_name=session_name,
        cluster_name=cluster_name,
        terminal=terminal,
        raw=body,
    )


def _event_from_campaign(body: dict[str, Any]) -> WorkflowRunEvent:
    return WorkflowRunEvent(
        event_type="workflow.campaign.status",
        data={
            "status": body.get("status"),
            "slug": body.get("slug"),
            "session_id": body.get("sessionId") or body.get("session_id"),
            "workflow_id": body.get("workflowId") or body.get("workflow_id"),
            "active_stage_id": body.get("activeStageId") or body.get("active_stage_id"),
            "stage_state": body.get("stageState") or body.get("stage_state") or [],
            "canonical_artifacts": body.get("canonicalArtifacts")
            or body.get("canonical_artifacts")
            or {},
        },
        timestamp=str(body.get("updatedAt") or body.get("updated_at") or ""),
        source="ting:campaign",
        raw=body,
    )


def _event_from_log_item(item: dict[str, Any]) -> WorkflowRunEvent:
    data = item.get("data")
    timestamp = str(item.get("timestamp") or item.get("createdAt") or item.get("created_at") or "")
    return WorkflowRunEvent(
        event_type=str(item.get("event") or item.get("event_type") or ""),
        data=dict(data) if isinstance(data, dict) else {},
        timestamp=timestamp,
        source=str(item.get("source") or "ting:dispatcher"),
        raw=item,
    )


def _event_matches_reference(
    event: WorkflowRunEvent,
    reference: WorkflowRunReference,
    campaign: dict[str, Any] | None,
) -> bool:
    data = event.data
    candidates = {
        str(data.get("session_id") or data.get("sessionId") or ""),
        str(data.get("campaign_id") or data.get("campaignId") or ""),
        str(data.get("slug") or ""),
        str(data.get("workflow_id") or data.get("workflowId") or ""),
    }
    if campaign is not None:
        candidates.update(
            {
                str(campaign.get("id") or ""),
                str(campaign.get("slug") or ""),
                str(campaign.get("sessionId") or campaign.get("session_id") or ""),
                str(campaign.get("workflowId") or campaign.get("workflow_id") or ""),
            }
        )
    return any(
        value
        and value
        in {
            reference.session_id,
            reference.slug,
            reference.workflow_id,
        }
        for value in candidates
    )


def _artifact_from_body(body: dict[str, Any], *, canonical: set[str]) -> WorkflowArtifact:
    path = str(body.get("path") or "")
    source_ids = body.get("sourceIds") or body.get("source_ids") or []
    return WorkflowArtifact(
        path=path,
        title=str(body.get("title") or ""),
        kind=str(body.get("kind") or ""),
        summary=str(body.get("summary") or ""),
        publish_state=str(body.get("publishState") or body.get("publish_state") or ""),
        source_ids=[str(item) for item in source_ids if str(item).strip()]
        if isinstance(source_ids, list)
        else [],
        canonical=path in canonical,
        raw=body,
    )


def _canonical_artifact_paths(body: dict[str, Any]) -> set[str]:
    raw = body.get("canonicalArtifacts") or body.get("canonical_artifacts") or {}
    if not isinstance(raw, dict):
        return set()
    return {str(value) for value in raw.values() if str(value).strip()}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _local_session_artifacts(session: dict[str, Any] | None) -> list[WorkflowArtifact]:
    root = _session_workspace_root(session)
    if root is None:
        return []
    files: list[WorkflowArtifact] = []
    for candidate in sorted((root / "research" / "campaigns").glob("*/*.md")):
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(root).as_posix()
        files.append(
            WorkflowArtifact(
                path=rel,
                title=candidate.stem.replace("-", " ").replace("_", " ").title(),
                kind=candidate.stem,
                summary=f"Local workflow artifact {rel}",
                publish_state="local",
                canonical=candidate.name in {"summary.md", "plan.md", "brief.md"},
                raw={"path": rel, "source": "forge-workspace"},
            )
        )
    return files


def _read_local_session_artifact(
    session: dict[str, Any] | None,
    *,
    path: str,
) -> WorkflowArtifactContent:
    root = _session_workspace_root(session)
    if root is None:
        raise RuntimeError("Workflow run has no readable Ting campaign artifact owner")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise RuntimeError("Workflow artifact not found")
    artifact = next(
        (item for item in _local_session_artifacts(session) if item.path == path),
        WorkflowArtifact(
            path=path,
            title=Path(path).stem.replace("-", " ").replace("_", " ").title(),
            kind=Path(path).stem,
            summary=f"Local workflow artifact {path}",
            publish_state="local",
            raw={"path": path, "source": "forge-workspace"},
        ),
    )
    return WorkflowArtifactContent(
        artifact=artifact,
        content=target.read_text(encoding="utf-8"),
        raw={"path": path, "source": "forge-workspace"},
    )


def _session_workspace_root(session: dict[str, Any] | None) -> Path | None:
    if not isinstance(session, dict):
        return None
    endpoint = str(session.get("code_endpoint") or "")
    parsed = urlparse(endpoint)
    if parsed.scheme != "file":
        return None
    root = Path(unquote(parsed.path)).expanduser().resolve()
    return root if root.exists() and root.is_dir() else None


def _identity_from_exchange(body: dict[str, Any]) -> dict[str, Any]:
    principal = body.get("principal")
    if not isinstance(principal, dict):
        principal = {}
    roles = principal.get("roles")
    if not isinstance(roles, list):
        roles = []
    return {
        "owner_id": str(principal.get("userId") or principal.get("user_id") or ""),
        "tenant_id": str(principal.get("tenantId") or principal.get("tenant_id") or ""),
        "workload_subject": str(body.get("workloadSubject") or body.get("workload_subject") or ""),
        "workload_name": str(body.get("workloadName") or body.get("workload_name") or ""),
        "roles": [str(role) for role in roles if str(role).strip()],
    }
