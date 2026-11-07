"""The startup recorder: Forge's own stages, the backend's stages, and closing a launch."""

import asyncio
from datetime import UTC, datetime

import pytest

from tests.conftest import InMemorySessionRepository, MockEventBroadcaster, MockPodManager
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_startup import SessionStartupRecorder
from volundr.domain.session_startup import StartupStage, StartupStageState

T0 = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


class StagedPodManager(MockPodManager):
    """A backend that reports its launch stages."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stages: tuple[StartupStage, ...] | None = ()
        self.read_error: Exception | None = None
        self.reads = 0

    @property
    def reports_startup_stages(self) -> bool:
        return True

    async def startup_stages(self, session):
        self.reads += 1
        if self.read_error is not None:
            raise self.read_error
        return self.stages


def schedule(state=StartupStageState.ACTIVE, detail=None) -> StartupStage:
    return StartupStage(
        key="schedule",
        label="Scheduling",
        state=state,
        detail=detail,
        started_at=T0,
        finished_at=T0 if state is StartupStageState.DONE else None,
    )


@pytest.fixture
def repository():
    return InMemorySessionRepository()


@pytest.fixture
def broadcaster():
    return MockEventBroadcaster()


async def starting(repository, status=SessionStatus.STARTING) -> Session:
    return await repository.create(Session(name="boot", status=status))


def recorder(repository, pod_manager, broadcaster, poll=0.01) -> SessionStartupRecorder:
    return SessionStartupRecorder(repository, pod_manager, broadcaster, poll)


async def wait_until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.01)


def test_the_poll_interval_must_be_positive(repository, broadcaster):
    with pytest.raises(ValueError, match="startup_poll_seconds"):
        SessionStartupRecorder(repository, MockPodManager(), broadcaster, 0)


async def test_begin_starts_a_new_launch_for_the_caller_to_announce(repository, broadcaster):
    session = await starting(repository)

    stored = await recorder(repository, MockPodManager(), broadcaster).begin(session)

    assert [(s.key, s.state) for s in stored.startup.stages] == [
        ("launch", StartupStageState.ACTIVE)
    ]
    assert broadcaster._session_updated_events == []


async def test_begin_replaces_the_previous_launch(repository, broadcaster):
    session = await starting(repository)
    startups = recorder(repository, MockPodManager(), broadcaster)
    first = await startups.begin(session)
    await startups.handing_off(session.id)

    second = await startups.begin(session)

    assert second.startup.started_at >= first.startup.started_at
    assert second.startup.handed_off_at is None
    assert [s.key for s in second.startup.stages] == ["launch"]


async def test_a_backend_without_stages_shows_the_runtime_starting(repository, broadcaster):
    session = await starting(repository)
    startups = recorder(repository, MockPodManager(), broadcaster)
    await startups.begin(session)

    await startups.handing_off(session.id)

    stored = await repository.get(session.id)
    assert stored.startup.handed_off_at is not None
    assert [(s.key, s.state) for s in stored.startup.stages] == [
        ("launch", StartupStageState.DONE),
        ("runtime", StartupStageState.ACTIVE),
    ]


async def test_a_reporting_backend_continues_the_record_itself(repository, broadcaster):
    session = await starting(repository)
    startups = recorder(repository, StagedPodManager(), broadcaster)
    await startups.begin(session)

    await startups.handing_off(session.id)

    stored = await repository.get(session.id)
    assert stored.startup.handed_off_at is not None
    assert [s.key for s in stored.startup.stages] == ["launch"]


async def test_the_watch_merges_backend_stages_after_the_hand_off(repository, broadcaster):
    session = await starting(repository)
    backend = StagedPodManager()
    backend.stages = (schedule(detail="0/3 nodes are available"),)
    startups = recorder(repository, backend, broadcaster)
    await startups.begin(session)

    startups.watch(session.id)
    await asyncio.sleep(0.05)
    assert backend.reads == 0, "the backend is not read before Forge hands the launch over"

    await startups.handing_off(session.id)

    async def scheduled():
        stored = await repository.get(session.id)
        return any(s.key == "schedule" for s in stored.startup.stages)

    await wait_until(scheduled)
    stored = await repository.get(session.id)
    assert stored.startup.current.detail == "0/3 nodes are available"
    assert stored.startup.stages[0].state is StartupStageState.DONE

    await repository.update(stored.with_status(SessionStatus.RUNNING))

    async def closed():
        current = await repository.get(session.id)
        return current.startup.current is None

    await wait_until(closed)


async def test_a_failed_read_is_recorded_on_the_launch(repository, broadcaster, caplog):
    session = await starting(repository)
    backend = StagedPodManager()
    backend.read_error = RuntimeError("may not list events in namespace 'skuld'")
    startups = recorder(repository, backend, broadcaster)
    await startups.begin(session)
    await startups.handing_off(session.id)

    startups.watch(session.id)

    async def recorded():
        stored = await repository.get(session.id)
        return stored.startup.progress_error is not None

    await wait_until(recorded)
    stored = await repository.get(session.id)
    assert stored.startup.progress_error == "may not list events in namespace 'skuld'"
    assert "Could not read the startup stages" in caplog.text

    backend.read_error = None
    backend.stages = (schedule(),)

    async def cleared():
        current = await repository.get(session.id)
        return current.startup.progress_error is None

    await wait_until(cleared)
    await repository.update(stored.with_status(SessionStatus.STOPPED))


async def test_a_failure_marks_the_stage_it_happened_in(repository, broadcaster):
    session = await starting(repository)
    backend = StagedPodManager()
    backend.stages = (schedule(),)
    startups = recorder(repository, backend, broadcaster)
    await startups.begin(session)
    await startups.handing_off(session.id)
    startups.watch(session.id)

    async def scheduled():
        stored = await repository.get(session.id)
        return stored.startup.current.key == "schedule"

    await wait_until(scheduled)
    stored = await repository.get(session.id)
    await repository.update(
        stored.with_status(SessionStatus.FAILED).with_error("Insufficient memory")
    )

    async def failed():
        current = await repository.get(session.id)
        return current.startup.current.state is StartupStageState.FAILED

    await wait_until(failed)
    current = await repository.get(session.id)
    assert current.startup.current.key == "schedule"
    assert current.startup.current.detail == "Insufficient memory"


async def test_stopping_while_starting_ends_the_open_stage(repository, broadcaster):
    session = await starting(repository, status=SessionStatus.STOPPED)
    startups = recorder(repository, MockPodManager(), broadcaster)
    await startups.begin(session)

    startups.watch(session.id)

    async def closed():
        current = await repository.get(session.id)
        return current.startup.current.state is StartupStageState.FAILED

    await wait_until(closed)
    current = await repository.get(session.id)
    assert current.startup.current.detail == "The session was stopped before it was ready"


async def test_a_session_without_a_launch_record_is_left_alone(repository, broadcaster):
    session = await starting(repository)
    startups = recorder(repository, StagedPodManager(), broadcaster)

    await startups.handing_off(session.id)
    startups.watch(session.id)
    await asyncio.sleep(0.05)

    assert (await repository.get(session.id)).startup is None
    assert broadcaster._session_updated_events == []


async def test_a_deleted_session_ends_the_watch(repository, broadcaster):
    session = await starting(repository)
    startups = recorder(repository, StagedPodManager(), broadcaster)
    await startups.begin(session)
    await repository.delete(session.id)

    startups.watch(session.id)
    await asyncio.sleep(0.05)

    assert startups._watches == {}
    assert await startups.begin(session) is None


async def test_watching_twice_runs_one_watch(repository, broadcaster):
    session = await starting(repository)
    startups = recorder(repository, StagedPodManager(), broadcaster, poll=10)
    await startups.begin(session)

    startups.watch(session.id)
    first = startups._watches[session.id]
    startups.watch(session.id)

    assert startups._watches[session.id] is first
    first.cancel()


async def test_starting_a_session_records_its_launch_until_running(repository, broadcaster):
    backend = StagedPodManager()
    backend.stages = (schedule(StartupStageState.DONE, "On node valhalla-w-07"),)
    service = SessionService(
        repository,
        backend,
        broadcaster=broadcaster,
        provisioning_initial_delay=0,
        startup_poll_seconds=0.01,
    )
    session = await repository.create(Session(name="boot"))

    await service.start_session(session.id)

    async def running_and_closed():
        current = await repository.get(session.id)
        return (
            current.status is SessionStatus.RUNNING
            and current.startup is not None
            and current.startup.current is None
            and any(s.key == "schedule" for s in current.startup.stages)
        )

    await wait_until(running_and_closed)
    current = await repository.get(session.id)
    assert current.startup.handed_off_at is not None
    assert [s.key for s in current.startup.stages] == ["launch", "schedule"]
    assert current.startup.stages[1].detail == "On node valhalla-w-07"


async def test_reconciling_after_a_restart_watches_starting_sessions(repository, broadcaster):
    backend = StagedPodManager()
    service = SessionService(repository, backend, broadcaster=broadcaster, startup_poll_seconds=10)
    first = await starting(repository)
    second = await starting(repository, status=SessionStatus.PROVISIONING)

    await service.reconcile_provisioning_sessions()

    assert set(service._startup._watches) == {first.id, second.id}
    for task in [*service._startup._watches.values(), *service._provisioning_tasks.values()]:
        task.cancel()


async def test_the_last_steps_before_running_are_read_before_the_launch_closes(
    repository, broadcaster
):
    """Seen live on Noatun: OpenShell reports running as soon as its sandbox is up,
    and the steps it ran since the previous read never reached the record."""
    session = await starting(repository)
    backend = StagedPodManager()
    backend.stages = (schedule(StartupStageState.DONE, "sandbox ready"),)
    startups = recorder(repository, backend, broadcaster, poll=10)
    await startups.begin(session)
    await startups.handing_off(session.id)
    stored = await repository.get(session.id)
    await repository.update(stored.with_status(SessionStatus.RUNNING))

    startups.watch(session.id)

    async def closed():
        current = await repository.get(session.id)
        return current.startup.current is None and len(current.startup.stages) == 2

    await wait_until(closed)
    current = await repository.get(session.id)
    assert [s.key for s in current.startup.stages] == ["launch", "schedule"]
    assert current.startup.stages[1].detail == "sandbox ready"


async def test_a_stopped_launch_is_closed_without_reading_the_backend(repository, broadcaster):
    session = await starting(repository, status=SessionStatus.STOPPED)
    backend = StagedPodManager()
    startups = recorder(repository, backend, broadcaster, poll=10)
    await startups.begin(session)
    await startups.handing_off(session.id)

    startups.watch(session.id)

    async def closed():
        current = await repository.get(session.id)
        return current.startup.current.state is StartupStageState.FAILED

    await wait_until(closed)
    assert backend.reads == 0
