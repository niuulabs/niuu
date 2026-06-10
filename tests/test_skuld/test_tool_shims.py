from __future__ import annotations

from pathlib import Path

from skuld.transports.tool_shims import ensure_codex_tool_shims


def test_ensure_codex_tool_shims_creates_tracker_bridge_without_mimir(tmp_path: Path) -> None:
    bin_dir, env = ensure_codex_tool_shims(str(tmp_path))

    assert bin_dir is not None
    assert (bin_dir / "tracker_issue").exists()
    assert "RAVN_WORKSPACE_DIR" in env
    assert env["UV_CACHE_DIR"].endswith(".skuld-tools/.uv-cache")
    assert "RAVN_MIMIR_PATH" not in env
    tracker_script = (bin_dir / "tracker_issue").read_text(encoding="utf-8")
    assert 'TRACKER_PATH = "/api/v1/tracker/issues"' in tracker_script
    assert 'if len(argv) < 2 or argv[1] in {"-h", "--help", "help"}' in tracker_script
    assert "usage: tracker_issue update-status <issue-id> <status>" in tracker_script
    assert "urllib.request" in tracker_script


def test_ensure_codex_tool_shims_adds_tracker_and_mimir_bridges(tmp_path: Path) -> None:
    bin_dir, env = ensure_codex_tool_shims(
        str(tmp_path),
        mcp_servers=[
            {
                "name": "mimir-local",
                "command": "python3",
                "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir", "--name", "tmp-mimir"],
            }
        ],
    )

    assert bin_dir is not None
    assert (bin_dir / "tracker_issue").exists()
    assert (bin_dir / "mimir_publish_files").exists()
    assert env["RAVN_MIMIR_PATH"] == "/tmp/mimir"
    assert env["RAVN_MIMIR_NAME"] == "tmp-mimir"
    mimir_script = (bin_dir / "mimir_publish_files").read_text(encoding="utf-8")
    assert "PYTHONPATH=" in mimir_script
    assert "UV_CACHE_DIR=" in mimir_script
    assert 'uv" run --project' in mimir_script or "uv run --project" in mimir_script


def test_ensure_codex_tool_shims_bakes_ravn_config_when_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("RAVN_CONFIG", "/tmp/ravn-config.yaml")

    bin_dir, env = ensure_codex_tool_shims(str(tmp_path))

    assert bin_dir is not None
    assert env["RAVN_CONFIG"] == "/tmp/ravn-config.yaml"
    tracker_script = (bin_dir / "tracker_issue").read_text(encoding="utf-8")
    assert "BASE_URL = " in tracker_script
