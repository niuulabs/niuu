"""Tests for MimirServiceConfig sourcing (constructor > env > YAML)."""

from __future__ import annotations

from pathlib import Path

import mimir.config as mimir_config


def test_defaults() -> None:
    config = mimir_config.MimirServiceConfig()
    assert config.path == "~/.ravn/mimir"
    assert config.port == 7477
    assert config.eval_capture is True
    assert config.backend is None


def test_env_overrides_with_nesting(monkeypatch) -> None:
    monkeypatch.setenv("MIMIR__EVAL_CAPTURE", "false")
    monkeypatch.setenv(
        "MIMIR__BACKEND",
        '{"adapter": "mimir.adapters.gbrain.GBrainMimirAdapter", "mcp_url": "http://b.test/mcp"}',
    )
    monkeypatch.setenv("MIMIR__LIVE_ACTIVITY__BUFFER_SIZE", "30")

    config = mimir_config.MimirServiceConfig()
    assert config.eval_capture is False
    assert config.backend == {
        "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
        "mcp_url": "http://b.test/mcp",
    }
    assert config.live_activity.buffer_size == 30


def test_constructor_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("MIMIR__PORT", "9999")
    assert mimir_config.MimirServiceConfig(port=1234).port == 1234


def test_yaml_file_source(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "mimir.yaml"
    config_file.write_text(
        "port: 8123\nbackend:\n  adapter: mimir.adapters.gbrain.GBrainMimirAdapter\n"
        "  mcp_url: http://b.test/mcp\nlive_activity:\n  buffer_size: 8\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MIMIR_CONFIG", str(config_file))
    # CONFIG_PATHS is resolved at import time from MIMIR_CONFIG; re-resolve
    # the yaml source for this test by rebuilding the class-level paths.
    import importlib

    importlib.reload(mimir_config)
    try:
        config = mimir_config.MimirServiceConfig()
        assert config.port == 8123
        assert config.backend == {
            "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
            "mcp_url": "http://b.test/mcp",
        }
        assert config.live_activity.buffer_size == 8
        # env beats yaml
        monkeypatch.setenv("MIMIR__PORT", "8200")
        assert mimir_config.MimirServiceConfig().port == 8200
    finally:
        monkeypatch.delenv("MIMIR_CONFIG")
        monkeypatch.delenv("MIMIR__PORT", raising=False)
        importlib.reload(mimir_config)
