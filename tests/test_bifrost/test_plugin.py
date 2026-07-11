"""Tests for BifrostPlugin."""

from __future__ import annotations

from bifrost.plugin import BifrostPlugin
from niuu.ports.plugin import ServiceLifecycle

class TestBifrostPlugin:
    def test_name(self) -> None:
        plugin = BifrostPlugin()
        assert plugin.name == "bifrost"

    def test_description(self) -> None:
        plugin = BifrostPlugin()
        assert plugin.description

    def test_register_service_returns_definition(self) -> None:
        plugin = BifrostPlugin()
        svc_def = plugin.register_service()
        assert svc_def is not None
        assert svc_def.name == "bifrost"
        assert svc_def.default_port == 8082

    def test_service_is_host_mounted(self) -> None:
        plugin = BifrostPlugin()
        definition = plugin.register_service()
        assert definition.lifecycle is ServiceLifecycle.HOSTED
        assert definition.factory is None
        assert plugin.create_service() is None

    def test_create_api_app_returns_fastapi_app(self) -> None:
        from fastapi import FastAPI

        plugin = BifrostPlugin()
        app = plugin.create_api_app()
        assert isinstance(app, FastAPI)

    def test_depends_on_is_empty(self) -> None:
        plugin = BifrostPlugin()
        svc_def = plugin.register_service()
        assert svc_def.depends_on == []
