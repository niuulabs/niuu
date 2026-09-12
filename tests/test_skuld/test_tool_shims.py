from __future__ import annotations

import os
import subprocess
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
    assert "-m ravn.cli.tracker_bridge" in tracker_script
    assert "Bearer" not in tracker_script


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
    assert "-m ravn.cli.mimir_bridge publish-files" in mimir_script


def test_ensure_codex_tool_shims_adds_mimir_related_bridge(tmp_path: Path) -> None:
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
    related_shim = bin_dir / "mimir_related"
    assert related_shim.exists()
    script = related_shim.read_text(encoding="utf-8")
    assert "-m ravn.cli.mimir_bridge related" in script
    assert "# mimir_related:" in script
    assert "wikilink graph" in script
    assert env["RAVN_MIMIR_PATH"] == "/tmp/mimir"


def test_ensure_codex_tool_shims_describe_each_mimir_command(tmp_path: Path) -> None:
    bin_dir, _ = ensure_codex_tool_shims(
        str(tmp_path),
        mcp_servers=[
            {
                "name": "mimir-local",
                "command": "python3",
                "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"],
            }
        ],
    )

    assert bin_dir is not None
    for name in (
        "mimir_ingest",
        "mimir_search",
        "mimir_read",
        "mimir_read_source",
        "mimir_write",
        "mimir_publish_files",
        "mimir_list",
        "mimir_related",
    ):
        script = (bin_dir / name).read_text(encoding="utf-8")
        assert f"# {name}:" in script


def test_ensure_codex_tool_shims_bakes_ravn_config_when_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("RAVN_CONFIG", "/tmp/ravn-config.yaml")

    bin_dir, env = ensure_codex_tool_shims(str(tmp_path))

    assert bin_dir is not None
    assert env["RAVN_CONFIG"] == "/tmp/ravn-config.yaml"
    assert "RAVN_CONFIG=" not in (bin_dir / "tracker_issue").read_text()
    tracker_script = (bin_dir / "tracker_issue").read_text(encoding="utf-8")
    assert "-m ravn.cli.tracker_bridge" in tracker_script


def test_ensure_codex_tool_shims_adds_mimir_bridges_for_dynamic_ravn_mount(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "ravn.yaml"
    config_path.write_text(
        """
mimir:
  registry_refs:
    - mount_name: mimir-yggdrasil
      url: https://mimir.example.test/api/v1
  bindings:
    - target_id: tool-builder
      resource_node_id: capability-memory
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RAVN_CONFIG", str(config_path))

    bin_dir, env = ensure_codex_tool_shims(
        str(tmp_path / "workspace"),
        mcp_servers=[
            {
                "name": "mimir-yggdrasil",
                "type": "http",
                "url": "https://mimir.example.test/api/v1",
            }
        ],
    )

    assert bin_dir is not None
    assert (bin_dir / "mimir_write").exists()
    assert (bin_dir / "mimir_read").exists()
    assert env["RAVN_CONFIG"] == str(config_path)
    assert "RAVN_MIMIR_PATH" not in env


def test_shims_run_installed_modules_without_uv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RAVN_CONFIG", raising=False)
    bin_dir, env = ensure_codex_tool_shims(
        str(tmp_path),
        mcp_servers=[{"args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"]}],
    )
    assert bin_dir is not None
    for name in ("tracker_issue", "mimir_search"):
        result = subprocess.run(
            [str(bin_dir / name), "--help"],
            env={**os.environ, **env, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "usage:" in result.stdout
