#!/usr/bin/env python3
"""Live proof for the multi-instance guild topology.

Exercises:
- instance visibility for tenant- and user-scoped registrations
- dispatch target visibility per principal
- assigning a saga/project to a Volundr target and dispatching without an explicit override
- dispatching one run to a second Volundr through an explicit target override
- verifying the spawned sessions through the central aggregate Volundr API
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8080"
OUTPUT_PATH = ROOT / "build" / "guild-run" / "guild-e2e-proof.json"


def _headers(
    *,
    user_id: str,
    tenant_id: str,
    roles: list[str] | None = None,
) -> dict[str, str]:
    return {
        "x-auth-user-id": user_id,
        "x-auth-email": f"{user_id}@example.test",
        "x-auth-tenant": tenant_id,
        "x-auth-roles": ",".join(roles or ["volundr:developer"]),
    }


def _branch_name() -> str:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return result.stdout.strip()


def _repo_url() -> str:
    return f"file://{ROOT}"


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    expected: int = 200,
    json_body: dict[str, Any] | list[Any] | None = None,
) -> Any:
    response = client.request(
        method,
        f"{BASE_URL}{path}",
        headers=headers,
        json=json_body,
    )
    if response.status_code != expected:
        raise RuntimeError(
            f"{method} {path} -> {response.status_code}: {response.text[:1000]}"
        )
    if not response.content:
        return None
    return response.json()


def _request_optional(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    expected: int = 200,
    json_body: dict[str, Any] | list[Any] | None = None,
    allowed_statuses: tuple[int, ...] = (),
) -> Any | None:
    response = client.request(
        method,
        f"{BASE_URL}{path}",
        headers=headers,
        json=json_body,
    )
    if response.status_code == expected:
        if not response.content:
            return None
        return response.json()
    if response.status_code in allowed_statuses:
        return None
    raise RuntimeError(
        f"{method} {path} -> {response.status_code}: {response.text[:1000]}"
    )


def _wait_for_session(
    client: httpx.Client,
    *,
    instance_id: str,
    session_id: str,
    headers: dict[str, str],
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sessions = _request(
            client,
            "GET",
            f"/api/v1/niuu/instances/{instance_id}/sessions",
            headers=headers,
        )
        if any(session["id"] == session_id for session in sessions):
            return sessions
        time.sleep(1)
    raise TimeoutError(
        f"Session {session_id} did not appear in /api/v1/niuu/instances/{instance_id}/sessions"
    )


def _wait_for_aggregate_session(
    client: httpx.Client,
    *,
    headers: dict[str, str],
    session_id: str,
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sessions = _request(
            client,
            "GET",
            "/api/v1/niuu/volundr/sessions",
            headers=headers,
        )
        if any(session["id"] == session_id for session in sessions):
            return sessions
        time.sleep(1)
    raise TimeoutError(f"Session {session_id} did not appear in /api/v1/niuu/volundr/sessions")


def _wait_for_activity(
    client: httpx.Client,
    *,
    headers: dict[str, str],
    session_id: str,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last_status: str | None = None
    while time.time() < deadline:
        detail = _request(
            client,
            "GET",
            f"/api/v1/niuu/volundr/sessions/{session_id}",
            headers=headers,
        )
        last_status = str(detail.get("status", ""))
        if last_status == "failed":
            raise RuntimeError(
                f"Session {session_id} failed before activity was available: {detail}"
            )

        conversation = _request_optional(
            client,
            "GET",
            f"/api/v1/niuu/volundr/sessions/{session_id}/conversation",
            headers=headers,
            allowed_statuses=(502,),
        )
        logs = _request_optional(
            client,
            "GET",
            f"/api/v1/niuu/volundr/sessions/{session_id}/logs?lines=20",
            headers=headers,
            allowed_statuses=(502,),
        )
        aggregated_logs = _request_optional(
            client,
            "GET",
            f"/api/v1/niuu/volundr/sessions/{session_id}/logs/aggregate?lines=20",
            headers=headers,
            allowed_statuses=(502,),
        )
        chronicle = _request_optional(
            client,
            "GET",
            f"/api/v1/niuu/volundr/chronicles/{session_id}/timeline",
            headers=headers,
            allowed_statuses=(404, 502),
        )
        turns = conversation.get("turns", []) if isinstance(conversation, dict) else []
        log_lines = logs.get("lines", []) if isinstance(logs, dict) else []
        aggregate_lines = (
            aggregated_logs.get("lines", []) if isinstance(aggregated_logs, dict) else []
        )
        chronicle_events = chronicle.get("events", []) if isinstance(chronicle, dict) else []
        if turns and log_lines and aggregate_lines and chronicle_events:
            return {
                "detail": detail,
                "conversation": conversation,
                "logs": logs,
                "aggregatedLogs": aggregated_logs,
                "chronicle": chronicle,
            }
        time.sleep(2)
    raise TimeoutError(
        f"Session {session_id} never produced aggregate activity (last status={last_status})"
    )


def _queue_item_for_slug(
    queue: list[dict[str, Any]],
    slug: str,
) -> dict[str, Any]:
    for item in queue:
        if item["saga_slug"] == slug:
            return item
    raise LookupError(f"No queue item found for slug {slug}")


def _commit_saga(
    client: httpx.Client,
    *,
    headers: dict[str, str],
    saga_name: str,
    slug: str,
    ) -> dict[str, Any]:
    commit_body = {
        "name": saga_name,
        "slug": slug,
        "description": f"Guild proof for {slug}",
        "repos": [_repo_url()],
        "base_branch": _branch_name(),
        "phases": [
            {
                "name": "Proof",
                "runs": [
                    {
                        "name": f"Dispatch {slug}",
                        "description": "Spawn a session on the selected guild instance",
                        "acceptance_criteria": ["Session is created on the targeted instance"],
                        "declared_files": ["proof.txt"],
                        "estimate_hours": 0.1,
                    }
                ],
            }
        ],
    }
    saga = _request(
        client,
        "POST",
        "/api/v1/ting/sagas/commit",
        headers=headers,
        json_body=commit_body,
        expected=201,
    )
    return saga


def _dispatch_one(
    client: httpx.Client,
    *,
    headers: dict[str, str],
    slug: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    queue = _request(client, "GET", "/api/v1/ting/dispatch/queue", headers=headers)
    queue_item = _queue_item_for_slug(queue, slug)
    body: dict[str, Any] = {
        "items": [
            {
                "saga_id": queue_item["saga_id"],
                "issue_id": queue_item["issue_id"],
                "repo": _repo_url(),
            }
        ]
    }
    if target_id:
        body["items"][0]["connection_id"] = target_id
        body["connection_id"] = target_id

    results = _request(
        client,
        "POST",
        "/api/v1/ting/dispatch/approve",
        headers=headers,
        json_body=body,
    )
    if len(results) != 1:
        raise RuntimeError(f"Expected one dispatch result for {slug}, got {results!r}")
    return {
        "queue_item": queue_item,
        "dispatch_result": results[0],
    }


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    suffix = str(int(time.time()))
    tenant_instance_name = f"Tenant A Beta {suffix}"
    user_instance_name = f"User B Alpha {suffix}"
    tenant_a_headers = _headers(user_id="guild-user-a", tenant_id="tenant-a")
    tenant_b_headers = _headers(user_id="guild-user-b", tenant_id="tenant-b")

    with httpx.Client(timeout=30.0) as client:
        health = _request(client, "GET", "/health")
        system_targets = _request(
            client,
            "GET",
            "/api/v1/ting/dispatch/targets",
            headers=tenant_a_headers,
        )
        tenant_instance = _request(
            client,
            "POST",
            "/api/v1/niuu/instances",
            headers=tenant_a_headers,
            expected=201,
            json_body={
                "kind": "volundr",
                "slug": f"tenant-a-beta-{suffix}",
                "name": tenant_instance_name,
                "baseUrl": "http://127.0.0.1:8282",
                "visibility": "tenant",
                "enabled": True,
                "isDefault": False,
                "config": {},
            },
        )
        user_instance = _request(
            client,
            "POST",
            "/api/v1/niuu/instances",
            headers=tenant_b_headers,
            expected=201,
            json_body={
                "kind": "volundr",
                "slug": f"user-b-alpha-{suffix}",
                "name": user_instance_name,
                "baseUrl": "http://127.0.0.1:8181",
                "visibility": "user",
                "enabled": True,
                "isDefault": False,
                "config": {},
            },
        )

        visible_a = _request(
            client,
            "GET",
            "/api/v1/niuu/instances?kind=volundr",
            headers=tenant_a_headers,
        )
        visible_b = _request(
            client,
            "GET",
            "/api/v1/niuu/instances?kind=volundr",
            headers=tenant_b_headers,
        )
        targets_a = _request(
            client,
            "GET",
            "/api/v1/ting/dispatch/targets",
            headers=tenant_a_headers,
        )
        targets_b = _request(
            client,
            "GET",
            "/api/v1/ting/dispatch/targets",
            headers=tenant_b_headers,
        )

        tenant_names_a = {item["name"] for item in visible_a}
        tenant_names_b = {item["name"] for item in visible_b}
        if tenant_instance_name not in tenant_names_a:
            raise AssertionError("Tenant A should see the tenant-scoped instance")
        if tenant_instance_name in tenant_names_b:
            raise AssertionError("Tenant B must not see Tenant A's tenant-scoped instance")
        if user_instance_name not in tenant_names_b:
            raise AssertionError("User B should see the user-scoped instance")
        if user_instance_name in tenant_names_a:
            raise AssertionError("User A must not see User B's user-scoped instance")

        target_names_a = {item["name"] for item in targets_a}
        target_names_b = {item["name"] for item in targets_b}
        if tenant_instance_name not in target_names_a:
            raise AssertionError("Tenant A dispatch targets should include the tenant instance")
        if tenant_instance_name in target_names_b:
            raise AssertionError("Tenant B dispatch targets must not include Tenant A Beta")
        if user_instance_name not in target_names_b:
            raise AssertionError("User B dispatch targets should include the user instance")

        tenant_slug = f"guild-tenant-dispatch-{suffix}"
        user_slug = f"guild-user-dispatch-{suffix}"
        tenant_saga = _commit_saga(
            client,
            headers=tenant_a_headers,
            saga_name=f"Guild Tenant Dispatch {suffix}",
            slug=tenant_slug,
        )
        assigned_tenant_saga = _request(
            client,
            "PUT",
            f"/api/v1/ting/sagas/{tenant_saga['id']}/target",
            headers=tenant_a_headers,
            json_body={"instance_id": tenant_instance["id"]},
        )
        user_saga = _commit_saga(
            client,
            headers=tenant_b_headers,
            saga_name=f"Guild User Dispatch {suffix}",
            slug=user_slug,
        )
        tenant_dispatch = _dispatch_one(
            client,
            headers=tenant_a_headers,
            slug=tenant_slug,
        )
        user_dispatch = _dispatch_one(
            client,
            headers=tenant_b_headers,
            slug=user_slug,
            target_id=user_instance["id"],
        )

        tenant_sessions = _wait_for_session(
            client,
            instance_id=tenant_instance["id"],
            session_id=tenant_dispatch["dispatch_result"]["session_id"],
            headers=tenant_a_headers,
        )
        user_sessions = _wait_for_session(
            client,
            instance_id=user_instance["id"],
            session_id=user_dispatch["dispatch_result"]["session_id"],
            headers=tenant_b_headers,
        )
        aggregate_tenant_sessions = _wait_for_aggregate_session(
            client,
            headers=tenant_a_headers,
            session_id=tenant_dispatch["dispatch_result"]["session_id"],
        )
        aggregate_user_sessions = _wait_for_aggregate_session(
            client,
            headers=tenant_b_headers,
            session_id=user_dispatch["dispatch_result"]["session_id"],
        )
        tenant_activity = _wait_for_activity(
            client,
            headers=tenant_a_headers,
            session_id=tenant_dispatch["dispatch_result"]["session_id"],
        )
        user_activity = _wait_for_activity(
            client,
            headers=tenant_b_headers,
            session_id=user_dispatch["dispatch_result"]["session_id"],
        )
        tenant_aggregate_ids = {session["id"] for session in aggregate_tenant_sessions}
        user_aggregate_ids = {session["id"] for session in aggregate_user_sessions}
        if user_dispatch["dispatch_result"]["session_id"] in tenant_aggregate_ids:
            raise AssertionError("Tenant A aggregate view must not contain User B's session")
        if tenant_dispatch["dispatch_result"]["session_id"] in user_aggregate_ids:
            raise AssertionError("Tenant B aggregate view must not contain Tenant A's session")

    proof = {
        "health": health,
        "systemTargets": system_targets,
        "tenantA": {
            "visibleInstances": visible_a,
            "targets": targets_a,
            "tenantInstance": tenant_instance,
            "assignedSaga": assigned_tenant_saga,
            "dispatch": tenant_dispatch,
            "instanceSessions": tenant_sessions,
            "aggregateSessions": aggregate_tenant_sessions,
            "aggregateActivity": tenant_activity,
        },
        "tenantB": {
            "visibleInstances": visible_b,
            "targets": targets_b,
            "userInstance": user_instance,
            "saga": user_saga,
            "dispatch": user_dispatch,
            "instanceSessions": user_sessions,
            "aggregateSessions": aggregate_user_sessions,
            "aggregateActivity": user_activity,
        },
    }
    OUTPUT_PATH.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    print(f"\nWrote proof to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - operational script
        print(f"guild-e2e failed: {exc}", file=sys.stderr)
        raise
