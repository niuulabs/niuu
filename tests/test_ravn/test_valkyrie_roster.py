"""The Valkyrie catalog as a resident roster.

Identity, chat room and visibility come from the catalog; status comes only
from what the dashboard projection actually observed on telemetry.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from niuu.domain.models import InstanceVisibility
from ravn.api.valkyrie_config import ValkyrieDashboardConfig, roster_fields
from ravn.api.valkyrie_projection import ValkyrieDashboardProjection
from ravn.api.valkyrie_roster import ValkyrieCatalogRoster
from sleipnir.domain.events import SleipnirEvent

_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
_STALE_AFTER = 600.0
_CHAT = "wss://yggdrasil.niuu.world/rooms/runa/ws/ravn"


def _record(valkyrie_id: str, name: str, **valkyrie: Any) -> dict[str, Any]:
    return {
        "id": f"env-{valkyrie_id}",
        "name": f"{name} cluster",
        "valkyrie": {
            "valkyrieId": valkyrie_id,
            "valkyrieName": name,
            "persona": f"{valkyrie_id}-persona",
            **valkyrie,
        },
    }


def _config(*records: dict[str, Any]) -> ValkyrieDashboardConfig:
    return ValkyrieDashboardConfig(
        environments_json=json.dumps({"environments": list(records)}),
        roster_stale_after_seconds=_STALE_AFTER,
    )


class _Dashboard:
    """Dashboard source that returns exactly the observed state a test sets."""

    def __init__(self, valkyries: list[dict[str, Any]] | None = None) -> None:
        self.valkyries = valkyries or []

    def dashboard(self) -> dict[str, Any]:
        return {"valkyries": self.valkyries}


def _observed(valkyrie_id: str, observed_at: str, source: str = "observed") -> dict[str, Any]:
    return {
        "id": valkyrie_id,
        "status": "online",
        "identitySource": source,
        "lastObservedAt": observed_at,
    }


def _roster(config: ValkyrieDashboardConfig, dashboard: Any) -> ValkyrieCatalogRoster:
    return ValkyrieCatalogRoster(dashboard=dashboard, config=config, clock=lambda: _NOW)


class TestCatalogMapping:
    async def test_maps_identity_onto_the_standalone_resident_shape(self):
        config = _config(_record("valkyrie-runa", "Runa", chatEndpoint=_CHAT))

        residents = await _roster(config, _Dashboard()).list_residents()

        assert len(residents) == 1
        runa = residents[0]
        assert runa.id == "valkyrie-runa"
        assert runa.resident_name == "Runa"
        assert runa.persona_name == "valkyrie-runa-persona"
        assert runa.location == "Runa cluster"
        assert runa.deployment == "valkyrie"
        assert runa.chat_endpoint == _CHAT
        assert runa.capabilities == ["chat"]

    async def test_without_a_chat_endpoint_there_is_no_chat_capability(self):
        config = _config(_record("valkyrie-bryn", "Bryn"))

        (bryn,) = await _roster(config, _Dashboard()).list_residents()

        assert bryn.chat_endpoint is None
        assert bryn.capabilities == []

    async def test_visibility_defaults_to_system_and_carries_owner_and_tenant(self):
        config = _config(
            _record("valkyrie-hildr", "Hildr"),
            _record(
                "valkyrie-rota",
                "Rota",
                visibility="user",
                ownerId="user-a",
                tenantId="tenant-a",
            ),
        )

        hildr, rota = await _roster(config, _Dashboard()).list_residents()

        assert hildr.visibility is InstanceVisibility.SYSTEM
        assert (hildr.owner_id, hildr.tenant_id) == ("", "")
        assert rota.visibility is InstanceVisibility.USER
        assert (rota.owner_id, rota.tenant_id) == ("user-a", "tenant-a")

    async def test_empty_catalog_lists_nothing(self):
        roster = _roster(ValkyrieDashboardConfig(environments_json=""), _Dashboard())

        assert await roster.list_residents() == []

    def test_duplicate_valkyrie_ids_are_rejected(self):
        config = _config(
            _record("valkyrie-eir", "Eir"),
            {**_record("valkyrie-eir", "Eir again"), "id": "env-other"},
        )

        with pytest.raises(ValueError, match="valkyrie-eir.*more than once"):
            _roster(config, _Dashboard())


class TestObservedStatus:
    async def _status(self, live: list[dict[str, Any]]) -> Any:
        config = _config(_record("valkyrie-gondul", "Gondul"))
        (gondul,) = await _roster(config, _Dashboard(live)).list_residents()
        return gondul

    async def test_recently_observed_is_active(self):
        observed_at = (_NOW - timedelta(seconds=30)).isoformat()

        gondul = await self._status([_observed("valkyrie-gondul", observed_at)])

        assert gondul.status == "active"
        assert gondul.updated_at == observed_at
        (condition,) = gondul.conditions
        assert (condition["type"], condition["status"], condition["reason"]) == (
            "Observed",
            "true",
            "Fresh",
        )
        assert condition["lastTransitionAt"] == observed_at

    async def test_observation_exactly_at_the_bound_is_still_active(self):
        observed_at = (_NOW - timedelta(seconds=_STALE_AFTER)).isoformat()

        gondul = await self._status([_observed("valkyrie-gondul", observed_at)])

        assert gondul.status == "active"

    async def test_stale_observation_is_idle_and_says_so(self):
        observed_at = (_NOW - timedelta(seconds=_STALE_AFTER + 1)).isoformat()

        gondul = await self._status([_observed("valkyrie-gondul", observed_at)])

        assert gondul.status == "idle"
        assert gondul.updated_at == observed_at
        (condition,) = gondul.conditions
        assert (condition["status"], condition["reason"]) == ("false", "Stale")
        assert "600s" in condition["message"]

    async def test_configured_only_valkyrie_is_idle_never_observed(self):
        # The projection defaults every catalog Valkyrie's status to "online";
        # that configured default must not leak into the roster.
        configured = {
            "id": "valkyrie-gondul",
            "status": "online",
            "identitySource": "configured",
            "lastObservedAt": "",
        }

        gondul = await self._status([configured])

        assert gondul.status == "idle"
        assert gondul.updated_at is None
        (condition,) = gondul.conditions
        assert (condition["status"], condition["reason"]) == ("unknown", "NeverObserved")
        assert "lastTransitionAt" not in condition

    async def test_absent_from_the_dashboard_is_idle(self):
        gondul = await self._status([])

        assert gondul.status == "idle"
        assert gondul.conditions[0]["reason"] == "NeverObserved"

    async def test_observed_without_a_timestamp_is_not_claimed_active(self):
        gondul = await self._status([_observed("valkyrie-gondul", "")])

        assert gondul.status == "idle"
        assert gondul.conditions[0]["reason"] == "NeverObserved"

    async def test_unparseable_timestamp_is_reported_not_guessed(self):
        gondul = await self._status([_observed("valkyrie-gondul", "yesterday-ish")])

        assert gondul.status == "idle"
        (condition,) = gondul.conditions
        assert condition["reason"] == "UnparseableTimestamp"
        assert "yesterday-ish" in condition["message"]

    async def test_naive_timestamp_is_read_as_utc(self):
        observed_at = (_NOW - timedelta(seconds=5)).replace(tzinfo=None).isoformat()

        gondul = await self._status([_observed("valkyrie-gondul", observed_at)])

        assert gondul.status == "active"


class TestAgainstTheRealProjection:
    """The roster reads the same state ``GET /valkyrie/dashboard`` reports."""

    def _config_for_projection(self) -> ValkyrieDashboardConfig:
        return _config(
            {**_record("valkyrie-sigrun", "Sigrun", chatEndpoint=_CHAT), "id": "ymir"},
            {**_record("valkyrie-kara", "Kara"), "id": "vanaheim"},
        )

    async def test_telemetry_makes_only_the_observing_valkyrie_active(self):
        config = self._config_for_projection()
        projection = ValkyrieDashboardProjection(config)
        projection.record_event(
            SleipnirEvent(
                event_type="valkyrie.runtime.started",
                source="ravn:valkyrie:ymir",
                payload={
                    "environment_id": "ymir",
                    "valkyrie_id": "valkyrie-sigrun",
                    "valkyrie_name": "Sigrun",
                },
                summary="runtime started",
                urgency=0.2,
                domain="infrastructure",
                timestamp=_NOW - timedelta(seconds=10),
            )
        )
        roster = _roster(config, projection)

        sigrun, kara = await roster.list_residents()

        assert (sigrun.resident_name, sigrun.status) == ("Sigrun", "active")
        assert (kara.resident_name, kara.status) == ("Kara", "idle")
        assert kara.conditions[0]["reason"] == "NeverObserved"

    async def test_telemetry_older_than_the_bound_goes_idle(self):
        config = self._config_for_projection()
        projection = ValkyrieDashboardProjection(config)
        projection.record_event(
            SleipnirEvent(
                event_type="valkyrie.presence.heartbeat",
                source="ravn:valkyrie:ymir",
                payload={"environment_id": "ymir", "valkyrie_id": "valkyrie-sigrun"},
                summary="heartbeat",
                urgency=0.1,
                domain="infrastructure",
                timestamp=_NOW - timedelta(hours=2),
            )
        )

        sigrun, _ = await _roster(config, projection).list_residents()

        assert sigrun.status == "idle"
        assert sigrun.conditions[0]["reason"] == "Stale"


class TestCatalogFieldValidation:
    def test_valid_fields_parse(self):
        fields = roster_fields(
            _record("valkyrie-ivaldi", "Ivaldi", chatEndpoint=_CHAT, visibility="tenant")
        )

        assert fields.chat_endpoint == _CHAT
        assert fields.visibility is InstanceVisibility.TENANT

    def test_snake_case_names_are_accepted(self):
        fields = roster_fields(
            {"valkyrie": {"chat_endpoint": _CHAT, "owner_id": "u", "tenant_id": "t"}}
        )

        assert (fields.chat_endpoint, fields.owner_id, fields.tenant_id) == (_CHAT, "u", "t")

    @pytest.mark.parametrize(
        "endpoint",
        [
            "ws://yggdrasil.niuu.world/rooms/x/ws/ravn",
            "https://yggdrasil.niuu.world/rooms/x",
            "wss://",
            "yggdrasil.niuu.world/rooms/x",
            "/rooms/x/ws/ravn",
            "wss://yggdrasil.niuu.world/rooms/x y",
            " wss://yggdrasil.niuu.world/rooms/x",
            42,
        ],
    )
    def test_malformed_chat_endpoint_fails_config_load(self, endpoint):
        catalog = json.dumps([_record("valkyrie-eir", "Eir", chatEndpoint=endpoint)])

        with pytest.raises(ValueError, match="valkyrie-eir.*chatEndpoint"):
            ValkyrieDashboardConfig(environments_json=catalog)

    def test_unknown_visibility_fails_config_load(self):
        catalog = json.dumps([_record("valkyrie-eir", "Eir", visibility="everyone")])

        with pytest.raises(ValueError, match="visibility 'everyone' must be one of: system"):
            ValkyrieDashboardConfig(environments_json=catalog)

    def test_non_string_owner_fails_config_load(self):
        catalog = json.dumps([_record("valkyrie-eir", "Eir", ownerId=7)])

        with pytest.raises(ValueError, match="ownerId must be a string"):
            ValkyrieDashboardConfig(environments_json=catalog)

    def test_catalog_file_is_validated_too(self, tmp_path):
        from ravn.api.valkyrie_config import configured_environment_records

        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([_record("valkyrie-eir", "Eir", chatEndpoint="ws://nope")]))
        config = ValkyrieDashboardConfig(environments_file=str(path))

        with pytest.raises(ValueError, match="chatEndpoint"):
            configured_environment_records(config)

    def test_staleness_bound_must_be_positive(self):
        with pytest.raises(ValueError):
            ValkyrieDashboardConfig(roster_stale_after_seconds=0)

    def test_staleness_bound_reads_the_environment(self, monkeypatch):
        monkeypatch.setenv("RAVN_VALKYRIE_DASHBOARD_ROSTER_STALE_AFTER_SECONDS", "42")

        assert ValkyrieDashboardConfig().roster_stale_after_seconds == 42.0
