"""Skuld broker integration tests for the Retrieval Reflex (NIU-1059)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ravn.reflex import POINTER_BLOCK_HEADER, ReflexEntity, ReflexInjector
from skuld.broker import Broker
from skuld.config import ReflexConfig, SkuldSettings


@pytest.fixture(autouse=True)
def _no_yaml_config(monkeypatch):
    """Disable YAML config file loading so real files on disk don't interfere."""
    monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])


def _settings(tmp_path, **reflex_kwargs) -> SkuldSettings:
    return SkuldSettings(
        session={"id": "reflex-session", "workspace_dir": str(tmp_path)},
        transport="subprocess",
        reflex=reflex_kwargs,
    )


def _injector(feed: list[ReflexEntity]) -> ReflexInjector:
    async def fetch():
        return feed

    return ReflexInjector(fetch_entities=fetch, max_pointers=5, cache_ttl_seconds=300.0)


_FEED = [
    ReflexEntity(
        slug="volundr-auth",
        title="Volundr Auth",
        type="decision",
        confidence="high",
        updated_at="2026-05-20T10:00:00Z",
        path="wiki/technical/volundr/auth.md",
    )
]


class TestReflexConfig:
    def test_defaults_are_on(self):
        config = ReflexConfig()
        assert config.enabled is True
        assert config.base_url == ""
        assert config.max_pointers == 5
        assert config.cache_ttl_seconds == 300
        assert config.timeout_seconds == 5.0

    def test_settings_default_on(self, tmp_path):
        settings = _settings(tmp_path)
        assert settings.reflex.enabled is True


class TestBrokerReflex:
    async def test_disabled_reflex_leaves_message_unchanged(self, tmp_path):
        broker = Broker(settings=_settings(tmp_path))
        result = await broker._apply_retrieval_reflex("Check Volundr Auth")
        assert result == "Check Volundr Auth"
        assert broker._reflex_injector is None
        assert broker._reflex_initialised is True

    async def test_enabled_reflex_builds_injector_from_settings(self, tmp_path):
        broker = Broker(settings=_settings(tmp_path, enabled=True, base_url="http://mimir.test"))
        injector = broker._retrieval_reflex_injector()
        assert isinstance(injector, ReflexInjector)
        assert broker._retrieval_reflex_injector() is injector

    async def test_enabled_without_base_url_disables_with_warning(self, tmp_path, caplog):
        broker = Broker(settings=_settings(tmp_path, enabled=True))
        with caplog.at_level(logging.WARNING, logger="ravn.reflex"):
            assert broker._retrieval_reflex_injector() is None
        assert any("no Mimir HTTP endpoint" in r.getMessage() for r in caplog.records)

    async def test_base_url_derived_from_volundr_api_url(self, tmp_path):
        from ravn.reflex import _resolve_base_url

        settings = _settings(tmp_path, enabled=True)
        settings.volundr_api_url = "http://platform:8080"
        assert _resolve_base_url(settings, settings.reflex) == "http://platform:8080/api/v1"

    async def test_enabled_reflex_prefixes_pointer_block(self, tmp_path):
        broker = Broker(settings=_settings(tmp_path))
        broker._reflex_injector = _injector(_FEED)
        broker._reflex_initialised = True

        result = await broker._apply_retrieval_reflex("Check Volundr Auth please")

        assert result.startswith(POINTER_BLOCK_HEADER)
        assert "[[volundr-auth]]" in result
        assert result.endswith("Check Volundr Auth please")

    async def test_same_slug_injected_once_per_session(self, tmp_path):
        broker = Broker(settings=_settings(tmp_path))
        broker._reflex_injector = _injector(_FEED)
        broker._reflex_initialised = True

        first = await broker._apply_retrieval_reflex("Check Volundr Auth")
        second = await broker._apply_retrieval_reflex("Volundr Auth again")

        assert POINTER_BLOCK_HEADER in first
        assert POINTER_BLOCK_HEADER not in second

    async def test_feed_error_fails_open_and_logs(self, tmp_path, caplog):
        async def fetch():
            raise httpx.ConnectError("mimir down")

        broker = Broker(settings=_settings(tmp_path))
        broker._reflex_injector = ReflexInjector(
            fetch_entities=fetch, max_pointers=5, cache_ttl_seconds=300.0
        )
        broker._reflex_initialised = True

        with caplog.at_level(logging.ERROR, logger="ravn.reflex"):
            result = await broker._apply_retrieval_reflex("Check Volundr Auth")

        assert result == "Check Volundr Auth"
        assert any("entity feed unreachable" in r.getMessage() for r in caplog.records)

    async def test_reflex_crash_never_breaks_forwarding(self, tmp_path, caplog):
        broker = Broker(settings=_settings(tmp_path))
        broken = MagicMock()
        broken.apply = AsyncMock(side_effect=RuntimeError("boom"))
        broker._reflex_injector = broken
        broker._reflex_initialised = True

        with caplog.at_level(logging.ERROR, logger="skuld.broker"):
            result = await broker._apply_retrieval_reflex("hello")

        assert result == "hello"
        assert any("retrieval reflex failed" in r.getMessage() for r in caplog.records)

    async def test_safe_transport_send_forwards_injected_message(self, tmp_path):
        broker = Broker(settings=_settings(tmp_path))
        broker._reflex_injector = _injector(_FEED)
        broker._reflex_initialised = True
        transport = MagicMock()
        transport.send_message = AsyncMock()

        await broker._safe_transport_send(transport, "Check Volundr Auth")

        sent = transport.send_message.await_args.args[0]
        assert sent.startswith(POINTER_BLOCK_HEADER)
        assert sent.endswith("Check Volundr Auth")

    async def test_safe_transport_send_without_reflex_sends_original(self, tmp_path):
        broker = Broker(settings=_settings(tmp_path))
        transport = MagicMock()
        transport.send_message = AsyncMock()

        await broker._safe_transport_send(transport, "plain message")

        transport.send_message.assert_awaited_once_with("plain message")
