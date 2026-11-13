"""Tests for mimir.app — create_app and helper functions (NIU-577)."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mimir.app import create_app
from mimir.config import MimirServiceConfig
from mimir.exceptions import MimirConfigurationError
from mimir.registry import MimirRegistryStore
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter

BACKEND = {"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"}


def _config(tmp_path: Path, **overrides) -> MimirServiceConfig:
    return MimirServiceConfig(path=str(tmp_path / "mimir"), backend=BACKEND, **overrides)


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


def test_create_app_returns_fastapi_app(tmp_path: Path) -> None:
    from fastapi import FastAPI

    config = _config(tmp_path)
    app = create_app(config)
    assert isinstance(app, FastAPI)


def test_create_app_mounts_mimir_router(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_app(config)
    with TestClient(app) as client:
        routes = client.get("/openapi.json").json()["paths"]
    assert any("/mimir" in r for r in routes)


def test_create_app_exposes_api_v1_mimir_routes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_app(config)
    with TestClient(app) as client:
        assert client.get("/api/v1/mimir/stats").status_code == 200
        assert client.get("/api/v1/mimir/mounts").status_code == 200
        assert client.get("/api/v1/mimir/mcp").status_code == 405


class _SeededAdapter(InMemoryMimirAdapter):
    """A store that already holds 80 pages."""

    def __init__(self) -> None:
        super().__init__()
        now = datetime.now(UTC)
        for index in range(80):
            self.pages[f"notes/page-{index}.md"] = (
                f"# Page {index}\n\nA summary of page {index}.\n",
                now,
            )


def test_create_app_compresses_large_json(tmp_path: Path) -> None:
    """The page listing and graph shipped raw: 880 KB and 1.1 MB on yggdrasil."""
    config = MimirServiceConfig(
        path=str(tmp_path / "mimir"),
        backend={"adapter": "tests.test_mimir.unit.test_app._SeededAdapter"},
    )
    app = create_app(config)
    with TestClient(app) as client:
        compressed = client.get("/api/v1/mimir/pages", headers={"accept-encoding": "gzip"})
        stream = client.get(
            "/api/v1/mimir/pages",
            headers={"accept-encoding": "gzip", "accept": "text/event-stream"},
        )
    assert compressed.headers["content-encoding"] == "gzip"
    assert len(compressed.json()) == 80
    assert "content-encoding" not in stream.headers


def test_create_app_exposes_settings_schema(tmp_path: Path) -> None:
    config = _config(tmp_path, name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        response = client.get("/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Mimir"
    assert payload["sections"][0]["id"] == "service"


def test_create_app_exposes_mounted_mimir_settings_alias(tmp_path: Path) -> None:
    config = _config(tmp_path, name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        response = client.get("/mimir/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Mimir"
    assert payload["sections"][0]["id"] == "service"


def test_create_app_exposes_api_v1_mimir_settings_alias(tmp_path: Path) -> None:
    config = _config(tmp_path, name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        response = client.get("/api/v1/mimir/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Mimir"
    assert payload["sections"][0]["id"] == "service"


def test_create_app_without_a_backend_fails_naming_the_setting(tmp_path: Path) -> None:
    with pytest.raises(MimirConfigurationError, match="MIMIR__BACKEND"):
        create_app(MimirServiceConfig(path=str(tmp_path / "mimir")))


def test_create_app_rejects_a_backend_that_is_not_a_mimir_port(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"), backend={"adapter": "pathlib.Path"})
    with pytest.raises(MimirConfigurationError, match="not a MimirPort"):
        create_app(config)


def test_create_app_builds_the_named_backend(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        assert client.get("/mimir/health").status_code == 200
        assert client.get("/mimir/pages").json() == []


def test_settings_schema_reports_the_backend(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        fields = client.get("/settings").json()["sections"][0]["fields"]
    backend = next(field for field in fields if field["key"] == "backend")
    assert backend["value"] == BACKEND["adapter"]


def test_create_app_exposes_health_aliases(tmp_path: Path) -> None:
    config = _config(tmp_path, name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        root = client.get("/health")
        mounted = client.get("/mimir/health")
        api = client.get("/api/v1/mimir/health")

    assert root.status_code == 200
    assert mounted.status_code == 200
    assert api.status_code == 200
    assert root.json() == {"status": "healthy", "name": "shared", "role": "shared"}
    assert mounted.json() == root.json()
    assert api.json() == root.json()


def test_announce_url_unset_registers_local_mount(tmp_path: Path) -> None:
    config = _config(tmp_path, name="home", announce_url=None)
    create_app(config)

    store = MimirRegistryStore(tmp_path / "mimir" / ".mimir-registry.json")
    (entry,) = [e for e in store.list_entries() if e.name == "home"]
    assert entry.kind == "local"
    assert entry.url == ""


def test_announce_url_registers_remote_mount_at_that_url(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        name="shared",
        announce_url="https://mimir.example.test",
    )
    create_app(config)

    store = MimirRegistryStore(tmp_path / "mimir" / ".mimir-registry.json")
    (entry,) = [e for e in store.list_entries() if e.name == "shared"]
    assert entry.kind == "remote"
    assert entry.url == "https://mimir.example.test"


def test_announce_url_startup_does_not_reach_for_sleipnir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Replaces a test asserting a failed Sleipnir announce was swallowed.

    The lifespan imported ``_announce_mimir`` from Ravn's Sleipnir mesh, which
    does not exist, and logged the ImportError at debug. Every configured
    announce did nothing while reading as working, and nothing consumes a
    Mímir announce event, so the path is gone rather than repaired. Making the
    module unimportable proves startup no longer depends on it at all.
    """
    monkeypatch.setitem(sys.modules, "niuu.adapters.mesh.sleipnir_mesh", None)
    config = _config(tmp_path, announce_url="https://mimir.example.test")
    app = create_app(config)

    with caplog.at_level(logging.DEBUG, logger="mimir"), TestClient(app):
        pass

    # The message template, not the rendered text: tmp_path carries this test's
    # own name, so every record naming the store path would say "announce".
    assert not [r for r in caplog.records if "announce" in str(r.msg)]
