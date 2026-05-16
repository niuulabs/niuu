from __future__ import annotations

import pytest

from skuld.broker import Broker, ConversationTurn
from skuld.config import SkuldSettings


@pytest.mark.asyncio
async def test_write_workspace_archive_captures_transcript_logs_and_event_streams(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    settings = SkuldSettings(
        session={"id": "test-session", "workspace_dir": str(workspace)},
        chronicle_watcher_enabled=False,
    )
    broker = Broker(settings=settings)
    broker._conversation_turns = [
        ConversationTurn(id="t1", role="user", content="archive this"),
    ]

    (workspace / ".skuld.log").write_text(
        "2026-01-01 12:00:00 skuld INFO archive this\n",
        encoding="utf-8",
    )

    event_dir = home / ".claude" / "projects" / str(workspace).replace("/", "-")
    event_dir.mkdir(parents=True, exist_ok=True)
    (event_dir / "session.jsonl").write_text('{"type":"message"}\n', encoding="utf-8")

    await broker._write_workspace_archive()

    archive_root = workspace / ".volundr" / "archive"
    assert (archive_root / "transcript.json").exists()
    assert (archive_root / "logs" / "aggregate.json").exists()
    assert (archive_root / "logs" / "raw" / "skuld.log").exists()
    assert (archive_root / "events" / "claude-jsonl" / "session.jsonl").exists()
