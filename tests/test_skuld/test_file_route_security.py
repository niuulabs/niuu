"""End-to-end containment regressions for Skuld file routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skuld.broker import app, broker


@pytest.fixture
def secured_workspace(tmp_path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    monkeypatch.setattr(broker, "workspace_dir", str(workspace))
    client = TestClient(app, raise_server_exceptions=False)
    yield client, workspace, outside
    client.close()


def test_download_rejects_symlink_escape(secured_workspace) -> None:
    client, workspace, outside = secured_workspace
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(secret)

    response = client.get("/api/files/download", params={"path": "escape.txt"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Path traversal not allowed"


def test_raw_upload_rejects_symlinked_parent_escape(secured_workspace) -> None:
    client, workspace, outside = secured_workspace
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    response = client.put(
        "/api/files/upload",
        params={"path": "escape/created.txt"},
        content=b"must not escape",
    )

    assert response.status_code == 400
    assert not (outside / "created.txt").exists()


def test_delete_rejects_symlink_escape_without_removing_target(secured_workspace) -> None:
    client, workspace, outside = secured_workspace
    secret = outside / "secret.txt"
    secret.write_text("keep", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(secret)

    response = client.delete("/api/files", params={"path": "escape.txt"})

    assert response.status_code == 400
    assert secret.read_text(encoding="utf-8") == "keep"
