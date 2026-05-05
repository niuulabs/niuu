"""Tests for the Skuld broker /api/diff endpoint."""

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


class TestDiffEndpoint:
    """Tests for GET /api/diff."""

    @pytest.fixture(autouse=True)
    def _setup_workspace(self, tmp_path):
        self._original_workspace = broker.workspace_dir
        broker.workspace_dir = str(tmp_path)
        self.workspace = tmp_path
        self.client = TestClient(app, raise_server_exceptions=False)
        yield
        self.client.close()
        broker.workspace_dir = self._original_workspace

    def test_missing_file_param_returns_422(self):
        response = self.client.get("/api/diff")
        assert response.status_code == 422

    def test_invalid_base_returns_400(self):
        response = self.client.get("/api/diff", params={"file": "main.py", "base": "invalid"})
        assert response.status_code == 400
        assert "Invalid base" in response.json()["detail"]

    def test_path_traversal_blocked(self):
        response = self.client.get(
            "/api/diff",
            params={"file": "../../../etc/passwd", "base": "last-commit"},
        )
        assert response.status_code == 400
        assert "Path traversal" in response.json()["detail"]

    @patch("skuld.broker.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_successful_diff(self, mock_create_subprocess_exec):
        diff_output = "@@ -1,2 +1,3 @@\n line1\n+added\n line2\n"

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(diff_output.encode(), b""))

        mock_create_subprocess_exec.return_value = mock_proc

        response = self.client.get(
            "/api/diff",
            params={"file": "src/main.py", "base": "last-commit"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filePath"] == "src/main.py"
        assert len(data["hunks"]) == 1

    @patch("skuld.broker.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_git_diff_failure_returns_502(self, mock_create_subprocess_exec):
        mock_proc = AsyncMock()
        mock_proc.returncode = 128
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: bad object"))

        mock_create_subprocess_exec.return_value = mock_proc

        response = self.client.get(
            "/api/diff",
            params={"file": "src/main.py", "base": "last-commit"},
        )
        assert response.status_code == 502
        assert "git diff failed" in response.json()["detail"]

    @patch("skuld.broker.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_default_branch_diff_command(self, mock_create_subprocess_exec):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        mock_create_subprocess_exec.return_value = mock_proc

        response = self.client.get(
            "/api/diff",
            params={"file": "src/main.py", "base": "default-branch"},
        )
        assert response.status_code == 200

        # Verify the command used main...HEAD
        call_args = mock_create_subprocess_exec.call_args
        cmd = call_args[0]
        assert "main...HEAD" in cmd
