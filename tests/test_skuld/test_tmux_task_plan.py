"""The `plan` frame from Claude Code's task tools (TaskCreate / TaskUpdate / TaskGet / TaskList).

Claude Code no longer offers TodoWrite (seen on valhalla 2026-11-05), so a session that
plans with its task tools sent no `plan` frame and the Forge plan bar stayed empty. The
hook shapes below are Claude Code 2.1.181's: PostToolUse carries the tool's structured
output as `tool_response` — TaskCreate ``{task: {id, subject}}``, TaskUpdate ``{success,
taskId, updatedFields, statusChange?}``, TaskGet ``{task}``, TaskList ``{tasks}``.
"""

from __future__ import annotations

from typing import Any

import pytest

from skuld.transports.tmux_interactive import PLAN_UNAVAILABLE_SUBTYPE
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _collect_events,
)


def _post(tool_name: str, tool_input: dict[str, Any], response: Any, **extra: Any) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "main-native",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": response,
        "tool_use_id": f"toolu_{tool_name}_{id(tool_input)}",
        **extra,
    }


def _create(task_id: str, subject: str, active_form: str | None = None) -> dict:
    tool_input: dict[str, Any] = {"subject": subject, "description": f"do {subject}"}
    if active_form is not None:
        tool_input["activeForm"] = active_form
    return _post("TaskCreate", tool_input, {"task": {"id": task_id, "subject": subject}})


def _status(task_id: str, before: str, after: str) -> dict:
    return _post(
        "TaskUpdate",
        {"taskId": task_id, "status": after},
        {
            "success": True,
            "taskId": task_id,
            "updatedFields": ["status"],
            "statusChange": {"from": before, "to": after},
        },
    )


def _plans(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "plan"]


def _unavailable(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e for e in events if e["type"] == "system" and e.get("subtype") == PLAN_UNAVAILABLE_SUBTYPE
    ]


@pytest.fixture
async def transport(tmp_path):
    t = FakeTmuxInteractiveTransport(str(tmp_path))
    t._claude_native_session_id = "main-native"
    return t


async def test_task_create_emits_the_plan_frame(transport) -> None:
    events = await _collect_events(transport)

    await transport.handle_claude_hook(_create("1", "Read the hooks", "Reading the hooks"))
    await transport.handle_claude_hook(_create("2", "Write tests"))

    plan = _plans(events)[-1]
    assert plan["event_type"] == "claude.plan"
    assert plan["tasks"] == [
        {"content": "Read the hooks", "status": "pending", "activeForm": "Reading the hooks"},
        {"content": "Write tests", "status": "pending"},
    ]
    assert plan["counts"] == {"total": 2, "pending": 2, "in_progress": 0, "completed": 0}
    assert len(_plans(events)) == 2  # one frame per change


async def test_status_updates_move_tasks_through_to_completion(transport) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(_create("1", "Read the hooks"))
    await transport.handle_claude_hook(_create("2", "Write tests"))

    await transport.handle_claude_hook(_status("1", "pending", "in_progress"))
    assert _plans(events)[-1]["counts"] == {
        "total": 2,
        "pending": 1,
        "in_progress": 1,
        "completed": 0,
    }

    await transport.handle_claude_hook(_status("1", "in_progress", "completed"))
    await transport.handle_claude_hook(_status("2", "pending", "in_progress"))
    await transport.handle_claude_hook(_status("2", "in_progress", "completed"))

    plan = _plans(events)[-1]
    assert [t["status"] for t in plan["tasks"]] == ["completed", "completed"]
    assert plan["counts"] == {"total": 2, "pending": 0, "in_progress": 0, "completed": 2}


async def test_subject_and_active_form_updates_and_deletion(transport) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(_create("1", "Read", "Reading"))
    await transport.handle_claude_hook(_create("2", "Obsolete"))

    await transport.handle_claude_hook(
        _post(
            "TaskUpdate",
            {"taskId": "1", "subject": "Read the hooks", "activeForm": ""},
            {"success": True, "taskId": "1", "updatedFields": ["subject", "activeForm"]},
        )
    )
    await transport.handle_claude_hook(
        _post(
            "TaskUpdate",
            {"taskId": "2", "status": "deleted"},
            {
                "success": True,
                "taskId": "2",
                "updatedFields": ["deleted"],
                "statusChange": {"from": "pending", "to": "deleted"},
            },
        )
    )

    assert _plans(events)[-1]["tasks"] == [{"content": "Read the hooks", "status": "pending"}]
    assert _plans(events)[-1]["counts"]["total"] == 1

    await transport.handle_claude_hook(
        _post(
            "TaskUpdate",
            {"taskId": "1", "activeForm": "Reading the hooks"},
            {"success": True, "taskId": "1", "updatedFields": ["activeForm"]},
        )
    )
    assert _plans(events)[-1]["tasks"][0]["activeForm"] == "Reading the hooks"


async def test_updates_outside_the_plan_and_refused_updates_emit_nothing(transport) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(_create("1", "Read"))

    await transport.handle_claude_hook(
        _post(
            "TaskUpdate",
            {"taskId": "1", "owner": "me", "addBlockedBy": ["2"]},
            {"success": True, "taskId": "1", "updatedFields": ["owner", "blockedBy"]},
        )
    )
    # A TaskCompleted hook blocked the completion: Claude reports it and changes nothing.
    await transport.handle_claude_hook(
        _post(
            "TaskUpdate",
            {"taskId": "1", "status": "completed"},
            {"success": False, "taskId": "1", "updatedFields": [], "error": "tests failing"},
        )
    )

    assert len(_plans(events)) == 1
    assert _unavailable(events) == []


async def test_task_list_resyncs_and_keeps_known_active_forms(transport) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(_create("1", "Read", "Reading"))

    await transport.handle_claude_hook(
        _post(
            "TaskList",
            {},
            {
                "tasks": [
                    {"id": "1", "subject": "Read", "status": "in_progress", "blockedBy": []},
                    {"id": "3", "subject": "Ship", "status": "pending", "blockedBy": ["1"]},
                ]
            },
        )
    )

    assert _plans(events)[-1]["tasks"] == [
        {"content": "Read", "status": "in_progress", "activeForm": "Reading"},
        {"content": "Ship", "status": "pending"},
    ]
    # An unchanged list is not a change.
    await transport.handle_claude_hook(
        _post(
            "TaskList",
            {},
            {
                "tasks": [
                    {"id": "1", "subject": "Read", "status": "in_progress", "blockedBy": []},
                    {"id": "3", "subject": "Ship", "status": "pending", "blockedBy": ["1"]},
                ]
            },
        )
    )
    assert len(_plans(events)) == 2


async def test_task_get_adds_a_task_this_session_had_not_seen(transport) -> None:
    events = await _collect_events(transport)

    await transport.handle_claude_hook(
        _post(
            "TaskGet",
            {"taskId": "4"},
            {
                "task": {
                    "id": "4",
                    "subject": "Resume work",
                    "description": "carry on",
                    "status": "in_progress",
                    "blocks": [],
                    "blockedBy": [],
                }
            },
        )
    )
    await transport.handle_claude_hook(_post("TaskGet", {"taskId": "9"}, {"task": None}))

    assert _plans(events)[-1]["tasks"] == [{"content": "Resume work", "status": "in_progress"}]
    assert len(_plans(events)) == 1
    assert _unavailable(events) == []


async def test_a_repeated_task_id_starts_a_new_list(transport) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(_create("1", "Old work"))
    await transport.handle_claude_hook(_create("2", "More old work"))

    await transport.handle_claude_hook(_create("1", "New conversation"))

    assert _plans(events)[-1]["tasks"] == [{"content": "New conversation", "status": "pending"}]


async def test_another_claude_process_does_not_touch_the_main_plan(transport) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(_create("1", "Main work"))

    await transport.handle_claude_hook({**_create("1", "Teammate work"), "session_id": "mate"})
    # An in-process subagent shares the main list.
    await transport.handle_claude_hook({**_create("2", "Subagent work"), "agent_id": "sub-1"})

    assert [t["content"] for t in _plans(events)[-1]["tasks"]] == ["Main work", "Subagent work"]


async def test_a_failed_task_tool_changes_nothing(transport) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(
        {**_create("1", "Read"), "hook_event_name": "PostToolUseFailure", "error": "boom"}
    )

    assert _plans(events) == []
    assert _unavailable(events) == []


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "response", "reason"),
    [
        ("TaskCreate", {"subject": "x"}, "Task #1 created successfully: x", "not an object"),
        ("TaskCreate", {"subject": "x"}, {"task": {"subject": "x"}}, "'id'"),
        (
            "TaskCreate",
            {"subject": "x", "activeForm": 3},
            {"task": {"id": "1", "subject": "x"}},
            "activeForm",
        ),
        ("TaskUpdate", {"taskId": "1"}, {"taskId": "1", "updatedFields": ["status"]}, "'success'"),
        (
            "TaskUpdate",
            {"taskId": "1", "status": "completed"},
            {"success": True, "taskId": "1", "updatedFields": ["status"]},
            "'statusChange'",
        ),
        (
            "TaskUpdate",
            {"taskId": "1", "status": "blocked"},
            {
                "success": True,
                "taskId": "1",
                "updatedFields": ["status"],
                "statusChange": {"from": "pending", "to": "blocked"},
            },
            "'blocked'",
        ),
        (
            "TaskUpdate",
            {"taskId": "7", "status": "completed"},
            {
                "success": True,
                "taskId": "7",
                "updatedFields": ["status"],
                "statusChange": {"from": "pending", "to": "completed"},
            },
            "never saw created",
        ),
        (
            "TaskUpdate",
            {"taskId": "1", "status": "completed"},
            {"success": True, "taskId": "1", "updatedFields": [1]},
            "not strings",
        ),
        (
            "TaskUpdate",
            {"taskId": "1", "activeForm": None},
            {"success": True, "taskId": "1", "updatedFields": ["activeForm"]},
            "activeForm",
        ),
        ("TaskList", {}, {"tasks": [{"id": "1", "status": "pending"}]}, "'subject'"),
        ("TaskList", {}, {"tasks": ["#1 [pending] Read"]}, "TaskList task is str"),
        ("TaskGet", {"taskId": "1"}, {}, "no task"),
    ],
)
async def test_an_unreadable_task_payload_is_loud_and_keeps_the_plan(
    transport, caplog, tool_name, tool_input, response, reason
) -> None:
    events = await _collect_events(transport)
    await transport.handle_claude_hook(_create("1", "Read"))

    with caplog.at_level("ERROR", logger="skuld.transport"):
        await transport.handle_claude_hook(_post(tool_name, tool_input, response))

    [frame] = _unavailable(events)
    assert frame["tool_name"] == tool_name
    assert reason in frame["reason"]
    assert any("could not update the session plan" in r.message for r in caplog.records)
    assert len(_plans(events)) == 1
    assert transport._claude_tasks == {"1": {"content": "Read", "status": "pending"}}
    # The tool result itself still reaches the client.
    assert any(
        e["type"] == "user" and e["message"]["content"][0]["type"] == "tool_result"
        for e in events
        if e.get("metadata", {}).get("hook_event_name") == "PostToolUse"
    )
