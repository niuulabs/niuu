"""Seed runtime data for the additive Ravn HTTP API."""

from __future__ import annotations

from copy import deepcopy

_RAVENS = [
    {
        "id": "a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c",
        "persona_name": "sindri",
        "status": "active",
        "model": "claude-4-sonnet",
        "created_at": "2026-01-15T08:00:00Z",
        "updated_at": "2026-01-15T08:30:00Z",
        "location": "valaskjalf",
        "deployment": "production",
        "role": "build",
        "letter": "S",
        "summary": "Writes and edits source code across the stack.",
        "iteration_budget": 40,
        "write_routing": "local",
        "cascade": "sequential",
        "mounts": [
            {"name": "codebase", "role": "primary"},
            {"name": "docs", "role": "ro"},
        ],
        "mcp_servers": ["filesystem", "git", "bash"],
        "gateway_channels": ["slack-dev", "github-webhook"],
        "event_subscriptions": ["code.requested", "bug.fix.requested", "code.changed"],
    },
    {
        "id": "b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c",
        "persona_name": "vidar",
        "status": "active",
        "model": "claude-4-sonnet",
        "created_at": "2026-01-15T08:00:00Z",
        "updated_at": "2026-01-15T08:25:00Z",
        "location": "valhalla",
        "deployment": "production",
        "role": "autonomy",
        "letter": "V",
        "summary": "Fully autonomous general-purpose agent.",
        "iteration_budget": 100,
        "write_routing": "shared",
        "cascade": "parallel",
        "mounts": [
            {"name": "codebase", "role": "ro"},
            {"name": "reviews", "role": "primary"},
        ],
        "mcp_servers": ["filesystem", "git"],
        "gateway_channels": ["github-pr"],
        "event_subscriptions": ["code.changed", "review.requested", "review.completed"],
    },
    {
        "id": "c4d5e6f7-1a2b-4c3d-8e9f-0a1b2c3d4e5f",
        "persona_name": "muninn",
        "status": "active",
        "model": "claude-4-sonnet",
        "created_at": "2026-01-15T08:00:00Z",
        "updated_at": "2026-01-15T08:20:00Z",
        "location": "valaskjalf",
        "deployment": "production",
        "role": "knowledge",
        "letter": "M",
        "summary": "Curates and indexes knowledge into Mimir.",
        "iteration_budget": 20,
        "write_routing": "domain",
        "cascade": "sequential",
        "mounts": [{"name": "codebase", "role": "ro"}],
        "mcp_servers": ["filesystem", "mimir"],
        "gateway_channels": [],
        "event_subscriptions": ["code.changed", "mimir.index.requested"],
    },
]

_SESSIONS = [
    {
        "id": "10000001-0000-4000-8000-000000000001",
        "ravn_id": "a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c",
        "persona_name": "sindri",
        "persona_role": "build",
        "persona_letter": "S",
        "status": "running",
        "model": "claude-4-sonnet",
        "created_at": "2026-01-15T08:30:00Z",
        "title": "Implement login form",
        "message_count": 6,
        "token_count": 4820,
        "cost_usd": 0.18,
    },
    {
        "id": "10000001-0000-4000-8000-000000000002",
        "ravn_id": "b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c",
        "persona_name": "vidar",
        "persona_role": "autonomy",
        "persona_letter": "V",
        "status": "running",
        "model": "claude-4-sonnet",
        "created_at": "2026-01-15T08:15:00Z",
        "title": "Autonomous refactor - auth module",
        "message_count": 4,
        "token_count": 11200,
        "cost_usd": 0.42,
    },
    {
        "id": "10000001-0000-4000-8000-000000000003",
        "ravn_id": "c4d5e6f7-1a2b-4c3d-8e9f-0a1b2c3d4e5f",
        "persona_name": "muninn",
        "persona_role": "knowledge",
        "persona_letter": "M",
        "status": "idle",
        "model": "claude-4-sonnet",
        "created_at": "2026-01-15T08:10:00Z",
        "title": "Index new knowledge docs",
        "message_count": 3,
        "token_count": 1890,
        "cost_usd": 0.07,
    },
]

_MESSAGES = [
    {
        "id": "00000001-0000-4000-8000-000000000001",
        "session_id": "10000001-0000-4000-8000-000000000001",
        "kind": "user",
        "content": "Please implement the login form",
        "ts": "2026-01-15T08:30:01Z",
    },
    {
        "id": "00000001-0000-4000-8000-000000000002",
        "session_id": "10000001-0000-4000-8000-000000000001",
        "kind": "think",
        "content": "I need to inspect the current auth setup first.",
        "ts": "2026-01-15T08:30:02Z",
    },
    {
        "id": "00000001-0000-4000-8000-000000000003",
        "session_id": "10000001-0000-4000-8000-000000000001",
        "kind": "tool_call",
        "content": '{"path":"src/auth/LoginForm.tsx"}',
        "ts": "2026-01-15T08:30:03Z",
        "tool_name": "file.read",
    },
    {
        "id": "00000001-0000-4000-8000-000000000004",
        "session_id": "10000001-0000-4000-8000-000000000001",
        "kind": "asst",
        "content": "I will create the login form and wire it into the auth flow.",
        "ts": "2026-01-15T08:30:06Z",
    },
    {
        "id": "00000001-0000-4000-8000-000000000005",
        "session_id": "10000001-0000-4000-8000-000000000002",
        "kind": "user",
        "content": "Refactor the auth module autonomously.",
        "ts": "2026-01-15T08:15:01Z",
    },
]

_TRIGGERS = [
    {
        "id": "aa000001-0000-4000-8000-000000000001",
        "kind": "cron",
        "persona_name": "eir",
        "spec": "0 * * * *",
        "enabled": True,
        "created_at": "2026-01-01T00:00:00Z",
        "last_fired_at": "2026-01-15T08:24:12Z",
        "fire_count": 336,
    },
    {
        "id": "aa000001-0000-4000-8000-000000000002",
        "kind": "event",
        "persona_name": "sindri",
        "spec": "code.changed",
        "enabled": True,
        "created_at": "2026-01-01T00:00:00Z",
        "last_fired_at": "2026-01-15T08:25:50Z",
        "fire_count": 47,
    },
]

_BUDGETS = {
    "a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c": {"spent_usd": 3.61, "cap_usd": 5.0, "warn_at": 0.7},
    "b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c": {"spent_usd": 2.83, "cap_usd": 4.0, "warn_at": 0.7},
    "c4d5e6f7-1a2b-4c3d-8e9f-0a1b2c3d4e5f": {"spent_usd": 1.42, "cap_usd": 2.0, "warn_at": 0.7},
}

_FLEET_BUDGET = {"spent_usd": 7.86, "cap_usd": 11.0, "warn_at": 0.7}


def list_ravens() -> list[dict[str, object]]:
    return deepcopy(_RAVENS)


def get_raven(ravn_id: str) -> dict[str, object] | None:
    for item in _RAVENS:
        if item["id"] == ravn_id:
            return deepcopy(item)
    return None


def list_sessions() -> list[dict[str, object]]:
    return deepcopy(_SESSIONS)


def get_session(session_id: str) -> dict[str, object] | None:
    for item in _SESSIONS:
        if item["id"] == session_id:
            return deepcopy(item)
    return None


def list_messages(session_id: str) -> list[dict[str, object]]:
    return [deepcopy(item) for item in _MESSAGES if item["session_id"] == session_id]


def list_triggers() -> list[dict[str, object]]:
    return deepcopy(_TRIGGERS)


def create_trigger(
    *,
    kind: str,
    persona_name: str,
    spec: str,
    enabled: bool,
) -> dict[str, object]:
    next_id = f"aa000001-0000-4000-8000-{str(len(_TRIGGERS) + 1).zfill(12)}"
    trigger = {
        "id": next_id,
        "kind": kind,
        "persona_name": persona_name,
        "spec": spec,
        "enabled": enabled,
        "created_at": "2026-04-25T12:00:00Z",
    }
    _TRIGGERS.append(trigger)
    return deepcopy(trigger)


def delete_trigger(trigger_id: str) -> bool:
    for index, item in enumerate(_TRIGGERS):
        if item["id"] == trigger_id:
            _TRIGGERS.pop(index)
            return True
    return False


def get_budget(ravn_id: str) -> dict[str, object] | None:
    budget = _BUDGETS.get(ravn_id)
    if budget is None:
        return None
    return deepcopy(budget)


def get_fleet_budget() -> dict[str, object]:
    return deepcopy(_FLEET_BUDGET)
