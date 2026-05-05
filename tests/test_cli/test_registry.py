"""Tests for cli.registry — PluginRegistry, ServicePlugin, Service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cli.registry import PluginRegistry
from tests.test_cli.conftest import FakePlugin, StubService


class TestPluginRegistry:
    """Tests for PluginRegistry."""

    def test_register_plugin(self, registry: PluginRegistry) -> None:
        plugin = FakePlugin(name="test")
        registry.register(plugin)
        assert "test" in registry.plugins
        assert registry.get("test") is plugin

    def test_register_multiple_plugins(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b"))
        assert len(registry.plugins) == 2

    def test_disable_plugin(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="test"))
        registry.disable("test")
        assert "test" not in registry.plugins
        assert registry.get("test") is None

    def test_enable_plugin(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="test"))
        registry.disable("test")
        registry.enable("test")
        assert "test" in registry.plugins

    def test_is_enabled(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="test"))
        assert registry.is_enabled("test") is True
        registry.disable("test")
        assert registry.is_enabled("test") is False

    def test_is_enabled_unregistered(self, registry: PluginRegistry) -> None:
        assert registry.is_enabled("nonexistent") is False

    def test_all_plugins_includes_disabled(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b"))
        registry.disable("b")
        assert len(registry.all_plugins) == 2
        assert len(registry.plugins) == 1

    def test_apply_config_disables(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b"))
        registry.apply_config({"b": False})
        assert registry.is_enabled("a") is True
        assert registry.is_enabled("b") is False

    def test_apply_config_enables(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="a"))
        registry.disable("a")
        registry.apply_config({"a": True})
        assert registry.is_enabled("a") is True

    def test_apply_config_none(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="a"))
        registry.apply_config(None)
        assert registry.is_enabled("a") is True

    def test_discover_entry_points(self, registry: PluginRegistry) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "test"
        mock_ep.load.return_value = FakePlugin

        mock_eps = MagicMock()
        mock_eps.select.return_value = [mock_ep]

        with patch("cli.registry.importlib.metadata.entry_points", return_value=mock_eps):
            registry.discover_entry_points()

        assert "fake" in registry.plugins

    def test_discover_entry_points_handles_error(self, registry: PluginRegistry) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("nope")

        mock_eps = MagicMock()
        mock_eps.select.return_value = [mock_ep]

        with patch("cli.registry.importlib.metadata.entry_points", return_value=mock_eps):
            registry.discover_entry_points()

        assert len(registry.plugins) == 0

    def test_discover_config(self, registry: PluginRegistry) -> None:
        config = [
            {"adapter": "tests.test_cli.conftest.FakePlugin", "name": "configured"},
        ]
        registry.discover_config(config)
        assert "configured" in registry.plugins

    def test_discover_config_missing_adapter(self, registry: PluginRegistry) -> None:
        config = [{"name": "no_adapter"}]
        registry.discover_config(config)
        assert len(registry.plugins) == 0

    def test_discover_config_bad_class(self, registry: PluginRegistry) -> None:
        config = [{"adapter": "nonexistent.module.Class"}]
        registry.discover_config(config)
        assert len(registry.plugins) == 0

    def test_enable_idempotent(self, registry: PluginRegistry) -> None:
        registry.register(FakePlugin(name="a"))
        registry.enable("a")
        registry.enable("a")
        assert registry.is_enabled("a") is True


class TestServicePlugin:
    """Tests for ServicePlugin ABC defaults."""

    def test_default_create_service(self) -> None:
        plugin = FakePlugin()
        assert plugin.create_service() is None

    def test_default_create_api_client(self) -> None:
        plugin = FakePlugin()
        assert plugin.create_api_client() is None

    def test_default_tui_pages(self) -> None:
        plugin = FakePlugin()
        assert plugin.tui_pages() == []

    def test_default_depends_on(self) -> None:
        plugin = FakePlugin()
        assert plugin.depends_on() == []

    def test_default_api_route_domains(self) -> None:
        plugin = FakePlugin()
        assert plugin.api_route_domains() == []

    def test_custom_deps(self) -> None:
        plugin = FakePlugin(deps=["a", "b"])
        assert list(plugin.depends_on()) == ["a", "b"]


class TestService:
    """Tests for Service ABC via StubService."""

    async def test_start(self, stub_service: StubService) -> None:
        await stub_service.start()
        assert stub_service.started is True

    async def test_stop(self, stub_service: StubService) -> None:
        await stub_service.stop()
        assert stub_service.stopped is True

    async def test_health_check(self, stub_service: StubService) -> None:
        assert await stub_service.health_check() is True
        assert stub_service.health_check_count == 1

    async def test_unhealthy(self) -> None:
        svc = StubService(healthy=False)
        assert await svc.health_check() is False
