"""Tests for the ``related`` subcommand of the Mimir shell bridge (NIU-1058)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from ravn.cli import mimir_bridge
from ravn.cli.mimir_bridge import main

ENTITY_PAGE = """---
type: entity
confidence: high
entity_type: person
---

# Alice Smith

## Compiled Truth

### Key Facts
- Alice is the lead engineer at Acme Corp.

### Relationships
- [[acme-corp]] — rel: works_at — lead engineer.

## Timeline

- 2026-05-01: Joined Acme. [Source: test, fixture, 2026-05-01]
"""

ORG_PAGE = """---
type: entity
confidence: high
entity_type: organization
---

# Acme Corp

## Compiled Truth

### Key Facts
- Acme builds widgets.

## Timeline

- 2026-01-01: Founded. [Source: test, fixture, 2026-01-01]
"""

TOPIC_PAGE = """---
type: topic
---

# Widget assembly

## Compiled Truth

### Key Facts
- Widgets are assembled by [[alice-smith]] at the plant.

## Timeline

- 2026-02-01: Plant opened. [Source: test, fixture, 2026-02-01]
"""


@pytest.fixture(autouse=True)
def _use_bridge_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """These bridge tests exercise RAVN_MIMIR_PATH, not a daemon config."""
    monkeypatch.delenv("RAVN_CONFIG", raising=False)


@pytest.fixture()
def mimir_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "mimir"
    wiki = root / "wiki"
    (wiki / "entities").mkdir(parents=True)
    (wiki / "technical").mkdir(parents=True)
    (wiki / "entities" / "alice-smith.md").write_text(ENTITY_PAGE, encoding="utf-8")
    (wiki / "entities" / "acme-corp.md").write_text(ORG_PAGE, encoding="utf-8")
    (wiki / "technical" / "widgets.md").write_text(TOPIC_PAGE, encoding="utf-8")
    monkeypatch.setenv("RAVN_MIMIR_PATH", str(root))
    return root


def test_related_outputs_json_neighbors(mimir_root: Path, capsys: pytest.CaptureFixture) -> None:
    exit_code = main(["related", "--path", "entities/alice-smith.md"])

    assert exit_code == 0
    items = json.loads(capsys.readouterr().out)
    assert {item["path"] for item in items} == {
        "entities/acme-corp.md",
        "technical/widgets.md",
    }
    for item in items:
        assert item["hop"] == 1
        assert item["direction"] in {"in", "out"}


def test_related_rel_filter(mimir_root: Path, capsys: pytest.CaptureFixture) -> None:
    exit_code = main(
        ["related", "--path", "entities/alice-smith.md", "--depth", "1", "--rel", "works_at"]
    )

    assert exit_code == 0
    items = json.loads(capsys.readouterr().out)
    assert [item["path"] for item in items] == ["entities/acme-corp.md"]
    assert items[0]["rel"] == "works_at"


def test_related_depth_two(mimir_root: Path, capsys: pytest.CaptureFixture) -> None:
    exit_code = main(["related", "--path", "technical/widgets.md", "--depth", "2"])

    assert exit_code == 0
    items = json.loads(capsys.readouterr().out)
    assert {item["path"] for item in items} == {
        "entities/alice-smith.md",
        "entities/acme-corp.md",
    }


def test_related_requires_mimir_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAVN_MIMIR_PATH", raising=False)

    with pytest.raises(SystemExit):
        main(["related", "--path", "entities/alice-smith.md"])


def test_related_requires_path_argument(mimir_root: Path) -> None:
    with pytest.raises(SystemExit):
        main(["related"])


def test_related_adapter_without_link_graph(
    mimir_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(mimir_bridge, "_adapter", object)

    exit_code = main(["related", "--path", "entities/alice-smith.md"])

    assert exit_code == 1
    assert "link graph" in capsys.readouterr().err


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
    monkeypatch.setenv("RAVN_MIMIR_NAME", "local")
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


@respx.mock
def test_related_http_backed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("RAVN_MIMIR_PATH", "http://mimir.test")
    payload = [{"path": "entities/acme-corp.md", "hop": 1, "rel": "works_at", "direction": "out"}]
    route = respx.get("http://mimir.test/mimir/related").mock(
        return_value=Response(200, json=payload)
    )

    exit_code = main(
        ["related", "--path", "entities/alice-smith.md", "--depth", "2", "--rel", "works_at"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == payload
    request = route.calls.last.request
    assert request.url.params["path"] == "entities/alice-smith.md"
    assert request.url.params["depth"] == "2"
    assert request.url.params["rel"] == "works_at"


@respx.mock
def test_related_http_backed_omits_rel_when_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("RAVN_MIMIR_PATH", "https://mimir.test/")
    route = respx.get("https://mimir.test/mimir/related").mock(return_value=Response(200, json=[]))

    exit_code = main(["related", "--path", "entities/alice-smith.md"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == []
    request = route.calls.last.request
    assert "rel" not in request.url.params
    assert request.url.params["depth"] == "1"
