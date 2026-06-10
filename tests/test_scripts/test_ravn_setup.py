"""Smoke tests for the ravn-setup helper script."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from ravn.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
RAVN_SETUP = REPO_ROOT / "scripts" / "setups" / "ravn-setup"


def test_environment_nats_profile_is_listed() -> None:
    result = subprocess.run(
        [str(RAVN_SETUP), "list"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "environment-nats" in result.stdout
    assert "Environment-resident Valkyries over NATS JetStream" in result.stdout


def test_environment_nats_profile_generates_config(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(RAVN_SETUP), "generate", "environment-nats", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    generated = tmp_path / "config.yaml"
    assert "Written:" in result.stdout
    assert generated.exists()

    config = generated.read_text(encoding="utf-8")
    assert "environment:" in config
    assert "signal_sources:" in config
    assert "adapter: nats" in config
    assert "servers_env: NATS_URL" in config
    assert "stream_name: ravn_environment" in config
    assert "subject_prefix: ravn.environment" in config

    settings = Settings.model_validate(yaml.safe_load(config))
    assert settings.environment.id == "local-dev"
    assert settings.environment.type == "local"
    assert [source.id for source in settings.environment.signal_sources] == [
        "kubernetes-events",
        "host-events",
        "printer-telemetry",
        "operator-events",
    ]
    assert settings.mesh.adapter == "nats"
    assert settings.mesh.nats.servers_env == "NATS_URL"
    assert settings.mesh.nats.stream_name == "ravn_environment"
    assert settings.mesh.nats.subject_prefix == "ravn.environment"
