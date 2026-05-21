from __future__ import annotations

import json

import pytest

from volundr.session_archive import (
    archive_manifest_path,
    archive_transcript_markdown_path,
    load_archive_logs,
    load_archive_manifest,
    load_archive_transcript,
    load_workspace_transcript,
    render_transcript_markdown,
    write_session_archive,
)


def test_load_workspace_transcript_missing_returns_empty(tmp_path):
    payload = load_workspace_transcript(tmp_path, "sess-1")
    assert payload == {"turns": [], "is_active": False, "last_activity": ""}


def test_load_workspace_transcript_non_list_turns_returns_empty(tmp_path):
    transcript = tmp_path / ".skuld" / "conversation_sess-1.json"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"turns": "invalid"}), encoding="utf-8")

    payload = load_workspace_transcript(tmp_path, "sess-1")

    assert payload["turns"] == []


def test_render_transcript_markdown_handles_empty_payload():
    rendered = render_transcript_markdown({"turns": []})
    assert "# Session Transcript" in rendered
    assert "No conversation turns recorded" in rendered


def test_render_transcript_markdown_handles_empty_content_and_timestamp():
    rendered = render_transcript_markdown(
        {
            "turns": [
                {
                    "role": "assistant",
                    "content": "",
                    "created_at": "2026-01-01T12:00:00+00:00",
                }
            ]
        }
    )

    assert "## Assistant (2026-01-01T12:00:00+00:00)" in rendered
    assert "_(empty)_" in rendered


def test_write_session_archive_writes_normalized_artifacts(tmp_path):
    session_id = "sess-1"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".skuld.log").write_text(
        "2026-01-01 12:00:00 skuld INFO hello\n",
        encoding="utf-8",
    )
    (workspace / ".flock" / "logs").mkdir(parents=True, exist_ok=True)
    (workspace / ".flock" / "logs" / "worker.log").write_text(
        "2026-01-01 12:00:01 ravn INFO worker hello\n",
        encoding="utf-8",
    )
    (workspace / ".services" / "logs").mkdir(parents=True, exist_ok=True)
    (workspace / ".services" / "logs" / "api.log").write_text(
        "2026-01-01 12:00:02 service INFO api hello\n",
        encoding="utf-8",
    )

    event_dir = tmp_path / "events"
    event_dir.mkdir()
    (event_dir / "session.jsonl").write_text('{"type":"message"}\n', encoding="utf-8")

    transcript = {
        "turns": [
            {
                "id": "turn-1",
                "role": "user",
                "content": "hello",
                "created_at": "2026-01-01T12:00:00+00:00",
            }
        ],
        "is_active": False,
        "last_activity": "",
    }
    logs = {
        "total": 1,
        "filtered": 1,
        "returned": 1,
        "available_participants": [{"id": "skuld", "label": "Skuld", "kind": "broker"}],
        "lines": [{"message": "hello"}],
    }

    manifest = write_session_archive(
        session_id=session_id,
        workspace_dir=workspace,
        transcript_payload=transcript,
        aggregated_logs=logs,
        chronicle_payload={"summary": "done"},
        timeline_payload={"events": [{"label": "hello"}]},
        event_source_dir=event_dir,
    )

    assert manifest["session_id"] == session_id
    assert manifest["counts"]["turns"] == 1
    assert manifest["counts"]["raw_logs"] == 3
    assert manifest["counts"]["raw_event_streams"] == 1

    stored_manifest = json.loads(archive_manifest_path(workspace).read_text(encoding="utf-8"))
    assert stored_manifest["artifacts"]["transcript_json"] == "transcript.json"
    assert stored_manifest["artifacts"]["chronicle"] == "chronicle.json"
    assert "logs/raw/flock/worker.log" in stored_manifest["sources"]["workspace_logs"]
    assert "logs/raw/services/api.log" in stored_manifest["sources"]["workspace_logs"]

    markdown = archive_transcript_markdown_path(workspace).read_text(encoding="utf-8")
    assert "## User" in markdown
    assert "hello" in markdown


def test_write_session_archive_without_optional_sources(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    manifest = write_session_archive(
        session_id="sess-2",
        workspace_dir=workspace,
        transcript_payload={"turns": []},
        aggregated_logs={"lines": []},
        event_source_dir=workspace / "missing-events",
    )

    assert manifest["artifacts"]["chronicle"] is None
    assert manifest["sources"]["workspace_transcript"] is None
    assert manifest["sources"]["event_streams"] == []


def test_load_archive_manifest_returns_none_when_missing(tmp_path):
    assert load_archive_manifest(tmp_path) is None


def test_load_archive_payloads_can_use_config_root_without_workspace_dir(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    niuu_home = tmp_path / ".niuu"
    monkeypatch.setenv("NIUU_HOME", str(niuu_home))

    transcript = {"turns": [{"role": "assistant", "content": "archived"}]}
    logs = {"lines": [{"message": "archived log"}]}

    write_session_archive(
        session_id="sess-config",
        workspace_dir=workspace,
        transcript_payload=transcript,
        aggregated_logs=logs,
        archive_location="config",
        archive_path="archives-store",
    )

    loaded_transcript = load_archive_transcript(
        None,
        session_id="sess-config",
        archive_location="config",
        archive_path="archives-store",
    )
    loaded_logs = load_archive_logs(
        None,
        session_id="sess-config",
        archive_location="config",
        archive_path="archives-store",
    )
    loaded_manifest = load_archive_manifest(
        None,
        session_id="sess-config",
        archive_location="config",
        archive_path="archives-store",
    )

    assert loaded_transcript is not None
    assert loaded_transcript["turns"][0]["content"] == "archived"
    assert loaded_logs is not None
    assert loaded_logs["lines"][0]["message"] == "archived log"
    assert loaded_manifest is not None
    assert loaded_manifest["session_id"] == "sess-config"


def test_write_session_archive_can_target_config_root(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    niuu_home = tmp_path / ".niuu"
    monkeypatch.setenv("NIUU_HOME", str(niuu_home))

    manifest = write_session_archive(
        session_id="sess-config",
        workspace_dir=workspace,
        transcript_payload={"turns": []},
        aggregated_logs={"lines": []},
        archive_location="config",
        archive_path="archives-store",
    )

    expected_root = niuu_home / "archives-store" / "sess-config"
    assert manifest["location"] == "config"
    assert manifest["archive_root"] == str(expected_root)
    assert (expected_root / "manifest.json").exists()
    loaded = load_archive_manifest(
        workspace,
        session_id="sess-config",
        archive_location="config",
        archive_path="archives-store",
    )
    assert loaded is not None
    assert loaded["session_id"] == "sess-config"


def test_load_workspace_transcript_rejects_non_object_json(tmp_path):
    transcript = tmp_path / ".skuld" / "conversation_sess-3.json"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text('["bad"]', encoding="utf-8")

    with pytest.raises(ValueError, match="Expected JSON object"):
        load_workspace_transcript(tmp_path, "sess-3")
