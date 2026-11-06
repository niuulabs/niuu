"""Tests for the Skuld broker /api/diff endpoint."""

import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from skuld.broker import _parse_diff_output, app, broker


class TestParseDiffOutput:
    """Tests for the git diff output parser."""

    def test_empty_diff(self):
        result = _parse_diff_output("", "src/main.py")
        assert result == {"filePath": "src/main.py", "hunks": []}

    def test_single_hunk_with_additions(self):
        raw = (
            "diff --git a/file.py b/file.py\n"
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,5 @@\n"
            " line1\n"
            " line2\n"
            "+new_line_a\n"
            "+new_line_b\n"
            " line3\n"
        )
        result = _parse_diff_output(raw, "file.py")
        assert result["filePath"] == "file.py"
        assert len(result["hunks"]) == 1

        hunk = result["hunks"][0]
        assert hunk["oldStart"] == 1
        assert hunk["oldCount"] == 3
        assert hunk["newStart"] == 1
        assert hunk["newCount"] == 5

        lines = hunk["lines"]
        assert len(lines) == 5
        assert lines[0]["type"] == "context"
        assert lines[0]["content"] == "line1"
        assert lines[2]["type"] == "add"
        assert lines[2]["content"] == "new_line_a"

    def test_single_hunk_with_removals(self):
        raw = "@@ -1,4 +1,2 @@\n keep\n-removed_a\n-removed_b\n keep2\n"
        result = _parse_diff_output(raw, "file.py")
        lines = result["hunks"][0]["lines"]
        assert len(lines) == 4
        assert lines[1]["type"] == "remove"
        assert lines[1]["content"] == "removed_a"
        assert "oldLine" in lines[1]

    def test_multiple_hunks(self):
        raw = "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n@@ -10,3 +10,3 @@\n x\n-y\n+Y\n z\n"
        result = _parse_diff_output(raw, "file.py")
        assert len(result["hunks"]) == 2
        assert result["hunks"][0]["oldStart"] == 1
        assert result["hunks"][1]["oldStart"] == 10

    def test_line_numbers_increment(self):
        raw = "@@ -5,3 +5,4 @@\n ctx\n+added\n ctx2\n ctx3\n"
        result = _parse_diff_output(raw, "file.py")
        lines = result["hunks"][0]["lines"]
        # Context line at old=5, new=5
        assert lines[0]["oldLine"] == 5
        assert lines[0]["newLine"] == 5
        # Added line only has newLine=6
        assert lines[1]["newLine"] == 6
        assert "oldLine" not in lines[1]
        # Next context at old=6, new=7
        assert lines[2]["oldLine"] == 6
        assert lines[2]["newLine"] == 7


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A workspace on ``feature``, started from ``dev`` (recorded as niuu.baseBranch)."""
    _git(tmp_path, "init", "-b", "dev")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "app.py").write_text("a\nb\nc\n")
    (tmp_path / "old.py").write_text("gone\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/dev", "HEAD")
    _git(tmp_path, "config", "niuu.baseBranch", "dev")
    _git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "committed.py").write_text("one\ntwo\n")
    _git(tmp_path, "add", "committed.py")
    _git(tmp_path, "commit", "-m", "feature work")
    # Uncommitted: an edit, a deletion, a new file, a new binary file and an ignored file.
    (tmp_path / "app.py").write_text("a\nB\nc\nd\n")
    (tmp_path / "old.py").unlink()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\nno trailing newline")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x01")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.js").write_text("ignored\n")
    return tmp_path


class TestDiffEndpoint:
    """Tests for GET /api/diff and GET /api/diff/files."""

    @pytest.fixture(autouse=True)
    def _setup_workspace(self, tmp_path):
        self._original_workspace = broker.workspace_dir
        broker.workspace_dir = str(tmp_path)
        self.client = TestClient(
            app, raise_server_exceptions=False, headers={"x-niuu-room-role": "owner"}
        )
        yield
        self.client.close()
        broker.workspace_dir = self._original_workspace

    def test_missing_file_param_returns_422(self):
        response = self.client.get("/api/diff")
        assert response.status_code == 422

    def test_invalid_base_returns_400(self, repo):
        response = self.client.get("/api/diff", params={"file": "main.py", "base": "invalid"})
        assert response.status_code == 400
        assert "Invalid base" in response.json()["detail"]
        response = self.client.get("/api/diff/files", params={"base": "invalid"})
        assert response.status_code == 400

    def test_path_traversal_blocked(self):
        response = self.client.get(
            "/api/diff",
            params={"file": "../../../etc/passwd", "base": "last-commit"},
        )
        assert response.status_code == 400
        assert "Path traversal" in response.json()["detail"]

    def test_lists_new_deleted_and_modified_files_since_the_last_commit(self, repo):
        response = self.client.get("/api/diff/files", params={"base": "last-commit"})
        assert response.status_code == 200
        files = {f["path"]: f for f in response.json()["files"]}
        assert files == {
            "app.py": {"path": "app.py", "status": "mod", "ins": 2, "del": 1},
            "old.py": {"path": "old.py", "status": "del", "ins": 0, "del": 1},
            "docs/guide.md": {"path": "docs/guide.md", "status": "new", "ins": 3, "del": 0},
            "logo.png": {"path": "logo.png", "status": "new", "ins": 0, "del": 0},
        }

    def test_against_the_base_branch_includes_committed_work(self, repo):
        response = self.client.get("/api/diff/files", params={"base": "default-branch"})
        assert response.status_code == 200
        files = {f["path"]: f["status"] for f in response.json()["files"]}
        assert files == {
            "app.py": "mod",
            "committed.py": "new",
            "old.py": "del",
            "docs/guide.md": "new",
            "logo.png": "new",
        }

        response = self.client.get(
            "/api/diff", params={"file": "committed.py", "base": "default-branch"}
        )
        lines = response.json()["hunks"][0]["lines"]
        assert [line["content"] for line in lines] == ["one", "two"]
        assert {line["type"] for line in lines} == {"add"}

    def test_a_new_file_diffs_as_all_additions(self, repo):
        response = self.client.get(
            "/api/diff", params={"file": "docs/guide.md", "base": "last-commit"}
        )
        assert response.status_code == 200
        lines = response.json()["hunks"][0]["lines"]
        assert [line["content"] for line in lines] == ["# Guide", "", "no trailing newline"]
        assert {line["type"] for line in lines} == {"add"}

    def test_a_tracked_file_diffs_against_the_last_commit(self, repo):
        response = self.client.get("/api/diff", params={"file": "app.py", "base": "last-commit"})
        types = [(line["type"], line["content"]) for line in response.json()["hunks"][0]["lines"]]
        assert ("remove", "b") in types
        assert ("add", "B") in types
        assert ("add", "d") in types

    def test_says_when_the_workspace_recorded_no_base_branch(self, repo):
        _git(repo, "config", "--unset", "niuu.baseBranch")
        response = self.client.get("/api/diff/files", params={"base": "default-branch"})
        assert response.status_code == 409
        assert "niuu.baseBranch" in response.json()["detail"]

    @patch("skuld.broker.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_git_failure_returns_502(self, mock_create_subprocess_exec):
        mock_proc = AsyncMock()
        mock_proc.returncode = 128
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: bad object"))
        mock_create_subprocess_exec.return_value = mock_proc

        response = self.client.get("/api/diff/files", params={"base": "last-commit"})
        assert response.status_code == 502
        assert "fatal: bad object" in response.json()["detail"]

    @patch("skuld.broker.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_git_timeout_returns_504(self, mock_create_subprocess_exec):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_create_subprocess_exec.return_value = mock_proc
        response = self.client.get("/api/diff", params={"file": "app.py"})
        assert response.status_code == 504
        assert "timed out" in response.json()["detail"]
