"""Tests for the Mimir shell bridge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mimir import bridge as mimir_bridge
from mimir.bridge import main


@pytest.fixture(autouse=True)
def _use_bridge_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """These bridge tests run without a daemon config."""
    monkeypatch.delenv("RAVN_CONFIG", raising=False)


def test_adapter_without_a_configured_mimir_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAVN_CONFIG", raising=False)

    with pytest.raises(SystemExit, match="RAVN_CONFIG"):
        mimir_bridge._adapter()


def test_adapter_prefers_configured_mimir(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = object()
    monkeypatch.setattr(mimir_bridge, "_configured_adapter", lambda: configured)

    assert mimir_bridge._adapter() is configured


def test_publish_files_uses_routing_when_no_mount_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    workspace = tmp_path / "workspace"
    page = workspace / "notes" / "result.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Result\n\nReady.", encoding="utf-8")

    class RecordingMimir:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str | None]] = []

        async def upsert_page(self, path: str, content: str, mimir: str | None = None, meta=None):
            self.writes.append((path, mimir))

        async def get_page(self, path: str):
            return SimpleNamespace(content=page.read_text(encoding="utf-8"))

    adapter = RecordingMimir()
    monkeypatch.setenv("RAVN_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(mimir_bridge, "_adapter", lambda: adapter)

    exit_code = main(["publish-files", "notes/result.md"])

    assert exit_code == 0
    assert adapter.writes == [("notes/result.md", None)]
    assert "routed to: local" not in capsys.readouterr().out


def test_list_uses_configured_adapter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    class ListingMimir:
        async def list_pages(self, category=None, prefix=None):
            assert prefix == "research/campaigns"
            return [
                SimpleNamespace(path="research/campaigns/example/final.md"),
                SimpleNamespace(path="research/campaigns/example/manifest.md"),
            ]

    monkeypatch.setattr(mimir_bridge, "_adapter", lambda: ListingMimir())

    exit_code = main(["list", "research/campaigns"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "research/campaigns/example/final.md",
        "research/campaigns/example/manifest.md",
    ]
