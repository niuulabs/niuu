"""The slot ledger's domain: policy, the pure placement functions and the session view."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from volundr.domain.compute import (
    LIVE_SLOT_STATES,
    ComputeCapacityError,
    ComputeLease,
    ComputePoolFullError,
    ComputePoolPolicy,
    ComputeProvisioningLimitError,
    ComputeSlot,
    ComputeStandbyTargetMetError,
    LeaseState,
    Machine,
    MachineState,
    SlotLease,
    SlotState,
    display_state,
    next_free_slot,
    session_limit,
    usable_slots,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def machine(slots=1, state=LeaseState.IDLE, **update) -> ComputeLease:
    fields = {
        "id": uuid4(),
        "pool_id": "pool",
        "profile": "small",
        "request_fingerprint": "digest",
        "slots": slots,
        "state": state,
        "created_at": NOW - timedelta(hours=1),
    }
    return ComputeLease(**{**fields, **update})


def binding(lease, slot=0, state=SlotState.BUSY, **update) -> ComputeSlot:
    fields = {
        "id": uuid4(),
        "allocation_id": lease.id,
        "slot": slot,
        "pool_id": lease.pool_id,
        "session_id": uuid4(),
        "tenant_id": "tenant",
        "owner_id": "owner",
        "state": state,
    }
    return ComputeSlot(**{**fields, **update})


def policy(**update) -> ComputePoolPolicy:
    return ComputePoolPolicy(profile="small", max_machines=4, **update)


# -- the policy ------------------------------------------------------------------------------


def test_a_policy_without_slot_settings_is_todays_one_session_per_machine():
    default = policy()

    assert default.slots_per_machine == 1
    assert default.max_sessions_per_machine is None
    assert default.max_machine_age_seconds is None
    assert default.reuse_policy == "replace"


def test_a_stored_policy_from_before_slots_existed_still_loads():
    stored = ComputePoolPolicy.model_validate_json(
        '{"profile": "small", "max_machines": 2, "warm_min": 1}'
    )

    assert stored.slots_per_machine == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("slots_per_machine", 0),
        ("max_sessions_per_machine", 0),
        ("max_machine_age_seconds", 0),
        ("max_machine_age_seconds", -1),
    ],
)
def test_slot_settings_must_be_positive(field, value):
    with pytest.raises(ValidationError):
        policy(**{field: value})


def test_the_warm_minimum_counts_slots_not_machines():
    assert policy(slots_per_machine=3, warm_min=12).warm_min == 12
    with pytest.raises(ValueError, match="Warm minimum"):
        policy(slots_per_machine=3, warm_min=13)
    with pytest.raises(ValueError, match="Warm minimum"):
        policy(warm_min=5)


@pytest.mark.parametrize(
    "field,value", [("max_sessions_per_machine", 5), ("max_machine_age_seconds", 60)]
)
def test_drain_limits_apply_to_replace_and_are_rejected_under_reuse(field, value):
    with pytest.raises(ValueError, match="apply to replace"):
        policy(reuse_policy="reuse", **{field: value})
    assert policy(reuse_policy="replace", **{field: value})


def test_replacement_cannot_come_before_every_slot_has_served_a_session():
    with pytest.raises(ValueError, match="cannot be below slots per machine"):
        policy(slots_per_machine=3, max_sessions_per_machine=2)
    assert policy(slots_per_machine=3, max_sessions_per_machine=3)
    assert policy(slots_per_machine=3, max_sessions_per_machine=9)


def test_slot_settings_have_no_extra_keys_and_round_trip():
    original = policy(slots_per_machine=2, max_sessions_per_machine=4, max_machine_age_seconds=9.5)

    assert ComputePoolPolicy.model_validate_json(original.model_dump_json()) == original
    with pytest.raises(ValidationError):
        ComputePoolPolicy.model_validate({"profile": "p", "max_machines": 1, "bogus": 1})


# -- errors ----------------------------------------------------------------------------------


def test_capacity_errors_share_one_base_so_callers_can_tell_a_cap_from_a_target():
    for error in (
        ComputeStandbyTargetMetError,
        ComputePoolFullError,
        ComputeProvisioningLimitError,
    ):
        assert issubclass(error, ComputeCapacityError)
    assert not issubclass(ComputePoolFullError, ComputeStandbyTargetMetError)


# -- the ledger models -----------------------------------------------------------------------


def test_a_machine_record_holds_no_session_and_has_one_slot_unless_told_otherwise():
    lease = machine()

    assert lease.slots == 1
    for gone in ("session_id", "tenant_id", "owner_id", "stop_requested", "runtime_started"):
        assert gone not in ComputeLease.model_fields
    with pytest.raises(ValidationError):
        ComputeLease(
            id=uuid4(), pool_id="p", profile="s", request_fingerprint="f", session_id=uuid4()
        )
    with pytest.raises(ValidationError):
        machine(slots=0)


def test_a_slot_binding_needs_a_tenant_an_owner_and_a_slot_index():
    lease = machine()
    assert binding(lease).state is SlotState.BUSY
    for update in ({"tenant_id": ""}, {"owner_id": ""}, {"slot": -1}):
        with pytest.raises(ValidationError):
            binding(lease, **update)


def test_live_slot_states_are_the_ones_that_hold_a_session():
    assert LIVE_SLOT_STATES == {
        SlotState.STARTING,
        SlotState.BUSY,
        SlotState.FAILED,
        SlotState.DRAINING,
    }


def test_a_session_view_reads_the_machine_and_the_binding():
    plan_machine = machine(
        machine=Machine(
            allocation_id=uuid4(),
            resource_id="vm",
            state=MachineState.RUNNING,
            addresses=("1.2.3.4",),
        )
    )
    slot = binding(plan_machine, slot=0)

    view = SlotLease(allocation=plan_machine, binding=slot)

    assert view.id == plan_machine.id
    assert (view.pool_id, view.profile, view.machine) == ("pool", "small", plan_machine.machine)
    assert (view.slot, view.session_id, view.owner_id, view.tenant_id) == (
        0,
        slot.session_id,
        "owner",
        "tenant",
    )
    # The session's plan lives on the binding, not on the machine's host-provisioning plan.
    assert view.execution_plan is slot.execution_plan is None
    with pytest.raises(ValidationError):
        view.allocation = plan_machine


# -- session_limit ---------------------------------------------------------------------------


def test_each_slot_serves_one_session_unless_the_policy_says_more():
    two = machine(slots=2)

    assert session_limit(policy(slots_per_machine=2), two) == 2
    assert session_limit(policy(slots_per_machine=2, max_sessions_per_machine=7), two) == 7


def test_a_reused_machine_has_no_session_limit():
    assert session_limit(policy(reuse_policy="reuse"), machine(slots=2)) is None


# -- usable_slots ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,expected",
    [
        (LeaseState.PROVISIONING, 1),
        (LeaseState.READY, 1),
        (LeaseState.IDLE, 1),
        (LeaseState.BUSY, 0),
        (LeaseState.QUARANTINED, 0),
        (LeaseState.DRAINING, 0),
        (LeaseState.RELEASED, 0),
        (LeaseState.FAILED, 0),
    ],
)
def test_only_a_machine_that_is_coming_up_or_in_service_offers_slots(state, expected):
    assert usable_slots(machine(state=state), [], policy(), NOW) == expected


def test_an_empty_machine_offers_every_slot():
    assert usable_slots(machine(slots=3), [], policy(slots_per_machine=3), NOW) == 3


@pytest.mark.parametrize(
    "state,usable",
    [
        (SlotState.STARTING, 1),
        (SlotState.BUSY, 1),
        (SlotState.FAILED, 1),
        (SlotState.DRAINING, 1),
        (SlotState.RETIRED, 1),
        (SlotState.RELEASED, 2),
    ],
)
def test_a_slot_is_occupied_in_every_state_except_released(state, usable):
    lease = machine(slots=2)
    held = [binding(lease, state=state)]

    # Under reuse there is no session limit, so only occupancy matters.
    assert (
        usable_slots(lease, held, policy(slots_per_machine=2, reuse_policy="reuse"), NOW) == usable
    )


def test_the_session_limit_counts_every_session_the_machine_served_including_released_ones():
    lease = machine(slots=2)
    pool = policy(slots_per_machine=2, max_sessions_per_machine=3)
    served = [binding(lease, slot=0, state=SlotState.RELEASED)]

    assert usable_slots(lease, served, pool, NOW) == 2
    served.append(binding(lease, slot=0, state=SlotState.RELEASED))
    assert usable_slots(lease, served, pool, NOW) == 1  # two slots free, one session left
    served.append(binding(lease, slot=1, state=SlotState.RELEASED))
    assert usable_slots(lease, served, pool, NOW) == 0


def test_one_session_per_slot_by_default_so_a_used_machine_offers_nothing():
    lease = machine()

    assert usable_slots(lease, [binding(lease, state=SlotState.RELEASED)], policy(), NOW) == 0


def test_a_machine_with_a_stop_intent_on_a_live_slot_offers_nothing():
    lease = machine(slots=2)
    stopping = binding(lease, slot=0, stop_requested=True)

    assert usable_slots(lease, [stopping], policy(slots_per_machine=2), NOW) == 0
    # A finished slot's old intent is history, not a block.
    done = binding(lease, slot=0, state=SlotState.RELEASED, stop_requested=True)
    assert usable_slots(lease, [done], policy(slots_per_machine=2, reuse_policy="reuse"), NOW) == 2


def test_a_machine_past_its_age_limit_offers_nothing_even_when_empty():
    lease = machine(created_at=NOW - timedelta(seconds=100))

    assert usable_slots(lease, [], policy(max_machine_age_seconds=101), NOW) == 1
    assert usable_slots(lease, [], policy(max_machine_age_seconds=100), NOW) == 0
    assert usable_slots(lease, [], policy(max_machine_age_seconds=50), NOW) == 0


def test_usable_slots_never_goes_negative():
    lease = machine()
    crowded = [binding(lease, slot=0), binding(lease, slot=0, state=SlotState.STARTING)]

    assert usable_slots(lease, crowded, policy(), NOW) == 0


# -- next_free_slot --------------------------------------------------------------------------


def test_the_lowest_unoccupied_slot_is_next():
    lease = machine(slots=3)

    assert next_free_slot(lease, []) == 0
    assert next_free_slot(lease, [binding(lease, slot=0)]) == 1
    assert next_free_slot(lease, [binding(lease, slot=1)]) == 0
    assert next_free_slot(lease, [binding(lease, slot=0), binding(lease, slot=2)]) == 1


def test_a_released_slot_is_free_again_but_a_retired_one_is_not():
    lease = machine(slots=1)

    assert next_free_slot(lease, [binding(lease, state=SlotState.RELEASED)]) == 0
    with pytest.raises(ComputeCapacityError, match="no free slot"):
        next_free_slot(lease, [binding(lease, state=SlotState.RETIRED)])


def test_a_full_machine_has_no_next_slot():
    lease = machine(slots=2)

    with pytest.raises(ComputeCapacityError, match="no free slot"):
        next_free_slot(lease, [binding(lease, slot=0), binding(lease, slot=1)])


# -- display_state: one slot reads as a one-session machine always did -------------------------


@pytest.mark.parametrize(
    "state,slot,shown",
    [
        # Cold start: machine ready, runtime start unconfirmed.
        (LeaseState.READY, SlotState.STARTING, LeaseState.READY),
        # Warm start: an idle machine, runtime start unconfirmed.
        (LeaseState.IDLE, SlotState.STARTING, LeaseState.READY),
        (LeaseState.READY, SlotState.BUSY, LeaseState.BUSY),
        (LeaseState.IDLE, SlotState.BUSY, LeaseState.BUSY),
        (LeaseState.READY, SlotState.FAILED, LeaseState.FAILED),
        (LeaseState.IDLE, SlotState.FAILED, LeaseState.FAILED),
        (LeaseState.DRAINING, SlotState.DRAINING, LeaseState.DRAINING),
        (LeaseState.PROVISIONING, SlotState.STARTING, LeaseState.PROVISIONING),
        (LeaseState.FAILED, SlotState.STARTING, LeaseState.FAILED),
    ],
)
def test_one_slot_shows_the_state_a_one_session_machine_had(state, slot, shown):
    lease = machine(state=state)

    assert display_state(lease, [binding(lease, state=slot)]) is shown


def test_an_empty_machine_shows_its_own_state():
    for state in (LeaseState.IDLE, LeaseState.READY, LeaseState.PROVISIONING):
        assert display_state(machine(state=state), []) is state


def test_ended_slots_do_not_change_what_a_machine_shows():
    lease = machine(state=LeaseState.IDLE)
    ended = [binding(lease, state=SlotState.RELEASED), binding(lease, state=SlotState.RETIRED)]

    assert display_state(lease, ended) is LeaseState.IDLE


def test_busy_outranks_failed_outranks_starting_across_slots():
    lease = machine(slots=3)
    starting, failed, busy = (
        binding(lease, slot=0, state=SlotState.STARTING),
        binding(lease, slot=1, state=SlotState.FAILED),
        binding(lease, slot=2, state=SlotState.BUSY),
    )

    assert display_state(lease, [starting, failed, busy]) is LeaseState.BUSY
    assert display_state(lease, [starting, failed]) is LeaseState.FAILED
    assert display_state(lease, [starting]) is LeaseState.READY
