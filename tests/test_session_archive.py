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


def test_workspace_archive_not_served_to_a_different_session(tmp_path):
    """The ghost-replay bug: workspace archives are SHARED per workspace
    (archive_root ignores session_id for location="workspace"), so the loader
    must verify manifest ownership — a NEW session in a workspace with prior
    history must NOT inherit the previous session's transcript/logs."""
    import json as _json

    from volundr.session_archive import (
        archive_logs_aggregate_path,
        archive_manifest_path,
        archive_transcript_json_path,
        load_archive_logs,
        load_archive_transcript,
    )

    owner = "11111111-1111-1111-1111-111111111111"
    stranger = "22222222-2222-2222-2222-222222222222"

    tpath = archive_transcript_json_path(tmp_path, session_id=owner)
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(_json.dumps({"turns": [{"role": "user", "content": "old stuff"}]}))
    lpath = archive_logs_aggregate_path(tmp_path, session_id=owner)
    lpath.parent.mkdir(parents=True, exist_ok=True)
    lpath.write_text(_json.dumps({"lines": ["old log"]}))
    mpath = archive_manifest_path(tmp_path, session_id=owner)
    mpath.write_text(_json.dumps({"version": 1, "session_id": owner}))

    # The owner still reads its archive.
    assert load_archive_transcript(tmp_path, session_id=owner) is not None
    assert load_archive_logs(tmp_path, session_id=owner) is not None

    # A different session in the SAME workspace gets nothing.
    assert load_archive_transcript(tmp_path, session_id=stranger) is None
    assert load_archive_logs(tmp_path, session_id=stranger) is None

    # Legacy archive without a manifest stays readable (no regression).
    mpath.unlink()
    assert load_archive_transcript(tmp_path, session_id=stranger) is not None


def test_resolve_contained_path_accepts_only_descendants(tmp_path):
    from volundr.session_archive import resolve_contained_path

    root = tmp_path / "root"
    root.mkdir()

    assert resolve_contained_path(root, "nested/file.json") == root / "nested" / "file.json"
    for candidate in ("../escape", "nested/../file.json", tmp_path / "root-prefix" / "file"):
        with pytest.raises(ValueError, match="Path (contains traversal|escapes configured root)"):
            resolve_contained_path(root, candidate)


def test_resolve_contained_path_rejects_symlink_escape(tmp_path):
    from volundr.session_archive import resolve_contained_path

    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes configured root"):
        resolve_contained_path(root, "link/artifact.json")


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape.json",
        "nested/../escape.json",
        "/absolute.json",
        r"..\escape.json",
        r"C:\escape.json",
        r"\\server\share\escape.json",
    ],
)
def test_resolve_archive_member_path_rejects_archive_slip(tmp_path, member_name):
    from volundr.session_archive import resolve_archive_member_path

    root = tmp_path / "extract"
    root.mkdir()

    with pytest.raises(ValueError, match="Archive member path"):
        resolve_archive_member_path(root, member_name)


def test_resolve_archive_member_path_accepts_nested_member(tmp_path):
    from volundr.session_archive import resolve_archive_member_path

    root = tmp_path / "extract"
    root.mkdir()

    assert resolve_archive_member_path(
        root, "logs/nested.json"
    ) == root / "logs" / "nested.json"


def test_archive_root_rejects_workspace_path_traversal(tmp_path):
    from volundr.session_archive import archive_root

    for archive_path in ("../archive", tmp_path / "absolute-archive"):
        with pytest.raises(ValueError):
            archive_root(tmp_path / "workspace", archive_path=archive_path)


def test_archive_root_rejects_config_session_traversal(tmp_path, monkeypatch):
    from volundr.session_archive import archive_root

    monkeypatch.setenv("NIUU_HOME", str(tmp_path / ".niuu"))

    with pytest.raises(ValueError, match="traversal"):
        archive_root(
            None,
            session_id="../escaped-session",
            archive_location="config",
        )


def test_load_workspace_transcript_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    transcript_dir = workspace / ".skuld"
    outside = tmp_path / "outside.json"
    transcript_dir.mkdir(parents=True)
    outside.write_text(json.dumps({"turns": [{"content": "secret"}]}), encoding="utf-8")
    (transcript_dir / "conversation_sess-link.json").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes configured root"):
        load_workspace_transcript(workspace, "sess-link")


def test_write_session_archive_rejects_archive_root_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    (workspace / ".volundr").mkdir(parents=True)
    outside.mkdir()
    (workspace / ".volundr" / "archive").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes configured root"):
        write_session_archive(
            session_id="sess-link",
            workspace_dir=workspace,
            transcript_payload={"turns": []},
            aggregated_logs={"lines": []},
        )

    assert list(outside.iterdir()) == []


def test_write_session_archive_rejects_workspace_log_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    outside_log = tmp_path / "outside.log"
    workspace.mkdir()
    outside_log.write_text("secret", encoding="utf-8")
    (workspace / ".skuld.log").symlink_to(outside_log)

    with pytest.raises(ValueError, match="escapes configured root"):
        write_session_archive(
            session_id="sess-log-link",
            workspace_dir=workspace,
            transcript_payload={"turns": []},
            aggregated_logs={"lines": []},
        )


def test_write_session_archive_rejects_event_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    events = tmp_path / "events"
    outside_event = tmp_path / "outside.jsonl"
    workspace.mkdir()
    events.mkdir()
    outside_event.write_text("{}\n", encoding="utf-8")
    (events / "escaped.jsonl").symlink_to(outside_event)

    with pytest.raises(ValueError, match="escapes configured root"):
        write_session_archive(
            session_id="sess-event-link",
            workspace_dir=workspace,
            transcript_payload={"turns": []},
            aggregated_logs={"lines": []},
            event_source_dir=events,
        )
