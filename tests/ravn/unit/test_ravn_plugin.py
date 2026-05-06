"""Unit tests for RavnPlugin and _RavnService."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer


class TestRavnService:
    @pytest.mark.asyncio
    async def test_start_is_noop(self) -> None:
        from ravn.plugin import _RavnService

        svc = _RavnService()
        await svc.start()  # Must not raise

    @pytest.mark.asyncio
    async def test_stop_is_noop(self) -> None:
        from ravn.plugin import _RavnService

        svc = _RavnService()
        await svc.stop()  # Must not raise

    @pytest.mark.asyncio
    async def test_health_check_returns_true(self) -> None:
        from ravn.plugin import _RavnService

        svc = _RavnService()
        assert await svc.health_check() is True


class TestRavnPlugin:
    def test_name_property(self) -> None:
        from ravn.plugin import RavnPlugin

        plugin = RavnPlugin()
        assert plugin.name == "ravn"

    def test_description_property(self) -> None:
        from ravn.plugin import RavnPlugin

        plugin = RavnPlugin()
        assert isinstance(plugin.description, str)
        assert len(plugin.description) > 0

    def test_depends_on_returns_empty(self) -> None:
        from ravn.plugin import RavnPlugin

        plugin = RavnPlugin()
        assert list(plugin.depends_on()) == []

    def test_register_service_returns_definition(self) -> None:
        from ravn.plugin import RavnPlugin

        plugin = RavnPlugin()
        defn = plugin.register_service()
        assert defn.name == "ravn"
        assert defn.default_enabled is True

    def test_create_service_returns_ravn_service(self) -> None:
        from ravn.plugin import RavnPlugin, _RavnService

        plugin = RavnPlugin()
        svc = plugin.create_service()
        assert isinstance(svc, _RavnService)

    def test_create_api_client_returns_client(self) -> None:
        from ravn.plugin import RavnPlugin

        plugin = RavnPlugin()
        client = plugin.create_api_client()
        assert client is not None

    def test_create_api_app_calls_create_app(self) -> None:
        from ravn.plugin import RavnPlugin

        plugin = RavnPlugin()
        mock_app = MagicMock()
        with patch("ravn.plugin.RavnPlugin.create_api_app", return_value=mock_app):
            result = plugin.create_api_app()
        assert result is mock_app

    def test_register_commands_adds_ravn_group(self) -> None:
        from ravn.plugin import RavnPlugin

        plugin = RavnPlugin()
        mock_app = MagicMock(spec=typer.Typer)
        plugin.register_commands(mock_app)
        mock_app.add_typer.assert_called_once()
        _, kwargs = mock_app.add_typer.call_args
        assert kwargs.get("name") == "ravn"
