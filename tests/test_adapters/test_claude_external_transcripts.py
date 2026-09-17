"""Synthetic native Claude history exercises the public import/replay contract."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from volundr.adapters.outbound.external_sessions.claude_code import ClaudeCodeSessionProvider
from volundr.domain.services.transcript_rebuild import rebuild_turns

NATIVE_ID = "2e877b9f-4b8a-4d46-8f00-03f6163addd5"
FORGE_ID = UUID("10000000-0000-4000-8000-000000000001")
USER_UUID = "20000000-0000-4000-8000-000000000001"
FOREIGN_ID = "30000000-0000-4000-8000-000000000001"


def _record(role: str, content: object, **extra: object) -> dict:
    return {
        "type": role,
        "sessionId": NATIVE_ID,
        "timestamp": "2026-09-08T10:01:00.250Z",
        "message": {"role": role, "content": content},
        **extra,
    }


def _write(tmp_path: Path, records: list[dict]) -> tuple[ClaudeCodeSessionProvider, Path]:
    projects = tmp_path / "projects"
    project = projects / "-synthetic-workspace"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{NATIVE_ID}.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return ClaudeCodeSessionProvider(projects_dir=str(projects)), path


async def test_full_native_transcript_rebuilds_humans_tools_and_completed_reply(tmp_path: Path):
    command = {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "pwd"}}
    final = _record("assistant", [{"type": "text", "text": "The workspace is ready."}])
    final["message"].update(model="claude-synthetic", stop_reason="end_turn")
    provider, _ = _write(
        tmp_path,
        [
            _record("user", "Check this workspace.", uuid=USER_UUID),
            _record("assistant", [{"type": "text", "text": "Checking. "}, command]),
            _record(
                "user",
                [{"type": "tool_result", "tool_use_id": "call-1", "content": "/workspace"}],
            ),
            final,
        ],
    )

    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)

    assert [entry.seq for entry in entries] == [1, 2, 3, 4, 5]
    assert [entry.kind for entry in entries] == ["user", "assistant", "user", "assistant", "result"]
    assert {entry.session_id for entry in entries} == {FORGE_ID}
    assert entries[0].payload["uuid"] == USER_UUID
    assert entries[1].payload["message"]["content"][1] == command
    assert entries[-1].payload["stop_reason"] == "end_turn"
    assert entries[0].ts == datetime(2026, 9, 8, 10, 1, 0, 250000, tzinfo=UTC)
    provenance = entries[0].payload["metadata"]["native_import"]
    assert provenance["provider"] == "claude-code"
    assert provenance["external_id"] == NATIVE_ID
    assert provenance["source_line"] == 1
    assert len(provenance["source_sha256"]) == 64
    assert provenance["partial"] is False
    rebuilt = rebuild_turns(entries)
    assert [turn["role"] for turn in rebuilt.turns] == ["user", "assistant"]
    assert rebuilt.turns[0]["content"] == "Check this workspace."
    assert "The workspace is ready." in rebuilt.turns[1]["content"]
    assert rebuilt.partial is False
    assert {part["type"] for part in rebuilt.turns[1]["parts"]} >= {"tool_use", "tool_result"}


async def test_filters_private_reasoning_context_plugins_and_sidechains(tmp_path: Path):
    records = [
        _record("system", "PRIVATE_SYSTEM"),
        _record("developer", "PRIVATE_DEVELOPER"),
        _record("user", "PRIVATE_META", isMeta=True),
        _record("user", "<environment_context>PRIVATE_ENV</environment_context>"),
        _record("user", "<plugin_instructions>PRIVATE_PLUGIN</plugin_instructions>"),
        _record("user", "<system-reminder>PRIVATE_CONTEXT</system-reminder>"),
        _record("assistant", [{"type": "text", "text": "PRIVATE_CHILD"}], isSidechain=True),
        _record("assistant", "PRIVATE_AGENT", agentId="worker-1", sessionId=FOREIGN_ID),
        _record("user", "Actual human request", uuid=USER_UUID),
        _record(
            "assistant",
            [
                {"type": "thinking", "thinking": "PRIVATE_THINKING"},
                {"type": "redacted_thinking", "data": "PRIVATE_REDACTED"},
                {"type": "image", "source": {"data": "PRIVATE_IMAGE"}},
                {"type": "text", "text": "Public answer", "citations": "PRIVATE_EXTRA"},
            ],
            metadata={"secret": "PRIVATE_METADATA"},
        ),
    ]
    provider, _ = _write(tmp_path, records)

    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)

    serialized = json.dumps([entry.payload for entry in entries])
    assert "PRIVATE_" not in serialized
    assert len(entries) == 2
    assert entries[0].payload["message"]["content"] == "Actual human request"
    assert entries[1].payload["message"]["content"] == [{"type": "text", "text": "Public answer"}]


async def test_assistant_may_discuss_context_wrappers(tmp_path: Path):
    provider, _ = _write(tmp_path, [_record("assistant", "<environment_context> is a wrapper.")])
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert len(entries) == 1
    assert entries[0].payload["message"]["content"][0]["text"].startswith("<environment_context>")


async def test_mirrored_uuid_is_deduplicated_without_losing_repeated_human_text(tmp_path: Path):
    human = _record("user", "Continue", uuid=USER_UUID)
    provider, _ = _write(
        tmp_path,
        [human, dict(human), _record("user", "Continue", uuid=FOREIGN_ID)],
    )
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert [entry.payload["uuid"] for entry in entries] == [USER_UUID, FOREIGN_ID]
    assert len(rebuild_turns(entries).turns) == 2


async def test_conflicting_native_uuid_fails_instead_of_hiding_changed_text(tmp_path: Path):
    provider, _ = _write(
        tmp_path,
        [_record("user", "First", uuid=USER_UUID), _record("user", "Changed", uuid=USER_UUID)],
    )
    with pytest.raises(ValueError, match="Conflicting Claude message UUID"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_mixed_user_blocks_preserve_order_and_distinct_stable_human_ids(tmp_path: Path):
    provider, _ = _write(
        tmp_path,
        [
            _record(
                "user",
                [
                    {"type": "text", "text": "First paragraph"},
                    {"type": "text", "text": "Second paragraph"},
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "done"},
                    {"type": "text", "text": "Follow up"},
                ],
                uuid=USER_UUID,
            )
        ],
    )
    first = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    second = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert first == second
    assert len(first) == 3
    assert first[0].payload["message"]["content"] == "First paragraph\nSecond paragraph"
    assert first[1].payload["message"]["content"][0]["type"] == "tool_result"
    assert first[2].payload["message"]["content"] == "Follow up"
    assert first[0].payload["uuid"] != first[2].payload["uuid"]
    UUID(first[2].payload["uuid"])
    assert [turn["content"] for turn in rebuild_turns(first).turns if turn["role"] == "user"] == [
        "First paragraph\nSecond paragraph",
        "Follow up",
    ]


async def test_missing_message_uuid_still_produces_stable_replay_identity(tmp_path: Path):
    provider, _ = _write(tmp_path, [_record("user", [{"type": "text", "text": "Human"}])])
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    again = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert entries == again
    UUID(entries[0].payload["uuid"])
    assert rebuild_turns(entries).turns[0]["content"] == "Human"


async def test_tool_result_preserves_large_unicode_text_and_explicit_failure(tmp_path: Path):
    output = "Result ✓ " * 3000
    provider, _ = _write(
        tmp_path,
        [
            _record(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": [
                            {"type": "text", "text": output},
                            {"type": "thinking", "thinking": "PRIVATE"},
                            {"type": "image", "source": {"data": "PRIVATE"}},
                        ],
                        "is_error": True,
                    }
                ],
            )
        ],
    )
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    block = entries[0].payload["message"]["content"][0]
    assert block == {
        "type": "tool_result",
        "tool_use_id": "call-1",
        "content": output,
        "is_error": True,
    }


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence", "max_tokens", "refusal"])
async def test_only_native_terminal_reason_closes_assistant_turn(tmp_path: Path, stop_reason: str):
    record = _record("assistant", "Completed")
    record["message"]["stop_reason"] = stop_reason
    provider, _ = _write(tmp_path, [record])
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert [entry.kind for entry in entries] == ["assistant", "result"]
    assert rebuild_turns(entries).partial is False


@pytest.mark.parametrize("stop_reason", [None, "tool_use", "unknown"])
async def test_unknown_or_missing_completion_keeps_partial_turn(
    tmp_path: Path, stop_reason: str | None
):
    record = _record("assistant", "Still working")
    record["message"]["stop_reason"] = stop_reason
    provider, _ = _write(tmp_path, [record])
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert [entry.kind for entry in entries] == ["assistant"]
    assert rebuild_turns(entries).partial is True


async def test_timestamps_keep_source_order_and_normalize_timezone(tmp_path: Path):
    provider, _ = _write(
        tmp_path,
        [
            _record("user", "First", timestamp="2026-09-08T14:00:00+02:00"),
            _record("assistant", "Second", timestamp="2026-09-08T11:59:59Z"),
        ],
    )
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert entries[0].ts.hour == 12
    assert entries[1].ts < entries[0].ts
    assert [entry.kind for entry in entries] == ["user", "assistant"]


@pytest.mark.parametrize("native_id", [None, "not-a-uuid", FOREIGN_ID, 12, ""])
async def test_missing_invalid_or_foreign_record_identity_fails(tmp_path: Path, native_id: object):
    record = _record("user", "Human", sessionId=native_id)
    provider, _ = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="session ID"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_foreign_metadata_identity_also_fails(tmp_path: Path):
    provider, _ = _write(
        tmp_path,
        [{"type": "permission-mode", "sessionId": FOREIGN_ID}, _record("user", "Human")],
    )
    with pytest.raises(ValueError, match="Foreign"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_unidentified_empty_file_fails(tmp_path: Path):
    provider, _ = _write(tmp_path, [])
    with pytest.raises(ValueError, match="no matching native session identity"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_invalid_requested_id_cannot_select_paths(tmp_path: Path):
    provider, _ = _write(tmp_path, [_record("user", "Human")])
    with pytest.raises(ValueError):
        await provider.read_transcript("../../outside", FORGE_ID)


async def test_missing_transcript_fails_clearly(tmp_path: Path):
    provider = ClaudeCodeSessionProvider(projects_dir=str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="was not found"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_duplicate_transcript_paths_are_ambiguous(tmp_path: Path):
    provider, path = _write(tmp_path, [_record("user", "Human")])
    second = path.parent.parent / "-other-workspace"
    second.mkdir()
    (second / path.name).write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="Multiple Claude transcripts"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_symlink_cannot_read_outside_configured_projects(tmp_path: Path):
    provider, path = _write(tmp_path, [_record("user", "Human")])
    outside = tmp_path / "outside.jsonl"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-09-08T10:00:00"])
async def test_invalid_timestamp_is_not_replaced_with_current_time(
    tmp_path: Path, timestamp: object
):
    provider, _ = _write(tmp_path, [_record("user", "Human", timestamp=timestamp)])
    with pytest.raises(ValueError, match="timestamp"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_configured_size_bound_is_enforced(tmp_path: Path):
    _, path = _write(tmp_path, [_record("user", "Human")])
    provider = ClaudeCodeSessionProvider(
        projects_dir=str(path.parent.parent), max_transcript_bytes=10
    )
    with pytest.raises(ValueError, match="max_transcript_bytes"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


def test_nonpositive_size_limit_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        ClaudeCodeSessionProvider(max_transcript_bytes=0)


async def test_unfinished_last_record_is_explicitly_partial(tmp_path: Path):
    provider, path = _write(tmp_path, [_record("user", "Human")])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"assistant","message":')
    entries = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    assert len(entries) == 1
    provenance = entries[0].payload["metadata"]["native_import"]
    assert provenance["partial"] is True
    assert provenance["diagnostics"] == ["ignored_truncated_final_line"]


async def test_interior_corruption_is_not_silently_skipped(tmp_path: Path):
    provider, path = _write(tmp_path, [_record("user", "Human")])
    path.write_text("not-json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


@pytest.mark.parametrize(
    ("role", "block", "message"),
    [
        ("assistant", {"type": "tool_use", "id": "x", "input": {}}, "tool call"),
        ("assistant", {"type": "tool_use", "id": "x", "name": "Bash", "input": "x"}, "tool input"),
        ("user", {"type": "tool_result", "content": "x"}, "tool result"),
        ("user", {"type": "tool_result", "tool_use_id": "x", "content": {}}, "tool output"),
    ],
)
async def test_invalid_public_tool_blocks_fail_clearly(
    tmp_path: Path, role: str, block: dict, message: str
):
    provider, _ = _write(tmp_path, [_record(role, [block])])
    with pytest.raises(ValueError, match=message):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_metadata_only_matching_session_has_no_invented_conversation(tmp_path: Path):
    provider, _ = _write(tmp_path, [{"type": "permission-mode", "sessionId": NATIVE_ID}])
    assert await provider.read_transcript(NATIVE_ID, FORGE_ID) == []
