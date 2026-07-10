"""Resident runtime profile configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from volundr.config import ResidentRuntimesConfig, Settings


def test_settings_parse_resident_profiles() -> None:
    settings = Settings.model_validate(
        {
            "resident_runtimes": {
                "profiles": [
                    {
                        "id": "ravn-helm",
                        "display_name": "Resident Ravn",
                        "backend": "helmrelease",
                        "engine": "ravn",
                        "capabilities": ["chat", "runtime.suspend"],
                    }
                ]
            }
        }
    )

    profile = settings.resident_runtimes.profiles[0]
    assert profile.id == "ravn-helm"
    assert profile.backend.value == "helmrelease"
    assert [capability.value for capability in profile.capabilities] == [
        "chat",
        "runtime.suspend",
    ]


def test_duplicate_profile_ids_fail_configuration() -> None:
    with pytest.raises(ValidationError, match="ids must be unique"):
        ResidentRuntimesConfig.model_validate(
            {
                "profiles": [
                    {
                        "id": "same",
                        "display_name": "First",
                        "backend": "openshell",
                        "engine": "ravn",
                    },
                    {
                        "id": "same",
                        "display_name": "Second",
                        "backend": "helmrelease",
                        "engine": "ravn",
                    },
                ]
            }
        )


def test_chart_contains_profile_config_and_dual_migration() -> None:
    root = Path(__file__).resolve().parents[2]
    configmap = (root / "charts/volundr/templates/configmap.yaml").read_text()
    migrations = (root / "charts/volundr/templates/migrations-configmap.yaml").read_text()
    migration = (root / "migrations/000055_resident_runtimes.up.sql").read_text()

    assert "resident_runtimes:" in configmap
    assert "residentRuntimeProfiles" in configmap
    assert "000055_resident_runtimes.up.sql" in migrations
    assert "CREATE TABLE IF NOT EXISTS resident_runtimes" in migration
    assert "CREATE TABLE IF NOT EXISTS resident_runtimes" in migrations
