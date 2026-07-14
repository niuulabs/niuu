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


def test_settings_parse_additional_resident_controllers() -> None:
    settings = Settings.model_validate(
        {
            "resident_runtimes": {
                "controllers": [
                    {
                        "adapter": (
                            "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"
                        ),
                        "kwargs": {"gateway_endpoint": "openshell.example.test:443"},
                        "secret_kwargs_env": {
                            "client_secret": "RESIDENT_CONTROLLER_0_SK_CLIENT_SECRET"
                        },
                    }
                ]
            }
        }
    )

    controller = settings.resident_runtimes.controllers[0]
    assert controller.adapter.endswith("OpenShellGatewayPodManager")
    assert controller.kwargs["gateway_endpoint"] == "openshell.example.test:443"
    assert controller.secret_kwargs_env["client_secret"].startswith("RESIDENT_CONTROLLER_0")


def test_settings_parse_resident_session_controllers() -> None:
    settings = Settings.model_validate(
        {
            "resident_runtimes": {
                "session_controllers": [
                    {
                        "adapter": (
                            "volundr.adapters.outbound.hermes_gateway."
                            "HermesResidentSessionController"
                        ),
                        "runtime_backend": "openshell",
                    }
                ]
            }
        }
    )

    controller = settings.resident_runtimes.session_controllers[0]
    assert controller.adapter.endswith("HermesResidentSessionController")
    assert controller.runtime_backend.value == "openshell"


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
    usage_migration = (root / "migrations/000056_resident_usage.up.sql").read_text()
    trace_migration = (root / "migrations/000058_resident_session_traces.up.sql").read_text()

    assert "resident_runtimes:" in configmap
    assert "residentRuntimeProfiles" in configmap
    assert "residentRuntimeControllers" in configmap
    assert "residentRuntimeSessionControllers" in configmap
    assert "000055_resident_runtimes.up.sql" in migrations
    assert "CREATE TABLE IF NOT EXISTS resident_runtimes" in migration
    assert "CREATE TABLE IF NOT EXISTS resident_runtimes" in migrations
    assert "ADD COLUMN IF NOT EXISTS tokens_used" in usage_migration
    assert "000056_resident_usage.up.sql" in migrations
    assert "DROP CONSTRAINT IF EXISTS session_spans_session_id_fkey" in trace_migration
    assert "000058_resident_session_traces.up.sql" in migrations
