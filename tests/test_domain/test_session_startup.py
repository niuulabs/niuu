"""The launch-stage record: merging backend views, finishing and failing a launch."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from volundr.domain.session_startup import SessionStartup, StartupStage, StartupStageState

T0 = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def active(key: str, seconds: int, detail: str | None = None) -> StartupStage:
    return StartupStage(key=key, label=key.title(), detail=detail, started_at=at(seconds))


def pending(key: str) -> StartupStage:
    return StartupStage(key=key, label=key.title(), state=StartupStageState.PENDING)


def done(key: str, start: int, end: int, detail: str | None = None) -> StartupStage:
    return StartupStage(
        key=key,
        label=key.title(),
        state=StartupStageState.DONE,
        detail=detail,
        started_at=at(start),
        finished_at=at(end),
    )


def launch() -> SessionStartup:
    return SessionStartup.begin(T0, active("launch", 0))


def keys_and_states(startup: SessionStartup) -> list[tuple[str, str]]:
    return [(s.key, s.state.value) for s in startup.stages]


def test_a_stage_that_begins_ends_every_earlier_active_stage():
    startup = launch().with_stage(active("schedule", 4))

    assert keys_and_states(startup) == [("launch", "done"), ("schedule", "active")]
    assert startup.stages[0].finished_at == at(4)


def test_a_known_stage_keeps_its_earliest_start():
    startup = launch().with_stage(active("schedule", 4)).with_stage(done("schedule", 9, 12))

    stage = startup.stages[1]
    assert stage.state is StartupStageState.DONE
    assert stage.started_at == at(4)
    assert stage.finished_at == at(12)


def test_pending_stages_stay_last_and_new_stages_go_before_them():
    startup = launch().with_stages([pending("containers"), pending("ready")])
    assert keys_and_states(startup)[0] == ("launch", "active")

    startup = startup.with_stage(active("schedule", 3))

    assert keys_and_states(startup) == [
        ("launch", "done"),
        ("schedule", "active"),
        ("containers", "pending"),
        ("ready", "pending"),
    ]


def test_a_pending_stage_that_begins_ends_the_stages_before_it():
    startup = launch().with_stages([active("schedule", 2), pending("containers")])

    startup = startup.with_stage(active("containers", 7, "Pulling image"))

    assert keys_and_states(startup) == [
        ("launch", "done"),
        ("schedule", "done"),
        ("containers", "active"),
    ]
    assert startup.stages[1].finished_at == at(7)
    assert startup.current.detail == "Pulling image"


def test_a_stage_never_ends_before_it_began():
    startup = SessionStartup.begin(T0, active("launch", 10)).with_stage(active("schedule", 5))

    assert startup.stages[0].finished_at == at(10)


def test_merging_a_view_clears_a_previous_read_failure():
    startup = launch().with_progress_error("events forbidden")
    assert startup.progress_error == "events forbidden"

    assert launch().with_progress_error("x").with_stages([]).progress_error is None


def test_finished_ends_open_stages_and_drops_ones_that_never_happened():
    startup = launch().with_stages([active("schedule", 2), pending("ready")])

    finished = startup.finished(at(30))

    assert keys_and_states(finished) == [("launch", "done"), ("schedule", "done")]
    assert finished.stages[1].finished_at == at(30)
    assert finished.current is None


def test_failed_marks_the_active_stage_with_the_reason():
    startup = launch().with_stages([active("schedule", 2), pending("containers")])

    failed = startup.failed(at(600), "0/9 nodes are available: 9 Insufficient memory.")

    assert keys_and_states(failed) == [("launch", "done"), ("schedule", "failed")]
    assert failed.current.detail == "0/9 nodes are available: 9 Insufficient memory."
    assert failed.current.finished_at == at(600)


def test_failed_after_the_last_stage_finished_adds_the_failure_in_place():
    startup = launch().finished(at(5)).failed(at(9), "Broker exited")

    assert keys_and_states(startup) == [("launch", "done"), ("failed", "failed")]
    assert startup.current.detail == "Broker exited"


def test_a_stage_that_began_needs_a_start_time():
    with pytest.raises(ValidationError, match="without a start time"):
        StartupStage(key="schedule", label="Scheduling", state=StartupStageState.ACTIVE)


def test_handed_off_records_when_the_backend_took_over():
    assert launch().handed_off(at(3)).handed_off_at == at(3)


def test_the_record_round_trips_as_json():
    startup = launch().with_stages([active("schedule", 2), pending("ready")]).handed_off(at(1))

    assert SessionStartup.model_validate_json(startup.model_dump_json()) == startup


def test_a_stage_the_backend_names_later_takes_its_place_in_the_launch():
    """Seen live: a pod's volumes and init stages were listed after the pending ones."""
    startup = launch().with_stages(
        [active("release", 1), pending("schedule"), pending("containers"), pending("ready")]
    )

    startup = startup.with_stages(
        [
            done("release", 1, 3),
            active("schedule", 3),
            pending("volumes"),
            pending("init"),
            pending("containers"),
            pending("ready"),
        ]
    )

    assert [s.key for s in startup.stages] == [
        "launch",
        "release",
        "schedule",
        "volumes",
        "init",
        "containers",
        "ready",
    ]


def test_stages_outside_the_view_keep_their_place():
    startup = launch().with_stages([active("schedule", 2)]).failed(at(9), "boom")

    reordered = startup.with_stages([startup.stages[1]])

    assert [s.key for s in reordered.stages] == ["launch", "schedule"]
    assert reordered.stages[1].state is StartupStageState.FAILED
