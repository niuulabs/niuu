"""Tests for the Skuld broker /api/files endpoint."""

import pytest
from fastapi.testclient import TestClient

from skuld.broker import app, broker


class TestFileListingEndpoint:
    """Tests for GET /api/files workspace file listing."""

    @pytest.fixture(autouse=True)
    def _setup_workspace(self, tmp_path):
        """Point the broker's workspace_dir at a temporary directory."""
        self._original_workspace = broker.workspace_dir
        broker.workspace_dir = str(tmp_path)
        self.workspace = tmp_path
        self.client = TestClient(
            app, raise_server_exceptions=False, headers={"x-niuu-room-role": "owner"}
        )
        yield
        broker.workspace_dir = self._original_workspace

    def test_list_root_directory(self):
        """Test listing files in workspace root returns correct entries."""
        (self.workspace / "src").mkdir()
        (self.workspace / "README.md").touch()
        (self.workspace / "package.json").touch()

        response = self.client.get("/api/files")
        assert response.status_code == 200

        data = response.json()
        entries = data["entries"]

        # Directories first, then files, both alpha-sorted
        names = [e["name"] for e in entries]
        assert names == ["src", "package.json", "README.md"]

        # Verify types
        assert entries[0]["type"] == "directory"
        assert entries[1]["type"] == "file"
        assert entries[2]["type"] == "file"

    def test_list_subdirectory(self):
        """Test listing a subdirectory via path parameter."""
        src = self.workspace / "src"
        src.mkdir()
        (src / "main.py").touch()
        (src / "utils").mkdir()

        response = self.client.get("/api/files", params={"path": "src"})
        assert response.status_code == 200

        entries = response.json()["entries"]
        names = [e["name"] for e in entries]
        assert names == ["utils", "main.py"]
        # paths are relative to workspace
        assert entries[0]["path"] == "src/utils"
        assert entries[1]["path"] == "src/main.py"

    def test_path_traversal_blocked(self):
        """Test that ../../../etc/passwd is rejected."""
        response = self.client.get("/api/files", params={"path": "../../../etc"})
        assert response.status_code == 400
        assert "Path traversal" in response.json()["detail"]

    def test_hidden_files_filtered(self):
        """Test .env, .secret are hidden but .github, .claude are shown."""
        (self.workspace / ".env").touch()
        (self.workspace / ".secret").touch()
        (self.workspace / ".github").mkdir()
        (self.workspace / ".claude").mkdir()
        (self.workspace / ".vscode").mkdir()
        (self.workspace / "visible.txt").touch()

        response = self.client.get("/api/files")
        assert response.status_code == 200

        names = [e["name"] for e in response.json()["entries"]]
        # Allowed hidden dirs should be present
        assert ".github" in names
        assert ".claude" in names
        assert ".vscode" in names
        # Regular hidden files should be filtered
        assert ".env" not in names
        assert ".secret" not in names
        # Normal files should be present
        assert "visible.txt" in names

    def test_noise_directories_filtered(self):
        """Test node_modules, __pycache__, .git are filtered."""
        (self.workspace / "node_modules").mkdir()
        (self.workspace / "__pycache__").mkdir()
        (self.workspace / ".git").mkdir()
        (self.workspace / "venv").mkdir()
        (self.workspace / ".venv").mkdir()
        (self.workspace / ".mypy_cache").mkdir()
        (self.workspace / "src").mkdir()

        response = self.client.get("/api/files")
        assert response.status_code == 200

        names = [e["name"] for e in response.json()["entries"]]
        assert "node_modules" not in names
        assert "__pycache__" not in names
        assert ".git" not in names
        assert "venv" not in names
        assert ".venv" not in names
        assert ".mypy_cache" not in names
        assert "src" in names

    def test_nonexistent_directory_404(self):
        """Test 404 for missing directory."""
        response = self.client.get("/api/files", params={"path": "nonexistent"})
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_empty_directory(self):
        """Test empty directory returns empty list."""
        (self.workspace / "empty").mkdir()

        response = self.client.get("/api/files", params={"path": "empty"})
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_directories_sorted_first(self):
        """Test directories appear before files in listing."""
        (self.workspace / "zebra.txt").touch()
        (self.workspace / "alpha").mkdir()
        (self.workspace / "beta.py").touch()
        (self.workspace / "delta").mkdir()

        response = self.client.get("/api/files")
        assert response.status_code == 200

        entries = response.json()["entries"]
        types = [e["type"] for e in entries]
        names = [e["name"] for e in entries]

        # Directories first (alpha-sorted), then files (alpha-sorted)
        assert types == ["directory", "directory", "file", "file"]
        assert names == ["alpha", "delta", "beta.py", "zebra.txt"]

    def test_default_path_is_root(self):
        """Test that omitting path parameter lists the workspace root."""
        (self.workspace / "file.txt").touch()

        response = self.client.get("/api/files")
        assert response.status_code == 200
        assert len(response.json()["entries"]) == 1
        assert response.json()["entries"][0]["name"] == "file.txt"
