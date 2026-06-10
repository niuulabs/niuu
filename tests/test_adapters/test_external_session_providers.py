"""Tests for the Claude Code and Codex external session providers."""

import json
from pathlib import Path

from volundr.adapters.outbound.external_sessions import (
    ClaudeCodeSessionProvider,
    CodexSessionProvider,
)

CLAUDE_SESSION_ID = "2e877b9f-4b8a-4d46-8f00-03f6163addd5"
CODEX_THREAD_ID = "019e88ee-074d-72a2-a81b-3fabef982d78"


def _write_claude_session(
    projects_dir: Path,
    session_id: str,
    workspace: Path,
    *,
    title: str = "Fix the login bug please",
) -> Path:
    project_dir = projects_dir / str(workspace).replace("/", "-")
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    lines = [
        {"type": "permission-mode", "permissionMode": "default", "sessionId": session_id},
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": str(workspace),
            "timestamp": "2026-06-01T10:00:00.000Z",
            "message": {"role": "user", "content": title},
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "cwd": str(workspace),
            "timestamp": "2026-06-01T10:00:05.000Z",
            "message": {"role": "assistant", "model": "claude-sonnet-4-6", "content": []},
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def _write_codex_session(
    sessions_dir: Path,
    thread_id: str,
    workspace: Path,
    *,
    title: str = "Refactor the billing module",
) -> Path:
    day_dir = sessions_dir / "2026" / "06" / "01"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-2026-06-01T10-00-00-{thread_id}.jsonl"
    lines = [
        {
            "timestamp": "2026-06-01T10:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": thread_id,
                "timestamp": "2026-06-01T10:00:00.000Z",
                "cwd": str(workspace),
                "model_provider": "openai",
            },
        },
        {
            "timestamp": "2026-06-01T10:00:01.000Z",
            "type": "turn_context",
            "payload": {"turn_id": "turn-1", "cwd": str(workspace), "model": "gpt-5-codex"},
        },
        {
            "timestamp": "2026-06-01T10:00:02.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": title},
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


class TestClaudeCodeSessionProvider:
    async def test_lists_sessions_with_metadata(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        projects_dir = tmp_path / "projects"
        _write_claude_session(projects_dir, CLAUDE_SESSION_ID, workspace)

        provider = ClaudeCodeSessionProvider(projects_dir=str(projects_dir))
        records = await provider.list_sessions()

        assert len(records) == 1
        record = records[0]
        assert record.provider == "claude-code"
        assert record.harness == "claude"
        assert record.external_id == CLAUDE_SESSION_ID
        assert record.workspace_path == str(workspace)
        assert record.title == "Fix the login bug please"
        assert record.model == "claude-sonnet-4-6"
        assert record.created_at is not None
        assert record.updated_at is not None
        assert record.workspace_exists is True
        assert record.live is True

    async def test_stale_session_is_not_live(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        projects_dir = tmp_path / "projects"
        _write_claude_session(projects_dir, CLAUDE_SESSION_ID, workspace)

        provider = ClaudeCodeSessionProvider(
            projects_dir=str(projects_dir),
            live_threshold_seconds=0,
        )
        records = await provider.list_sessions()

        assert records[0].live is False

    async def test_skips_non_uuid_files(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        projects_dir = tmp_path / "projects"
        _write_claude_session(projects_dir, CLAUDE_SESSION_ID, workspace)
        project_dir = next(projects_dir.iterdir())
        (project_dir / "agent-helper.jsonl").write_text("{}\n")

        provider = ClaudeCodeSessionProvider(projects_dir=str(projects_dir))
        records = await provider.list_sessions()

        assert [r.external_id for r in records] == [CLAUDE_SESSION_ID]

    async def test_missing_projects_dir_returns_empty(self, tmp_path: Path) -> None:
        provider = ClaudeCodeSessionProvider(projects_dir=str(tmp_path / "missing"))
        assert await provider.list_sessions() == []

    async def test_get_session_by_id(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        projects_dir = tmp_path / "projects"
        _write_claude_session(projects_dir, CLAUDE_SESSION_ID, workspace)

        provider = ClaudeCodeSessionProvider(projects_dir=str(projects_dir))
        record = await provider.get_session(CLAUDE_SESSION_ID)

        assert record is not None
        assert record.external_id == CLAUDE_SESSION_ID

    async def test_get_session_rejects_non_uuid(self, tmp_path: Path) -> None:
        provider = ClaudeCodeSessionProvider(projects_dir=str(tmp_path))
        assert await provider.get_session("not-a-uuid") is None

    async def test_get_session_missing_returns_none(self, tmp_path: Path) -> None:
        provider = ClaudeCodeSessionProvider(projects_dir=str(tmp_path))
        assert await provider.get_session(CLAUDE_SESSION_ID) is None

    async def test_missing_workspace_flagged(self, tmp_path: Path) -> None:
        projects_dir = tmp_path / "projects"
        _write_claude_session(projects_dir, CLAUDE_SESSION_ID, tmp_path / "gone")

        provider = ClaudeCodeSessionProvider(projects_dir=str(projects_dir))
        records = await provider.list_sessions()

        assert records[0].workspace_exists is False

    async def test_corrupt_lines_are_skipped(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        projects_dir = tmp_path / "projects"
        path = _write_claude_session(projects_dir, CLAUDE_SESSION_ID, workspace)
        path.write_text("not-json\n" + path.read_text())

        provider = ClaudeCodeSessionProvider(projects_dir=str(projects_dir))
        records = await provider.list_sessions()

        assert records[0].workspace_path == str(workspace)

    async def test_max_sessions_caps_results(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        projects_dir = tmp_path / "projects"
        _write_claude_session(projects_dir, CLAUDE_SESSION_ID, workspace)
        _write_claude_session(
            projects_dir,
            "11111111-2222-3333-4444-555555555555",
            workspace,
        )

        provider = ClaudeCodeSessionProvider(projects_dir=str(projects_dir), max_sessions=1)
        records = await provider.list_sessions()

        assert len(records) == 1


class TestCodexSessionProvider:
    async def test_lists_sessions_with_metadata(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        sessions_dir = tmp_path / "sessions"
        _write_codex_session(sessions_dir, CODEX_THREAD_ID, workspace)

        provider = CodexSessionProvider(sessions_dir=str(sessions_dir))
        records = await provider.list_sessions()

        assert len(records) == 1
        record = records[0]
        assert record.provider == "codex"
        assert record.harness == "codex"
        assert record.external_id == CODEX_THREAD_ID
        assert record.workspace_path == str(workspace)
        assert record.title == "Refactor the billing module"
        assert record.model == "gpt-5-codex"
        assert record.created_at is not None
        assert record.workspace_exists is True
        assert record.live is True

    async def test_stale_session_is_not_live(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        sessions_dir = tmp_path / "sessions"
        _write_codex_session(sessions_dir, CODEX_THREAD_ID, workspace)

        provider = CodexSessionProvider(
            sessions_dir=str(sessions_dir),
            live_threshold_seconds=0,
        )
        records = await provider.list_sessions()

        assert records[0].live is False

    async def test_missing_sessions_dir_returns_empty(self, tmp_path: Path) -> None:
        provider = CodexSessionProvider(sessions_dir=str(tmp_path / "missing"))
        assert await provider.list_sessions() == []

    async def test_file_without_session_meta_is_skipped(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        day_dir = sessions_dir / "2026" / "06" / "01"
        day_dir.mkdir(parents=True)
        (day_dir / "rollout-2026-06-01T10-00-00-deadbeef.jsonl").write_text(
            json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n"
        )

        provider = CodexSessionProvider(sessions_dir=str(sessions_dir))
        assert await provider.list_sessions() == []

    async def test_get_session_by_id(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        sessions_dir = tmp_path / "sessions"
        _write_codex_session(sessions_dir, CODEX_THREAD_ID, workspace)

        provider = CodexSessionProvider(sessions_dir=str(sessions_dir))
        record = await provider.get_session(CODEX_THREAD_ID)

        assert record is not None
        assert record.external_id == CODEX_THREAD_ID

    async def test_get_session_missing_returns_none(self, tmp_path: Path) -> None:
        provider = CodexSessionProvider(sessions_dir=str(tmp_path))
        assert await provider.get_session("nope") is None
        assert await provider.get_session("") is None
