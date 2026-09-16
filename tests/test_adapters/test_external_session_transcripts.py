"""Synthetic native Codex transcripts project safely and deterministically into replay."""

import json
from uuid import UUID

import pytest

from niuu.domain.transcript_reducer import reduce_frames
from volundr.adapters.outbound.external_sessions.codex import CodexSessionProvider

NATIVE_ID = "019e88ee-074d-72a2-a81b-3fabef982d78"
FORGE_ID = UUID("2e877b9f-4b8a-4d46-8f00-03f6163addd5")
STAMP = "2026-09-08T07:40:00Z"


def record(kind, payload, timestamp=STAMP):
    return {"type": kind, "timestamp": timestamp, "payload": payload}


def completed(item):
    return record("event_msg", {"type": "item_completed", "item": item})


def write_rollout(tmp_path, records, *, native_id=NATIVE_ID):
    root = tmp_path / "sessions"
    root.mkdir(exist_ok=True)
    path = root / f"rollout-2026-09-08T07-40-00-{native_id}.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in [record("session_meta", {"id": native_id}), *records])
        + "\n"
    )
    return root, path


def message(role, text, *, identifier=None):
    return record(
        "response_item",
        {
            "type": "message",
            "role": role,
            "id": identifier,
            "content": [{"type": "input_text", "text": text}],
        },
    )


async def test_modern_transcript_round_trips_humans_text_and_paired_tools(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [
            completed(
                {
                    "type": "UserMessage",
                    "id": str(FORGE_ID),
                    "content": [{"type": "text", "text": "Inspect the synthetic fixture"}],
                }
            ),
            completed(
                {
                    "type": "AgentMessage",
                    "id": "reply-1",
                    "content": [{"type": "Text", "text": "Checking it."}],
                }
            ),
            completed(
                {
                    "type": "CommandExecution",
                    "id": "cmd-1",
                    "command": ["cat", "fixture"],
                    "cwd": "/fixture",
                    "aggregated_output": "hello\n",
                    "exit_code": 0,
                    "status": "completed",
                }
            ),
            completed(
                {
                    "type": "FileChange",
                    "id": "edit-1",
                    "changes": {"fixture": "patch"},
                    "stdout": "updated",
                    "status": "completed",
                }
            ),
            completed(
                {
                    "type": "AgentMessage",
                    "id": "reply-2",
                    "content": [{"type": "Text", "text": "Done."}],
                }
            ),
            record(
                "event_msg",
                {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "Done."},
            ),
        ],
    )
    provider = CodexSessionProvider(sessions_dir=str(root))
    rows = await provider.read_transcript(NATIVE_ID, FORGE_ID)

    assert [row.seq for row in rows] == list(range(1, 9))
    assert all(row.session_id == FORGE_ID for row in rows)
    assert rows[0].payload["uuid"] == str(FORGE_ID)
    assert rows[0].ts.isoformat() == "2026-09-08T07:40:00+00:00"
    assert all(row.payload["metadata"]["native_import"]["external_id"] == NATIVE_ID for row in rows)
    calls = [
        b
        for row in rows
        for b in row.payload.get("message", {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    results = [
        b
        for row in rows
        for b in row.payload.get("message", {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert [b["id"] for b in calls] == [b["tool_use_id"] for b in results] == ["cmd-1", "edit-1"]
    assert results[0]["content"] == "hello\n"
    assert rows[-1].payload["result"] == ""
    turns = reduce_frames(rows).turns
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "Inspect the synthetic fixture"
    assert "Checking it." in turns[1]["content"] and "Done." in turns[1]["content"]
    assert rows == await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_public_native_text_identity_phase_and_turn_survive_import(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [
            record("event_msg", {"type": "task_started", "turn_id": "turn-native"}),
            completed(
                {
                    "type": "AgentMessage",
                    "id": "commentary-a",
                    "phase": "commentary",
                    "content": [{"type": "Text", "text": "Checking café 東京."}],
                }
            ),
            completed(
                {
                    "type": "AgentMessage",
                    "id": "final-a",
                    "phase": "final_answer",
                    "content": [{"type": "Text", "text": "Done."}],
                }
            ),
            record(
                "event_msg",
                {"type": "task_complete", "turn_id": "turn-native", "last_agent_message": "Done."},
            ),
        ],
    )
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)
    blocks = [row.payload["message"]["content"][0] for row in rows if row.kind == "assistant"]
    assert [(block["id"], block["phase"]) for block in blocks] == [
        ("commentary-a", "commentary"),
        ("final-a", "final_answer"),
    ]
    assert all(
        block["turn_id"] == "turn-native"
        and block["thread_id"] == NATIVE_ID
        and block["complete"] is True
        and block["id_source"] == "native"
        for block in blocks
    )
    parts = reduce_frames(rows).turns[0]["parts"]
    assert [part["phase"] for part in parts] == ["commentary", "final_answer"]
    assert [part["text"] for part in parts] == ["Checking café 東京.", "Done."]


async def test_import_without_native_text_id_has_stable_explicit_synthetic_identity(tmp_path):
    public = message("assistant", "A public answer.")
    public["payload"]["channel"] = "final_answer"
    root, _ = write_rollout(tmp_path, [public])
    provider = CodexSessionProvider(sessions_dir=str(root))
    rows = await provider.read_transcript(NATIVE_ID, FORGE_ID)
    block = rows[0].payload["message"]["content"][0]
    assert block["id"] and block["id_source"] == "synthetic"
    assert block["phase"] == "final_answer"
    assert rows[0].payload["metadata"]["native_import"]["native_id"] is None
    assert rows == await provider.read_transcript(NATIVE_ID, FORGE_ID)


async def test_distinct_native_items_with_identical_text_remain_distinct(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [
            completed(
                {
                    "type": "AgentMessage",
                    "id": identifier,
                    "phase": "commentary",
                    "content": [{"type": "Text", "text": "Checking."}],
                }
            )
            for identifier in ("first", "second")
        ],
    )
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)
    parts = reduce_frames(rows).turns[0]["parts"]
    assert [(part["id"], part["text"]) for part in parts] == [
        ("first", "Checking."),
        ("second", "Checking."),
    ]


async def test_task_complete_only_final_survives_prior_commentary(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [
            record("event_msg", {"type": "task_started", "turn_id": "turn-native"}),
            completed(
                {
                    "type": "AgentMessage",
                    "id": "progress",
                    "phase": "commentary",
                    "content": [{"type": "Text", "text": "Checking."}],
                }
            ),
            record(
                "event_msg",
                {
                    "type": "task_complete",
                    "turn_id": "turn-native",
                    "last_agent_message": "Final answer.",
                },
            ),
        ],
    )
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)
    parts = reduce_frames(rows).turns[0]["parts"]
    assert [part["text"] for part in parts] == ["Checking.", "Final answer."]
    assert parts[-1]["phase"] == "final_answer"
    assert parts[-1]["complete"] is True and parts[-1]["id_source"] == "synthetic"
    assert rows[-2].payload["metadata"]["native_import"]["native_id"] is None


async def test_mirrored_formats_deduplicate_but_repeated_human_requests_survive(tmp_path):
    records = []
    for number in (1, 2):
        records.extend(
            [
                record("event_msg", {"type": "task_started", "turn_id": f"turn-{number}"}),
                message("user", "continue", identifier=f"raw-user-{number}"),
                record("event_msg", {"type": "user_message", "message": "continue"}),
                completed(
                    {
                        "type": "UserMessage",
                        "id": f"user-{number}",
                        "content": [{"type": "text", "text": "continue"}],
                    }
                ),
                completed(
                    {
                        "type": "AgentMessage",
                        "id": f"reply-{number}",
                        "content": [{"type": "Text", "text": "Okay."}],
                    }
                ),
                message("assistant", "Okay.", identifier=f"reply-{number}"),
                record("event_msg", {"type": "agent_message", "message": "Okay."}),
                record(
                    "event_msg",
                    {
                        "type": "task_complete",
                        "turn_id": f"turn-{number}",
                        "last_agent_message": "Okay.",
                    },
                ),
            ]
        )
    root, _ = write_rollout(tmp_path, records)
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)

    assert [row.kind for row in rows] == ["user", "assistant", "result"] * 2
    assert rows[0].payload["uuid"] != rows[3].payload["uuid"]
    assert [t["content"] for t in reduce_frames(rows).turns] == ["continue", "Okay."] * 2


async def test_legacy_functions_and_modern_execution_share_one_pair(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [
            message("user", "Run the fixture"),
            record(
                "response_item",
                {
                    "type": "function_call",
                    "call_id": "cmd-1",
                    "name": "Bash",
                    "arguments": json.dumps({"command": "false"}),
                },
            ),
            completed(
                {
                    "type": "CommandExecution",
                    "id": "cmd-1",
                    "command": "false",
                    "stdout": "failed",
                    "stderr": " detail",
                    "exit_code": 1,
                }
            ),
            record(
                "response_item",
                {"type": "function_call_output", "call_id": "cmd-1", "output": "failed detail"},
            ),
            record("event_msg", {"type": "task_complete", "last_agent_message": "Command failed"}),
        ],
    )
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)

    assert [row.kind for row in rows] == ["user", "assistant", "user", "assistant", "result"]
    assert rows[2].payload["message"]["content"][0]["is_error"]
    assert rows[-1].payload["result"] == "Command failed"


async def test_private_context_reasoning_and_encrypted_metadata_never_reach_replay(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [
            message("system", "SYSTEM_SECRET"),
            message("developer", "DEVELOPER_SECRET"),
            message("user", "<recommended_plugins>PLUGIN_SECRET</recommended_plugins>"),
            message("user", "<environment_context>ENV_SECRET</environment_context>"),
            message("user", "# AGENTS.md instructions for /private\nINSTRUCTIONS_SECRET"),
            message("user", "Actual human request"),
            record("response_item", {"type": "reasoning", "encrypted_content": "ENCRYPTED_SECRET"}),
            completed({"type": "Reasoning", "raw_content": ["THOUGHT_SECRET"]}),
            record(
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "analysis",
                    "content": [{"type": "output_text", "text": "ANALYSIS_SECRET"}],
                },
            ),
            record(
                "response_item",
                {
                    "type": "custom_tool_call",
                    "call_id": "call-1",
                    "name": "exec",
                    "input": json.dumps(
                        {"command": "echo public", "encrypted_content": "TOOL_SECRET"}
                    ),
                },
            ),
            record(
                "response_item",
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call-1",
                    "output": {"text": "public output", "reasoning": "OUTPUT_SECRET"},
                },
            ),
            message("assistant", "Public answer"),
        ],
    )
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)
    rendered = json.dumps([row.payload for row in rows])

    assert "SECRET" not in rendered
    assert "Actual human request" in rendered
    assert "Public answer" in rendered
    assert "public output" in rendered
    assert (
        len([r for r in rows if isinstance(r.payload.get("message", {}).get("content"), str)]) == 1
    )


@pytest.mark.parametrize("native_id", ["bad-id", "../escape"])
async def test_invalid_native_identity_fails(tmp_path, native_id):
    with pytest.raises(ValueError, match="identifier"):
        await CodexSessionProvider(sessions_dir=str(tmp_path)).read_transcript(native_id, FORGE_ID)


async def test_missing_and_foreign_transcripts_fail(tmp_path):
    provider = CodexSessionProvider(sessions_dir=str(tmp_path / "sessions"))
    with pytest.raises(FileNotFoundError):
        await provider.read_transcript(NATIVE_ID, FORGE_ID)
    root, path = write_rollout(tmp_path, [message("user", "hello")])
    path.write_text(path.read_text().replace(NATIVE_ID, str(FORGE_ID)))
    with pytest.raises(ValueError, match="identity"):
        await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)


async def test_transcript_size_limit_and_symlink_boundary(tmp_path):
    root, path = write_rollout(tmp_path, [message("user", "hello")])
    with pytest.raises(ValueError, match="max_transcript_bytes"):
        await CodexSessionProvider(sessions_dir=str(root), max_transcript_bytes=10).read_transcript(
            NATIVE_ID, FORGE_ID
        )
    outside = tmp_path / "outside.jsonl"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)


async def test_truncated_final_record_is_explicitly_partial_but_corrupt_middle_fails(tmp_path):
    root, path = write_rollout(tmp_path, [message("user", "hello")])
    original = path.read_text()
    path.write_text(original + '{"type":')
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)
    provenance = rows[0].payload["metadata"]["native_import"]
    assert provenance["partial"]
    assert provenance["diagnostics"] == ["ignored_truncated_final_line"]
    path.write_text(original + '{"type":\n{}\n')
    with pytest.raises(ValueError, match="JSON at line"):
        await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)


async def test_invalid_timestamp_fails_without_inventing_time(tmp_path):
    value = message("user", "hello")
    value["timestamp"] = "not-a-timestamp"
    root, _ = write_rollout(tmp_path, [value])
    with pytest.raises(ValueError, match="timestamp"):
        await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)


async def test_duplicate_native_message_id_with_conflicting_content_fails(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [message("user", "first", identifier="same"), message("user", "second", identifier="same")],
    )
    with pytest.raises(ValueError, match="Conflicting"):
        await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)


async def test_async_question_agent_item_is_control_context_not_final_prose(tmp_path):
    root, _ = write_rollout(
        tmp_path,
        [
            record("turn_context", {"turn_id": "parent-turn"}),
            message("assistant", "Checking.", identifier="commentary-a"),
            record(
                "response_item",
                {
                    "type": "function_call",
                    "call_id": "question-call",
                    "name": "request_user_input_async",
                    "arguments": '{"questions":[{"title":"Label?"}]}',
                },
            ),
            completed(
                {
                    "type": "AgentMessage",
                    "id": "question-call",
                    "phase": "final_answer",
                    "delivery": "async",
                    "questions": [{"title": "Label?", "options": None}],
                    "content": [{"type": "Text", "text": "CONTROL CARD CONTENT"}],
                }
            ),
            record(
                "response_item",
                {
                    "type": "function_call_output",
                    "call_id": "question-call",
                    "output": "Blue",
                },
            ),
            message("assistant", "Continuing.", identifier="commentary-b"),
            record(
                "event_msg",
                {
                    "type": "task_complete",
                    "turn_id": "parent-turn",
                    "last_agent_message": "Continuing.",
                },
            ),
        ],
    )
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)
    parts = reduce_frames(rows).turns[0]["parts"]
    assert [p["text"] for p in parts if p["type"] == "text"] == ["Checking.", "Continuing."]
    assert [p["id"] for p in parts if p["type"] == "tool_use"] == ["question-call"]
    assert [p["content"] for p in parts if p["type"] == "tool_result"] == ["Blue"]


async def test_foreign_native_thread_items_do_not_enter_or_finish_parent_turn(tmp_path):
    worker = completed(
        {
            "type": "AgentMessage",
            "id": "worker-reply",
            "phase": "final_answer",
            "content": [{"type": "Text", "text": "WORKER"}],
        }
    )
    worker["payload"].update(thread_id="worker-thread", turn_id="worker-turn")
    root, _ = write_rollout(
        tmp_path,
        [
            record("turn_context", {"turn_id": "parent-turn"}),
            message("assistant", "Parent.", identifier="parent-reply"),
            worker,
            record(
                "event_msg",
                {
                    "type": "task_complete",
                    "thread_id": "worker-thread",
                    "turn_id": "worker-turn",
                    "last_agent_message": "WORKER",
                },
            ),
            message("assistant", "Parent final.", identifier="parent-final"),
            record(
                "event_msg",
                {
                    "type": "task_complete",
                    "thread_id": NATIVE_ID,
                    "turn_id": "parent-turn",
                    "last_agent_message": "Parent final.",
                },
            ),
        ],
    )
    rows = await CodexSessionProvider(sessions_dir=str(root)).read_transcript(NATIVE_ID, FORGE_ID)
    turns = reduce_frames(rows).turns
    assert len(turns) == 1
    assert [p["text"] for p in turns[0]["parts"] if p["type"] == "text"] == [
        "Parent.",
        "Parent final.",
    ]
