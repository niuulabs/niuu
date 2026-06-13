"""Tests for MimirServiceConfig sourcing (constructor > env > YAML)."""

from __future__ import annotations

from pathlib import Path

from mimir.config import MimirServiceConfig


def test_defaults() -> None:
    config = MimirServiceConfig()
    assert config.path == "~/.ravn/mimir"
    assert config.port == 7477
    assert config.eval_capture is True
    assert config.ranking.enabled is True
    assert config.evidence.consolidate_on_ingest is True


def test_env_overrides_with_nesting(monkeypatch) -> None:
    monkeypatch.setenv("MIMIR__EVAL_CAPTURE", "false")
    monkeypatch.setenv("MIMIR__EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    monkeypatch.setenv("MIMIR__RANKING__TITLE_MATCH_BOOST", "1.5")
    monkeypatch.setenv("MIMIR__EVIDENCE__STALE_AFTER_DAYS", "30")

    config = MimirServiceConfig()
    assert config.eval_capture is False
    assert config.embedding_model == "all-MiniLM-L6-v2"
    assert config.ranking.title_match_boost == 1.5
    assert config.evidence.stale_after_days == 30


def test_constructor_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("MIMIR__PORT", "9999")
    assert MimirServiceConfig(port=1234).port == 1234


def test_yaml_file_source(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "mimir.yaml"
    config_file.write_text(
        "port: 8123\nembedding_model: all-MiniLM-L6-v2\nranking:\n  overfetch_factor: 8\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MIMIR_CONFIG", str(config_file))
    # CONFIG_PATHS is resolved at import time from MIMIR_CONFIG; re-resolve
    # the yaml source for this test by rebuilding the class-level paths.
    import importlib

    import mimir.config as mimir_config

    importlib.reload(mimir_config)
    try:
        config = mimir_config.MimirServiceConfig()
        assert config.port == 8123
        assert config.embedding_model == "all-MiniLM-L6-v2"
        assert config.ranking.overfetch_factor == 8
        # env beats yaml
        monkeypatch.setenv("MIMIR__PORT", "8200")
        assert mimir_config.MimirServiceConfig().port == 8200
    finally:
        monkeypatch.delenv("MIMIR_CONFIG")
        monkeypatch.delenv("MIMIR__PORT", raising=False)
        importlib.reload(mimir_config)
