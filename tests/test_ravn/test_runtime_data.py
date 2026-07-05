from ravn.api import runtime_data


def test_runtime_data_lookup_and_trigger_helpers_round_trip():
    session = runtime_data.get_session("10000001-0000-4000-8000-000000000001")
    assert session is not None
    assert session["persona_name"] == "sindri"
    assert runtime_data.get_session("missing") is None

    before = len(runtime_data.list_triggers())
    created = runtime_data.create_trigger(
        kind="event",
        persona_name="muninn",
        spec="research.requested",
        enabled=True,
    )
    assert created["kind"] == "event"
    assert len(runtime_data.list_triggers()) == before + 1
    assert runtime_data.delete_trigger(created["id"]) is True
    assert runtime_data.delete_trigger("missing-trigger") is False
    assert len(runtime_data.list_triggers()) == before


def test_runtime_data_budget_accessors_cover_present_and_missing_values():
    budget = runtime_data.get_budget("a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c")
    assert budget == {"spent_usd": 3.61, "cap_usd": 5.0, "warn_at": 0.7}
    assert runtime_data.get_budget("missing") is None
    assert runtime_data.get_fleet_budget() == {"spent_usd": 7.86, "cap_usd": 11.0, "warn_at": 0.7}
