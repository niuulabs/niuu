"""Tests for dynamic persona_source wiring in Volundr (NIU-639).

Verifies that volundr's persona CRUD endpoints use the adapter class
specified in ``settings.ravn.persona_source.adapter`` rather than
hardcoding FilesystemPersonaAdapter.
"""

from __future__ import annotations

from ravn.adapters.personas.loader import PersonaConfig
from ravn.ports.persona import PersonaRegistryPort
from volundr.config import PersonaSourceConfig, RavnConfig

# ---------------------------------------------------------------------------
# Recording stub adapter
# ---------------------------------------------------------------------------


class StubPersonaRegistry(PersonaRegistryPort):
    """Minimal persona registry stub for wiring tests."""

    def __init__(self, **kwargs: object) -> None:
        self.init_kwargs = kwargs

    def load(self, name: str) -> PersonaConfig | None:
        if name == "stub-persona":
            return PersonaConfig(name="stub-persona", system_prompt_template="stub prompt")
        return None

    def list_names(self) -> list[str]:
        return ["stub-persona"]

    def save(self, config: PersonaConfig) -> None:
        pass

    def delete(self, name: str) -> bool:
        return False

    def is_builtin(self, name: str) -> bool:
        return False

    def load_all(self) -> list[PersonaConfig]:
        return [PersonaConfig(name="stub-persona", system_prompt_template="stub prompt")]

    def source(self, name: str) -> str:
        return "[stub]"


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestVolundrPersonaSourceConfig:
    def test_default_adapter_is_filesystem(self) -> None:
        cfg = PersonaSourceConfig()
        assert cfg.adapter == "ravn.adapters.personas.loader.FilesystemPersonaAdapter"

    def test_custom_adapter(self) -> None:
        cfg = PersonaSourceConfig(
            adapter="tests.test_volundr.test_persona_source_wiring.StubPersonaRegistry",
            kwargs={"custom": "value"},
        )
        assert "StubPersonaRegistry" in cfg.adapter
        assert cfg.kwargs["custom"] == "value"

    def test_ravn_config_has_persona_source(self) -> None:
        ravn = RavnConfig()
        assert hasattr(ravn, "persona_source")
        assert isinstance(ravn.persona_source, PersonaSourceConfig)


# ---------------------------------------------------------------------------
# Dynamic wiring
# ---------------------------------------------------------------------------


class TestVolundrDynamicWiring:
    def test_import_class_instantiates_stub(self) -> None:
        from niuu.utils import import_class, resolve_secret_kwargs

        cfg = PersonaSourceConfig(
            adapter="tests.test_volundr.test_persona_source_wiring.StubPersonaRegistry",
            kwargs={"region": "us-east"},
        )
        cls = import_class(cfg.adapter)
        kwargs = resolve_secret_kwargs(cfg.kwargs, cfg.secret_kwargs_env)
        instance = cls(**kwargs)

        assert isinstance(instance, StubPersonaRegistry)
        assert instance.init_kwargs["region"] == "us-east"

    def test_stub_satisfies_registry_port(self) -> None:
        adapter = StubPersonaRegistry()
        assert isinstance(adapter, PersonaRegistryPort)
        assert adapter.load("stub-persona") is not None
        assert adapter.load("missing") is None

    def test_stub_works_with_personas_router(self) -> None:
        """The REST router accepts any PersonaRegistryPort implementation."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from ravn.api.personas import create_personas_router

        adapter = StubPersonaRegistry()
        app = FastAPI()
        app.include_router(create_personas_router(adapter))
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.get("/api/v1/ravn/personas")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert "stub-persona" in names

    def test_stub_detail_endpoint(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from ravn.api.personas import create_personas_router

        adapter = StubPersonaRegistry()
        app = FastAPI()
        app.include_router(create_personas_router(adapter))
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.get("/api/v1/ravn/personas/stub-persona")
        assert resp.status_code == 200
        assert resp.json()["name"] == "stub-persona"
