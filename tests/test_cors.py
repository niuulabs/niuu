"""Tests for shared CORS configuration and service wiring."""

from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware

from cli.shared_host import create_app as create_niuu_app
from niuu.config import CorsConfig, GitConfig, NiuuHostConfig, NiuuSettings
from ting.config import Settings as TingSettings
from ting.main import create_app as create_ting_app
from volundr.config import Settings as NiuuSharedSettings
from volundr.config import Settings as VolundrSettings
from volundr.main import create_app as create_volundr_app


def _cors_options(app) -> dict[str, object]:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return middleware.kwargs
    raise AssertionError("CORSMiddleware was not configured")


class TestCorsConfig:
    def test_parses_csv_and_boolean_values(self) -> None:
        cors = CorsConfig(
            allowed_origins="https://old.example.com, https://new.example.com",
            allow_methods="GET,POST",
            allow_headers="Authorization,Content-Type",
            allow_credentials="false",
        )

        assert cors.allowed_origins == [
            "https://old.example.com",
            "https://new.example.com",
        ]
        assert cors.allow_methods == ["GET", "POST"]
        assert cors.allow_headers == ["Authorization", "Content-Type"]
        assert cors.allow_credentials is False

    def test_legacy_environment_aliases_are_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "https://one.example,https://two.example")
        monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "false")

        cors = CorsConfig()

        assert cors.allowed_origins == [
            "https://one.example",
            "https://two.example",
        ]
        assert cors.allow_credentials is False

    def test_malformed_legacy_boolean_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "sometimes")

        with pytest.raises(ValueError, match="allow_credentials"):
            CorsConfig()


class TestNiuuHostConfig:
    def test_bare_bind_host_does_not_decode_as_nested_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOST", "0.0.0.0")
        monkeypatch.setenv("NIUU_FORGE_STATE_FILE", "~/.niuu/pod-state.json")

        settings = NiuuSettings()

        assert settings.host.forge_state_file == "~/.niuu/pod-state.json"

    def test_legacy_environment_aliases_are_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIUU_CORS_ORIGINS", "https://ui.example")
        monkeypatch.setenv("NIUU_FORGE_STATE_FILE", "~/.niuu/custom-state.json")
        monkeypatch.setenv("NIUU_NO_WEB", "true")
        monkeypatch.setenv("NIUU_DATABASE_MODE", "external")
        monkeypatch.setenv("NIUU_PGDATA_DIR", "/var/lib/niuu")
        monkeypatch.setenv("DATABASE__HOST", "postgres")

        config = NiuuHostConfig()

        assert config.cors_origins == ["https://ui.example"]
        assert config.forge_state_file == "~/.niuu/custom-state.json"
        assert config.no_web is True
        assert config.database_mode == "external"
        assert config.pgdata_dir == "/var/lib/niuu"
        assert config.external_database_host == "postgres"

    def test_blank_legacy_database_mode_preserves_auto_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NIUU_DATABASE_MODE", "")

        assert NiuuHostConfig().database_mode == "auto"

    def test_invalid_database_mode_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIUU_DATABASE_MODE", "sometimes")

        with pytest.raises(ValueError, match="database_mode"):
            NiuuHostConfig()


class TestServiceCorsWiring:
    def test_volundr_uses_settings_cors(self) -> None:
        app = create_volundr_app(
            VolundrSettings(
                cors=CorsConfig(
                    allowed_origins=["https://ui.example.com"],
                    allow_credentials=False,
                )
            )
        )

        options = _cors_options(app)
        assert options["allow_origins"] == ["https://ui.example.com"]
        assert options["allow_credentials"] is False

    def test_ting_uses_settings_cors(self) -> None:
        app = create_ting_app(
            TingSettings(
                cors=CorsConfig(
                    allowed_origins=["https://ui.example.com"],
                    allow_credentials=False,
                )
            )
        )

        options = _cors_options(app)
        assert options["allow_origins"] == ["https://ui.example.com"]
        assert options["allow_credentials"] is False

    def test_niuu_shared_uses_settings_cors(self) -> None:
        app = create_niuu_app(
            git_config=GitConfig(),
            settings=NiuuSharedSettings(
                cors=CorsConfig(
                    allowed_origins=["https://ui.example.com"],
                    allow_credentials=False,
                )
            ),
        )

        options = _cors_options(app)
        assert options["allow_origins"] == ["https://ui.example.com"]
        assert options["allow_credentials"] is False
