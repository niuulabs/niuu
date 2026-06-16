"""Tests for the forge-independent catalog component.

Covers the guardrail that the catalog builder assembles from settings alone (no
DB, pod manager, gateway, or Bifrost) and serves the config-driven catalog routes
the full Forge mounts via ``build_catalog``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.catalog import build_catalog
from volundr.config import LaunchSpecConfig, SessionDefinitionConfig, Settings

CATALOG_PATHS = (
    "/api/v1/volundr/launch-specs",
    "/api/v1/volundr/session-definitions",
)


def _settings() -> Settings:
    """Settings with system launch specs + session definitions, no real DB."""
    return Settings(
        launch_specs=[
            LaunchSpecConfig(name="standard", description="Default", is_default=True),
            LaunchSpecConfig(name="heavy", model="claude-opus-4-6"),
        ],
        session_definitions={
            "skuldClaude": SessionDefinitionConfig(
                display_name="Claude Code",
                default_model="claude-sonnet-4-6",
                labels=["session"],
            ),
            "disabled": SessionDefinitionConfig(enabled=False),
        },
    )


@pytest.fixture
def settings() -> Settings:
    return _settings()


def _client(settings: Settings) -> TestClient:
    """Mount the catalog router on a bare app — no runtime dependencies."""
    app = FastAPI()
    app.include_router(build_catalog(settings).router)
    return TestClient(app)


def test_catalog_builds_and_serves(settings: Settings) -> None:
    """The catalog builds from settings alone and serves all catalog routes."""
    client = _client(settings)

    specs = client.get("/api/v1/volundr/launch-specs")
    assert specs.status_code == 200
    assert {s["name"] for s in specs.json()} == {s.name for s in settings.launch_specs}
    assert all(s["scope"] == "system" for s in specs.json())

    defs = client.get("/api/v1/volundr/session-definitions")
    assert defs.status_code == 200
    expected = {key for key, defn in settings.session_definitions.items() if defn.enabled}
    assert {d["key"] for d in defs.json()} == expected
    assert expected, "fixture should yield at least one enabled session definition"


def test_catalog_requires_no_database(settings: Settings) -> None:
    """Building and serving the catalog never touches the database."""
    client = _client(settings)
    assert client.get("/api/v1/volundr/launch-specs").status_code == 200
