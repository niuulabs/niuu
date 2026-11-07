"""Resident runtime profile configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from volundr.config import ResidentRuntimesConfig, Settings


def test_realm_detail_stream_interval_requires_a_positive_value() -> None:
    settings = Settings.model_validate({"realm_detail_stream_interval_seconds": 7.5})

    assert settings.realm_detail_stream_interval_seconds == 7.5
    with pytest.raises(ValidationError):
        Settings.model_validate({"realm_detail_stream_interval_seconds": 0})


def test_settings_parse_resident_profiles() -> None:
    settings = Settings.model_validate(
        {
            "resident_runtimes": {
                "profiles": [
                    {
                        "id": "openclaw-local",
                        "display_name": "Resident OpenClaw",
                        "backend": "local",
                        "engine": "openclaw",
                        "capabilities": ["chat", "runtime.suspend"],
                    }
                ]
            }
        }
    )

    profile = settings.resident_runtimes.profiles[0]
    assert profile.id == "openclaw-local"
    assert profile.backend.value == "local"
    assert profile.engine.value == "openclaw"
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
                        "engine": "openclaw",
                    },
                    {
                        "id": "same",
                        "display_name": "Second",
                        "backend": "openshell",
                        "engine": "hermes",
                    },
                ]
            }
        )


@pytest.mark.parametrize("spelling", ["ravn", "Ravn", " ravn "])
def test_a_profile_with_the_removed_ravn_engine_is_refused_with_the_remedy(spelling: str) -> None:
    with pytest.raises(ValidationError) as raised:
        Settings.model_validate(
            {
                "resident_runtimes": {
                    "profiles": [
                        {
                            "id": "ravn-helm",
                            "display_name": "Resident Ravn",
                            "backend": "helmrelease",
                            "engine": spelling,
                        }
                    ]
                }
            }
        )

    message = str(raised.value)
    assert "the ravn resident engine was removed" in message
    assert "openclaw or hermes" in message


def test_an_unknown_engine_is_still_a_plain_validation_error() -> None:
    with pytest.raises(ValidationError, match="Input should be 'openclaw' or 'hermes'"):
        ResidentRuntimesConfig.model_validate(
            {"profiles": [{"id": "x", "display_name": "X", "backend": "local", "engine": "other"}]}
        )


def test_migration_drops_the_resident_runtimes_of_the_removed_ravn_engine() -> None:
    root = Path(__file__).resolve().parents[2]
    up = (root / "migrations/000111_drop_ravn_resident_runtimes.up.sql").read_text()
    down = (root / "migrations/000111_drop_ravn_resident_runtimes.down.sql").read_text()

    statements = [line for line in up.splitlines() if not line.startswith("--") and line.strip()]
    assert statements == ["DELETE FROM resident_runtimes", "WHERE engine = 'ravn';"]
    assert "cannot be restored" in down


def test_chart_contains_profile_config_and_migrations() -> None:
    root = Path(__file__).resolve().parents[2]
    configmap = (root / "charts/volundr/templates/configmap.yaml").read_text()
    migration = (root / "migrations/000055_resident_runtimes.up.sql").read_text()
    usage_migration = (root / "migrations/000056_resident_usage.up.sql").read_text()
    trace_migration = (root / "migrations/000058_resident_session_traces.up.sql").read_text()

    assert "resident_runtimes:" in configmap
    assert "residentRuntimeProfiles" in configmap
    assert "residentRuntimeControllers" in configmap
    assert "residentRuntimeSessionControllers" in configmap
    assert "CREATE TABLE IF NOT EXISTS resident_runtimes" in migration
    assert "ADD COLUMN IF NOT EXISTS tokens_used" in usage_migration
    assert "DROP CONSTRAINT IF EXISTS session_spans_session_id_fkey" in trace_migration
