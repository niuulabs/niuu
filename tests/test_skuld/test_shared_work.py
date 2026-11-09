"""A run member shares its work with the run's room, in the shape the room keeps as turns."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from niuu.mesh.events import MeshEventType
from niuu.mesh.projection import project_mesh_event
from skuld.shared_work import WorkSharer, activity_topic


def _sharer(published: list, *, fail: Exception | None = None) -> WorkSharer:
    async def publish(event, topic):
        if fail is not None:
            raise fail
        published.append((event, topic))

    return WorkSharer(
        publish=publish, peer_id="reviewer-session", persona="reviewer", session_id="s-1"
    )


@pytest.mark.asyncio
async def test_a_tool_call_and_its_result_are_shared_as_the_members_work() -> None:
    published: list = []
    sharer = _sharer(published)

    await sharer.cli_event(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading the file."},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a.txt"}},
                ]
            },
        }
    )
    await sharer.cli_event(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "hello"}],
                    }
                ]
            },
        }
    )

    (start, topic), (result, _) = published
    assert topic == activity_topic("reviewer-session") == "activity.reviewer-session"
    assert start.type == MeshEventType.TOOL_START
    assert start.payload == {"tool_name": "Read", "input": {"path": "a.txt"}, "persona": "reviewer"}
    assert result.type == MeshEventType.TOOL_RESULT
    assert result.payload["tool_name"] == "Read"
    assert result.payload["result"] == "hello"
    assert result.payload["is_error"] is False
    # The room reads them as the member's work: an activity and an agent event each.
    kinds = [event["kind"] for event in project_mesh_event(start)]
    assert "agent_event" in kinds


@pytest.mark.asyncio
async def test_a_reply_is_shared_as_the_members_message() -> None:
    published: list = []
    sharer = _sharer(published)

    await sharer.reply("Review passed.")
    await sharer.reply("   ")

    [(event, _)] = published
    assert event.type == MeshEventType.RESPONSE
    [message] = [e for e in project_mesh_event(event) if e["kind"] == "message"]
    assert message["content"] == "Review passed."


@pytest.mark.asyncio
async def test_a_step_close_is_shared_as_the_members_outcome_with_its_verdict() -> None:
    published: list = []
    sharer = _sharer(published)
    step = SimpleNamespace(node_label="Reviewer (Codex)", node_id="probe-review")

    await sharer.step_closed(
        step,
        {"kind": "verdict", "verdict": "pass", "note": "Mesh answer matched.", "outcome": {}},
    )

    [(event, _)] = published
    assert event.type == MeshEventType.OUTCOME
    [outcome] = [e for e in project_mesh_event(event) if e["kind"] == "outcome"]
    assert outcome["verdict"] == "pass"
    assert outcome["summary"] == "Mesh answer matched."
    assert outcome["fields"]["step"] == "Reviewer (Codex)"


@pytest.mark.asyncio
async def test_a_copy_that_cannot_be_published_is_counted_and_does_not_fail_the_turn(
    monkeypatch,
) -> None:
    degraded: list = []
    monkeypatch.setattr(
        "skuld.shared_work.record_degraded", lambda path, message: degraded.append(path)
    )
    sharer = _sharer([], fail=RuntimeError("mesh down"))

    await sharer.reply("Done.")

    assert degraded == ["skuld.mesh.share_work"]


@pytest.mark.asyncio
async def test_a_presented_file_is_shared_with_this_sessions_id() -> None:
    published: list = []
    sharer = _sharer(published)

    await sharer.presented({"file_id": "pf_1", "name": "review.md", "mime": "text/markdown"})

    [(event, _)] = published
    assert event.type == MeshEventType.TOOL_START
    assert event.payload["tool_name"] == "present_file"
    assert event.payload["input"] == {
        "file_id": "pf_1",
        "name": "review.md",
        "mime": "text/markdown",
        "session_id": "s-1",
    }
