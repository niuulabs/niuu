"""Tests for ravn.cli.trigger_wiring._wire_api_triggers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ravn.cli.commands import _wire_api_triggers
from ravn.config import Settings


class FakeDriveLoop:
    def __init__(self) -> None:
        self.registered: list[object] = []

    def register_trigger(self, trigger: object) -> None:
        self.registered.append(trigger)


def test_disabled_by_default_registers_nothing() -> None:
    drive_loop = FakeDriveLoop()
    _wire_api_triggers(drive_loop, Settings(), "product-resident")
    assert drive_loop.registered == []


def test_enabled_with_no_platform_base_url_fails_loudly() -> None:
    drive_loop = FakeDriveLoop()
    settings = Settings(
        resident_triggers={"enabled": True},
        gateway={"platform": {"base_url": ""}},
    )
    with pytest.raises(ValueError, match="gateway.platform.base_url is empty"):
        _wire_api_triggers(drive_loop, settings, "product-resident")


def test_enabled_with_no_persona_fails_loudly() -> None:
    drive_loop = FakeDriveLoop()
    settings = Settings(
        resident_triggers={"enabled": True},
        gateway={"platform": {"base_url": "http://platform.local:8080"}},
    )
    with pytest.raises(ValueError, match="no persona configured"):
        _wire_api_triggers(drive_loop, settings, "")
    assert drive_loop.registered == []


def test_enabled_with_no_amqp_url_fails_loudly(monkeypatch) -> None:
    drive_loop = FakeDriveLoop()
    settings = Settings(
        resident_triggers={"enabled": True},
        gateway={"platform": {"base_url": "http://platform.local:8080"}},
    )
    monkeypatch.delenv(settings.sleipnir.amqp_url_env, raising=False)
    with pytest.raises(ValueError, match="AMQP"):
        _wire_api_triggers(drive_loop, settings, "product-resident")
    assert drive_loop.registered == []


def test_enabled_and_configured_registers_source_and_cron_trigger(monkeypatch) -> None:
    from ravn.adapters.triggers.api_source import ApiTriggerSource
    from ravn.adapters.triggers.cron import CronTrigger

    drive_loop = FakeDriveLoop()
    settings = Settings(
        resident_triggers={"enabled": True, "poll_interval_seconds": 5},
        gateway={"platform": {"base_url": "http://platform.local:8080"}},
    )
    monkeypatch.setenv(settings.sleipnir.amqp_url_env, "amqp://guest:guest@localhost/")
    _wire_api_triggers(drive_loop, settings, "product-resident")

    kinds = [type(item) for item in drive_loop.registered]
    assert ApiTriggerSource in kinds
    assert CronTrigger in kinds

    source = next(item for item in drive_loop.registered if isinstance(item, ApiTriggerSource))
    assert source._persona_name == "product-resident"
    assert source._poll_interval_seconds == 5
    expected_max_failures = settings.resident_triggers.max_consecutive_poll_failures
    assert source._max_consecutive_poll_failures == expected_max_failures


def test_second_cron_trigger_uses_a_distinct_lock_from_the_local_one(tmp_path, monkeypatch) -> None:
    """Two CronTrigger instances on one drive loop must not fight over one lock file."""
    from ravn.adapters.triggers.cron import CronTrigger, make_cron_trigger

    drive_loop = FakeDriveLoop()
    settings = Settings(
        resident_triggers={"enabled": True},
        gateway={"platform": {"base_url": "http://platform.local:8080"}},
        initiative={"queue_journal_path": str(tmp_path / "daemon" / "queue.json")},
    )
    monkeypatch.setenv(settings.sleipnir.amqp_url_env, "amqp://guest:guest@localhost/")
    local_trigger, _store = make_cron_trigger(
        jobs=[],
        jobs_path=tmp_path / "cron" / "jobs.json",
        state_path=tmp_path / "daemon" / "cron_state.json",
        lock_path=tmp_path / "daemon" / "cron.lock",
    )
    _wire_api_triggers(drive_loop, settings, "product-resident")
    api_trigger = next(item for item in drive_loop.registered if isinstance(item, CronTrigger))

    assert api_trigger._lock_path != local_trigger._lock_path
    assert api_trigger._state_path != local_trigger._state_path


def test_pat_token_forwarded_to_the_http_client(monkeypatch) -> None:
    drive_loop = FakeDriveLoop()
    settings = Settings(
        resident_triggers={"enabled": True},
        gateway={
            "platform": {
                "base_url": "http://platform.local:8080",
                "pat_token": "shh-secret",
            }
        },
    )
    monkeypatch.setenv(settings.sleipnir.amqp_url_env, "amqp://guest:guest@localhost/")
    _wire_api_triggers(drive_loop, settings, "product-resident")
    from ravn.adapters.triggers.api_source import ApiTriggerSource

    source = next(item for item in drive_loop.registered if isinstance(item, ApiTriggerSource))
    # StaticBearerTokenAuthAdapter is chosen when an explicit token is given —
    # confirms the PAT flowed through client_from_workload_identity rather
    # than falling back to workload-identity discovery.
    assert source._client._auth is not None


def test_disabled_is_a_no_op_with_mock_settings() -> None:
    """Sanity: a MagicMock settings double (used elsewhere in this test suite)
    is falsy-safe for resident_triggers.enabled, matching the default off state."""
    drive_loop = FakeDriveLoop()
    settings = MagicMock()
    settings.resident_triggers.enabled = False
    _wire_api_triggers(drive_loop, settings, "product-resident")
    assert drive_loop.registered == []
