"""Tests for dynamic persona_source wiring (NIU-639).

Verifies that the full boot sequence respects ``persona_source.adapter``
and instantiates the configured adapter class rather than hardcoding
FilesystemPersonaAdapter.
"""

from __future__ import annotations

from ravn.adapters.personas.loader import PersonaConfig
from ravn.config import PersonaSourceConfig, Settings
from ravn.ports.persona import PersonaRegistryPort

# ---------------------------------------------------------------------------
# Recording stub adapter
# ---------------------------------------------------------------------------


class RecordingPersonaAdapter(PersonaRegistryPort):
    """Minimal stub that records calls instead of hitting the filesystem."""

    def __init__(self, **kwargs: object) -> None:
        self.init_kwargs = kwargs
        self.load_calls: list[str] = []

    def load(self, name: str) -> PersonaConfig | None:
        self.load_calls.append(name)
        if name == "stub-persona":
            return PersonaConfig(name="stub-persona", system_prompt_template="stub")
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
        return [PersonaConfig(name="stub-persona", system_prompt_template="stub")]

    def source(self, name: str) -> str:
        return "[stub]"


# ---------------------------------------------------------------------------
# PersonaSourceConfig
# ---------------------------------------------------------------------------


class TestPersonaSourceConfig:
    def test_default_adapter_is_filesystem(self) -> None:
        cfg = PersonaSourceConfig()
        assert cfg.adapter == "ravn.adapters.personas.loader.FilesystemPersonaAdapter"

    def test_custom_adapter_class_path(self) -> None:
        cfg = PersonaSourceConfig(
            adapter="tests.test_ravn.test_persona_source_wiring.RecordingPersonaAdapter",
        )
        assert "RecordingPersonaAdapter" in cfg.adapter

    def test_kwargs_forwarded(self) -> None:
        cfg = PersonaSourceConfig(
            adapter="tests.test_ravn.test_persona_source_wiring.RecordingPersonaAdapter",
            kwargs={"persona_dirs": ["/tmp/test"]},
        )
        assert cfg.kwargs == {"persona_dirs": ["/tmp/test"]}


# ---------------------------------------------------------------------------
# Dynamic wiring via import_class
# ---------------------------------------------------------------------------


class TestDynamicWiring:
    def test_import_class_instantiates_stub(self) -> None:
        """import_class resolves the stub adapter and kwargs are forwarded."""
        from niuu.utils import import_class, resolve_secret_kwargs

        cfg = PersonaSourceConfig(
            adapter="tests.test_ravn.test_persona_source_wiring.RecordingPersonaAdapter",
            kwargs={"custom_key": "custom_value"},
        )
        cls = import_class(cfg.adapter)
        kwargs = resolve_secret_kwargs(cfg.kwargs, cfg.secret_kwargs_env)
        instance = cls(**kwargs)

        assert isinstance(instance, RecordingPersonaAdapter)
        assert instance.init_kwargs["custom_key"] == "custom_value"

    def test_stub_satisfies_port_interface(self) -> None:
        adapter = RecordingPersonaAdapter()
        assert isinstance(adapter, PersonaRegistryPort)
        assert adapter.load("stub-persona") is not None
        assert adapter.load("missing") is None
        assert adapter.list_names() == ["stub-persona"]
        assert adapter.source("x") == "[stub]"


# ---------------------------------------------------------------------------
# _resolve_persona integration
# ---------------------------------------------------------------------------


class TestResolvePersonaWiring:
    def test_resolve_persona_uses_settings_adapter(self) -> None:
        """_resolve_persona with custom adapter in settings loads from the stub."""
        from ravn.cli.commands import _resolve_persona

        settings = Settings(
            persona_source=PersonaSourceConfig(
                adapter="tests.test_ravn.test_persona_source_wiring.RecordingPersonaAdapter",
            ),
        )
        result = _resolve_persona("stub-persona", None, settings=settings)
        assert result is not None
        assert result.name == "stub-persona"

    def test_resolve_persona_unknown_returns_none(self) -> None:
        from ravn.cli.commands import _resolve_persona

        settings = Settings(
            persona_source=PersonaSourceConfig(
                adapter="tests.test_ravn.test_persona_source_wiring.RecordingPersonaAdapter",
            ),
        )
        result = _resolve_persona("nonexistent", None, settings=settings)
        assert result is None

    def test_resolve_persona_no_settings_falls_back_to_filesystem(self) -> None:
        """Without settings, _resolve_persona uses default FilesystemPersonaAdapter."""
        from ravn.cli.commands import _resolve_persona

        result = _resolve_persona("coding-agent", None)
        assert result is not None
        assert result.name == "coding-agent"


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------


class TestSettingsPersonaSource:
    def test_settings_default_has_persona_source(self) -> None:
        settings = Settings()
        assert hasattr(settings, "persona_source")
        assert settings.persona_source.adapter == (
            "ravn.adapters.personas.loader.FilesystemPersonaAdapter"
        )

    def test_settings_custom_persona_source(self) -> None:
        settings = Settings(
            persona_source=PersonaSourceConfig(
                adapter="tests.test_ravn.test_persona_source_wiring.RecordingPersonaAdapter",
                kwargs={"foo": "bar"},
            ),
        )
        assert "RecordingPersonaAdapter" in settings.persona_source.adapter
        assert settings.persona_source.kwargs == {"foo": "bar"}
