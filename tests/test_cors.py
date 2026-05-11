"""Tests for shared CORS configuration and service wiring."""

from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware

from niuu.config import CorsConfig, GitConfig
from niuu.main import create_app as create_niuu_app
from niuu.service_settings import Settings as NiuuSharedSettings
from tyr.config import Settings as TyrSettings
from tyr.main import create_app as create_tyr_app
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

    def test_tyr_uses_settings_cors(self) -> None:
        app = create_tyr_app(
            TyrSettings(
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
