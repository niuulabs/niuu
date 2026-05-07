"""Tests for local git workspace REST endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_local_git import create_local_git_router
from volundr.domain.ports import GitWorkspacePort, SessionRepository


def _make_session(session_id=None):
    """Create a minimal mock session."""
    from volundr.domain.models import Session

    sid = session_id or uuid4()
    return Session(id=sid, name="test-session", model="claude-sonnet-4-5-20250514")


@pytest.fixture
def mock_git_workspace() -> MagicMock:
    """Mock GitWorkspacePort."""
    mock = MagicMock(spec=GitWorkspacePort)
    mock.diff_files = AsyncMock(return_value=[])
    mock.file_diff = AsyncMock(return_value=None)
    mock.commit_log = AsyncMock(return_value=[])
    mock.pr_status = AsyncMock(return_value=None)
    mock.current_branch = AsyncMock(return_value="main")
    return mock


@pytest.fixture
def mock_session_repo() -> MagicMock:
    """Mock SessionRepository."""
    mock = MagicMock(spec=SessionRepository)
    mock.get = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Create a temp sessions base directory."""
    return tmp_path


def _create_workspace(sessions_dir: Path, session_id) -> Path:
    """Create a workspace directory for a session."""
    ws = sessions_dir / str(session_id) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _make_client(
    mock_git_workspace: MagicMock,
    mock_session_repo: MagicMock,
    sessions_base: str,
) -> TestClient:
    app = FastAPI()
    router = create_local_git_router(
        mock_git_workspace,
        session_repository=mock_session_repo,
        sessions_base=sessions_base,
    )
    app.include_router(router)
    return TestClient(app)


class TestGetPRStatus:
    """Tests for GET /api/v1/forge/sessions/{id}/pr."""

    def test_session_not_found(self, mock_git_workspace, mock_session_repo, sessions_dir):
        mock_session_repo.get.return_value = None
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{uuid4()}/pr")
        assert resp.status_code == 404
        assert "Session not found" in resp.json()["detail"]

    def test_workspace_not_found(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        mock_session_repo.get.return_value = session
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/pr")
        assert resp.status_code == 404
        assert "Workspace not found" in resp.json()["detail"]

    def test_returns_null_when_no_pr(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.pr_status.return_value = None
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/pr")
        assert resp.status_code == 200
        assert resp.json() is None

    def test_returns_pr_data(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.pr_status.return_value = {
            "number": 42,
            "url": "https://github.com/org/repo/pull/42",
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "checks": [{"name": "tests", "status": "SUCCESS"}],
        }
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/pr")
        assert resp.status_code == 200
        data = resp.json()
        assert data["number"] == 42
        assert data["state"] == "OPEN"
        assert len(data["checks"]) == 1


class TestGetDiffFiles:
    """Tests for GET /api/v1/forge/sessions/{id}/diff/files."""

    def test_session_not_found(self, mock_git_workspace, mock_session_repo, sessions_dir):
        mock_session_repo.get.return_value = None
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{uuid4()}/diff/files")
        assert resp.status_code == 404

    def test_returns_changed_files(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.diff_files.return_value = [
            {"path": "src/main.py", "additions": 10, "deletions": 5},
            {"path": "README.md", "additions": 3, "deletions": 0},
        ]
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/diff/files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 2
        assert data["files"][0]["path"] == "src/main.py"
        assert data["files"][0]["additions"] == 10

    def test_returns_empty_list(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.diff_files.return_value = []
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/diff/files")
        assert resp.status_code == 200
        assert resp.json()["files"] == []


class TestGetFileDiff:
    """Tests for GET /api/v1/forge/sessions/{id}/diff."""

    def test_session_not_found(self, mock_git_workspace, mock_session_repo, sessions_dir):
        mock_session_repo.get.return_value = None
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(
            f"/api/v1/forge/sessions/{uuid4()}/diff",
            params={"path": "file.py"},
        )
        assert resp.status_code == 404

    def test_returns_diff_text(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        diff_text = "diff --git a/f.py b/f.py\n-old\n+new\n"
        mock_git_workspace.file_diff.return_value = diff_text
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(
            f"/api/v1/forge/sessions/{session.id}/diff",
            params={"path": "f.py", "base_branch": "develop"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "f.py"
        assert data["diff"] == diff_text

    def test_returns_null_diff(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.file_diff.return_value = None
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(
            f"/api/v1/forge/sessions/{session.id}/diff",
            params={"path": "f.py"},
        )
        assert resp.status_code == 200
        assert resp.json()["diff"] is None

    def test_requires_path_param(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/diff")
        assert resp.status_code == 422  # missing required query param

    def test_rejects_path_traversal(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(
            f"/api/v1/forge/sessions/{session.id}/diff",
            params={"path": "../../etc/passwd"},
        )
        assert resp.status_code == 400
        assert "must not contain '..'" in resp.json()["detail"]

    def test_rejects_flag_in_base_branch(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(
            f"/api/v1/forge/sessions/{session.id}/diff",
            params={"path": "file.py", "base_branch": "--exec=malicious"},
        )
        assert resp.status_code == 400
        assert "must not start with '-'" in resp.json()["detail"]


class TestGetCommits:
    """Tests for GET /api/v1/forge/sessions/{id}/commits."""

    def test_session_not_found(self, mock_git_workspace, mock_session_repo, sessions_dir):
        mock_session_repo.get.return_value = None
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{uuid4()}/commits")
        assert resp.status_code == 404

    def test_returns_commits(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.commit_log.return_value = [
            {"hash": "abc123full", "short_hash": "abc123", "message": "feat: add"},
            {"hash": "def456full", "short_hash": "def456", "message": "fix: bug"},
        ]
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/commits")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commits"]) == 2
        assert data["commits"][0]["short_hash"] == "abc123"

    def test_since_query_param(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.commit_log.return_value = []
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(
            f"/api/v1/forge/sessions/{session.id}/commits",
            params={"since": "2025-01-01"},
        )
        assert resp.status_code == 200
        mock_git_workspace.commit_log.assert_called_once()
        call_kwargs = mock_git_workspace.commit_log.call_args
        assert call_kwargs[1]["since"] == "2025-01-01" or call_kwargs[0][1] == "2025-01-01"

    def test_rejects_flag_in_since(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(
            f"/api/v1/forge/sessions/{session.id}/commits",
            params={"since": "--exec=bad"},
        )
        assert resp.status_code == 400
        assert "must not start with '-'" in resp.json()["detail"]

    def test_returns_empty_commits(self, mock_git_workspace, mock_session_repo, sessions_dir):
        session = _make_session()
        _create_workspace(sessions_dir, session.id)
        mock_session_repo.get.return_value = session
        mock_git_workspace.commit_log.return_value = []
        client = _make_client(mock_git_workspace, mock_session_repo, str(sessions_dir))
        resp = client.get(f"/api/v1/forge/sessions/{session.id}/commits")
        assert resp.status_code == 200
        assert resp.json()["commits"] == []
