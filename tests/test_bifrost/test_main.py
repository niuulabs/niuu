"""Tests for the Bifröst __main__ entry point."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from bifrost.__main__ import _load_config, main
from bifrost.config import BifrostConfig


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path):
        config_data = {
            "bifrost": {
                "providers": {
                    "openai": {
                        "api_key_env": "OPENAI_API_KEY",
                        "models": ["gpt-4o"],
                    }
                },
                "aliases": {"fast": "gpt-4o"},
            }
        }
        cfg_file = tmp_path / "bifrost.yaml"
        cfg_file.write_text(yaml.dump(config_data))

        cfg = _load_config(str(cfg_file))
        assert isinstance(cfg, BifrostConfig)
        assert "openai" in cfg.providers
        assert cfg.aliases.get("fast") == "gpt-4o"

    def test_load_config_top_level_bifrost_key(self, tmp_path):
        config_data = {
            "bifrost": {
                "host": "127.0.0.1",
                "port": 9000,
            }
        }
        cfg_file = tmp_path / "bifrost.yaml"
        cfg_file.write_text(yaml.dump(config_data))

        cfg = _load_config(str(cfg_file))
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9000

    def test_load_config_root_level(self, tmp_path):
        config_data = {"host": "0.0.0.0", "port": 8088}
        cfg_file = tmp_path / "bifrost.yaml"
        cfg_file.write_text(yaml.dump(config_data))

        cfg = _load_config(str(cfg_file))
        assert cfg.port == 8088

    def test_missing_config_returns_defaults(self, tmp_path):
        cfg = _load_config(str(tmp_path / "nonexistent.yaml"))
        assert isinstance(cfg, BifrostConfig)
        assert cfg.port == 8088

    def test_empty_config_file(self, tmp_path):
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("")
        cfg = _load_config(str(cfg_file))
        assert isinstance(cfg, BifrostConfig)


class TestMain:
    def test_main_calls_uvicorn(self, tmp_path):
        cfg_file = tmp_path / "bifrost.yaml"
        cfg_file.write_text(yaml.dump({"port": 9999}))

        with (
            patch("uvicorn.run") as mock_run,
            patch("sys.argv", ["bifrost", "--config", str(cfg_file), "--port", "9999"]),
        ):
            main()
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs.kwargs.get("port") == 9999 or call_kwargs.args[1] == 9999

    def test_main_host_override(self, tmp_path):
        cfg_file = tmp_path / "bifrost.yaml"
        cfg_file.write_text("")

        with (
            patch("uvicorn.run") as mock_run,
            patch(
                "sys.argv",
                ["bifrost", "--config", str(cfg_file), "--host", "127.0.0.1"],
            ),
        ):
            main()
            mock_run.assert_called_once()


class TestGracefulShutdown:
    def test_uvicorn_is_told_how_long_in_flight_streams_may_finish(self, tmp_path):
        cfg_file = tmp_path / "bifrost.yaml"
        cfg_file.write_text(yaml.dump({"shutdown_grace_seconds": 240}))

        with (
            patch("uvicorn.run") as mock_run,
            patch("sys.argv", ["bifrost", "--config", str(cfg_file)]),
        ):
            main()

        assert mock_run.call_args.kwargs["timeout_graceful_shutdown"] == 240

    def test_the_default_waits_for_the_longest_request_envoy_allows(self, tmp_path):
        cfg_file = tmp_path / "bifrost.yaml"
        cfg_file.write_text("")

        with (
            patch("uvicorn.run") as mock_run,
            patch("sys.argv", ["bifrost", "--config", str(cfg_file)]),
        ):
            main()

        assert mock_run.call_args.kwargs["timeout_graceful_shutdown"] == 300

    def test_a_zero_grace_is_refused_rather_than_cutting_every_stream(self):
        with pytest.raises(ValidationError):
            BifrostConfig(shutdown_grace_seconds=0)
