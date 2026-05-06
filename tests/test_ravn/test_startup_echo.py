"""Tests for ravn daemon startup config echo (NIU-635)."""

from __future__ import annotations

import logging
import os
from collections.abc import Coroutine
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ravn.cli.commands import _log_effective_config, app
from ravn.config import Settings

runner = CliRunner()


@pytest.fixture
def _clean_env(monkeypatch):
    """Remove RAVN_ env vars that interfere with Settings() defaults."""
    for key in list(os.environ):
        if key.startswith("RAVN_"):
            monkeypatch.delenv(key, raising=False)


class TestStartupEcho:
    @pytest.mark.usefixtures("_clean_env")
    def test_log_contains_required_fields(self, caplog):
        """The startup log line must contain persona, llm_alias, thinking, budget, source."""
        settings = Settings()
        with caplog.at_level(logging.INFO, logger="ravn.cli.commands"):
            _log_effective_config(settings)

        assert len(caplog.records) == 1
        msg = caplog.records[0].message
        assert "persona=" in msg
        assert "llm_alias=" in msg
        assert "thinking=" in msg
        assert "budget=" in msg
        assert "source=" in msg

    @pytest.mark.usefixtures("_clean_env")
    def test_source_reflects_ravn_config_env(self, caplog, monkeypatch):
        """When RAVN_CONFIG is set, source= shows that path."""
        monkeypatch.setenv("RAVN_CONFIG", "/etc/ravn/config.yaml")
        settings = Settings()
        with caplog.at_level(logging.INFO, logger="ravn.cli.commands"):
            _log_effective_config(settings)

        assert "source=/etc/ravn/config.yaml" in caplog.records[0].message

    @pytest.mark.usefixtures("_clean_env")
    def test_source_defaults_when_no_env(self, caplog):
        """Without RAVN_CONFIG, source=defaults."""
        settings = Settings()
        with caplog.at_level(logging.INFO, logger="ravn.cli.commands"):
            _log_effective_config(settings)

        assert "source=defaults" in caplog.records[0].message

    @pytest.mark.usefixtures("_clean_env")
    def test_persona_from_env(self, caplog, monkeypatch):
        """RAVN_PERSONA env var is reflected in the log."""
        monkeypatch.setenv("RAVN_PERSONA", "coordinator")
        settings = Settings()
        with caplog.at_level(logging.INFO, logger="ravn.cli.commands"):
            _log_effective_config(settings)

        assert "persona=coordinator" in caplog.records[0].message

    @pytest.mark.usefixtures("_clean_env")
    def test_default_persona(self, caplog):
        """Without RAVN_PERSONA, persona=default."""
        settings = Settings()
        with caplog.at_level(logging.INFO, logger="ravn.cli.commands"):
            _log_effective_config(settings)

        assert "persona=default" in caplog.records[0].message

    @pytest.mark.usefixtures("_clean_env")
    def test_llm_alias_from_settings(self, caplog):
        """llm_alias reflects the effective model from settings."""
        settings = Settings()
        with caplog.at_level(logging.INFO, logger="ravn.cli.commands"):
            _log_effective_config(settings)

        expected = settings.effective_model()
        assert f"llm_alias={expected}" in caplog.records[0].message

    @pytest.mark.usefixtures("_clean_env")
    def test_thinking_and_budget_from_settings(self, caplog):
        """thinking and budget reflect extended_thinking config."""
        settings = Settings()
        with caplog.at_level(logging.INFO, logger="ravn.cli.commands"):
            _log_effective_config(settings)

        msg = caplog.records[0].message
        thinking = settings.llm.extended_thinking.enabled
        budget = settings.llm.extended_thinking.budget_tokens
        assert f"thinking={thinking}" in msg
        assert f"budget={budget}" in msg


class TestDaemonCallsConfigEcho:
    """Verify daemon and listen commands call _log_effective_config."""

    @staticmethod
    def _close_run_coroutine(coro: Coroutine) -> None:
        coro.close()

    @pytest.mark.usefixtures("_clean_env")
    def test_daemon_emits_config_log(self):
        with (
            patch("ravn.cli.commands._log_effective_config") as mock_log,
            patch(
                "ravn.cli.commands.asyncio.run",
                side_effect=self._close_run_coroutine,
            ),
        ):
            runner.invoke(app, ["daemon"])

        mock_log.assert_called_once()

    @pytest.mark.usefixtures("_clean_env")
    def test_listen_emits_config_log(self):
        with (
            patch("ravn.cli.commands._log_effective_config") as mock_log,
            patch(
                "ravn.cli.commands.asyncio.run",
                side_effect=self._close_run_coroutine,
            ),
        ):
            runner.invoke(app, ["listen"])

        mock_log.assert_called_once()
