"""Tests for the Kubernetes launch stages read from a HelmRelease, pod and events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from volundr.adapters.outbound.k8s_startup_stages import event_time, pod_startup_stages
from volundr.domain.session_startup import StartupStageState

T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def release(*, created: datetime | None = T0, ready: dict | None = None) -> dict:
    body: dict = {"metadata": {}, "status": {"conditions": []}}
    if created is not None:
        body["metadata"]["creationTimestamp"] = created.isoformat()
    if ready is not None:
        body["status"]["conditions"].append({"type": "Ready", **ready})
    return body


def cond(kind: str, status: str = "True", when: datetime | None = None, message=None):
    return SimpleNamespace(type=kind, status=status, last_transition_time=when, message=message)


def container(name: str, *, ready=False, waiting=None, running=False):
    return SimpleNamespace(
        name=name,
        ready=ready,
        state=SimpleNamespace(
            waiting=SimpleNamespace(reason=waiting[0], message=waiting[1]) if waiting else None,
            running=SimpleNamespace() if running else None,
        ),
    )


def pod(
    *,
    created: datetime = at(1),
    conditions=(),
    node: str | None = None,
    pvc: bool = False,
    init: bool = False,
    init_statuses=None,
    statuses=None,
):
    return SimpleNamespace(
        metadata=SimpleNamespace(creation_timestamp=created),
        spec=SimpleNamespace(
            node_name=node,
            volumes=[
                SimpleNamespace(
                    persistent_volume_claim=SimpleNamespace(claim_name="ws") if pvc else None
                )
            ],
            init_containers=[SimpleNamespace(name="init")] if init else [],
        ),
        status=SimpleNamespace(
            conditions=list(conditions),
            init_container_statuses=init_statuses,
            container_statuses=statuses,
        ),
    )


def event(reason: str, message: str, when: datetime | str | None, *, field="last_timestamp"):
    values = {"last_timestamp": None, "event_time": None, "first_timestamp": None}
    values[field] = when
    return SimpleNamespace(reason=reason, message=message, **values)


def by_key(stages):
    return {s.key: s for s in stages}


def scheduled_pod(**kwargs):
    conditions = [cond("PodScheduled", when=at(2)), *kwargs.pop("conditions", ())]
    return pod(conditions=conditions, node="hnoss", **kwargs)


class TestNoPodYet:
    def test_release_active_and_remaining_stages_pending(self):
        stages = pod_startup_stages(release(), None, [])

        assert [s.key for s in stages] == ["release", "schedule", "containers", "release_ready"]
        assert [s.state for s in stages] == [
            StartupStageState.ACTIVE,
            StartupStageState.PENDING,
            StartupStageState.PENDING,
            StartupStageState.PENDING,
        ]
        assert stages[0].started_at == T0

    def test_helmrelease_ready_message_is_the_detail(self):
        ready = {"status": "False", "message": "Running 'install' action with timeout of 5m0s"}

        stages = pod_startup_stages(release(ready=ready), None, [])

        assert stages[0].detail == "Running 'install' action with timeout of 5m0s"

    def test_release_without_creation_timestamp_is_pending(self):
        stages = pod_startup_stages(release(created=None), None, [])

        assert stages[0].state is StartupStageState.PENDING
        assert stages[0].started_at is None


class TestSchedule:
    def test_unscheduled_pod_shows_scheduler_condition_message(self):
        unscheduled = pod(
            conditions=[cond("PodScheduled", "False", at(1), "0/3 nodes are available")]
        )

        stages = by_key(pod_startup_stages(release(), unscheduled, []))

        assert stages["release"].state is StartupStageState.DONE
        assert stages["release"].finished_at == at(1)
        assert stages["schedule"].state is StartupStageState.ACTIVE
        assert stages["schedule"].detail == "0/3 nodes are available"
        assert stages["containers"].state is StartupStageState.PENDING

    def test_unscheduled_pod_falls_back_to_failed_scheduling_event(self):
        unscheduled = pod(conditions=[cond("PodScheduled", "False", at(1), None)])
        events = [
            event("FailedScheduling", "old", at(2)),
            event("FailedScheduling", "Insufficient cpu", at(5)),
        ]

        stages = by_key(pod_startup_stages(release(), unscheduled, events))

        assert stages["schedule"].detail == "Insufficient cpu"

    def test_pod_without_conditions_is_scheduling_without_detail(self):
        stages = by_key(pod_startup_stages(release(), pod(), []))

        assert stages["schedule"].state is StartupStageState.ACTIVE
        assert stages["schedule"].detail is None

    def test_scheduled_pod_names_its_node(self):
        stages = by_key(pod_startup_stages(release(), scheduled_pod(), []))

        assert stages["schedule"].state is StartupStageState.DONE
        assert stages["schedule"].detail == "On node hnoss"
        assert stages["schedule"].started_at == at(1)
        assert stages["schedule"].finished_at == at(2)

    def test_scheduled_pod_without_node_name_has_no_detail(self):
        scheduled = pod(conditions=[cond("PodScheduled", when=at(2))])

        stages = by_key(pod_startup_stages(release(), scheduled, []))

        assert stages["schedule"].detail is None

    def test_finish_never_precedes_start(self):
        early = pod(created=at(10), conditions=[cond("PodScheduled", when=at(3))])

        stage = by_key(pod_startup_stages(release(), early, []))["schedule"]

        assert stage.finished_at == stage.started_at == at(10)


class TestVolumes:
    def test_waiting_for_attach_shows_the_attach_event(self):
        events = [
            event("SuccessfulAttachVolume", "AttachVolume.Attach succeeded", at(3)),
            event("FailedMount", "before the schedule", at(1)),
        ]

        stages = by_key(pod_startup_stages(release(), scheduled_pod(pvc=True), events))

        assert stages["volumes"].state is StartupStageState.ACTIVE
        assert stages["volumes"].started_at == at(2)
        assert stages["volumes"].detail == "AttachVolume.Attach succeeded"
        assert stages["containers"].state is StartupStageState.PENDING

    def test_done_at_the_first_container_event(self):
        events = [
            event("SuccessfulAttachVolume", "attached", at(3)),
            event("Pulled", "later", at(9)),
            event("Pulling", "Pulling image", at(5)),
        ]

        stages = by_key(pod_startup_stages(release(), scheduled_pod(pvc=True), events))

        assert stages["volumes"].state is StartupStageState.DONE
        assert stages["volumes"].finished_at == at(5)
        assert stages["containers"].started_at == at(5)

    def test_unscheduled_pod_does_not_attach_volumes(self):
        stages = by_key(pod_startup_stages(release(), pod(pvc=True), []))

        assert stages["volumes"].state is StartupStageState.PENDING

    def test_pod_without_pvc_skips_the_stage(self):
        stages = pod_startup_stages(release(), scheduled_pod(), [])

        assert "volumes" not in [s.key for s in stages]


class TestInitContainers:
    def test_pod_without_init_containers_skips_the_stage(self):
        stages = pod_startup_stages(release(), scheduled_pod(), [])

        assert "init" not in [s.key for s in stages]

    def test_done_at_initialized(self):
        started = scheduled_pod(init=True, conditions=[cond("Initialized", when=at(8))])

        stages = by_key(pod_startup_stages(release(), started, []))

        assert stages["init"].state is StartupStageState.DONE
        assert stages["init"].started_at == at(2)
        assert stages["init"].finished_at == at(8)
        assert stages["containers"].started_at == at(8)

    def test_non_routine_waiting_reason_is_the_detail(self):
        stuck = scheduled_pod(
            init=True,
            init_statuses=[
                container("init", waiting=("ErrImagePull", "rpc error: manifest unknown"))
            ],
        )

        stages = by_key(pod_startup_stages(release(), stuck, []))

        assert stages["init"].state is StartupStageState.ACTIVE
        assert stages["init"].detail == "init: ErrImagePull: rpc error: manifest unknown"
        assert stages["containers"].state is StartupStageState.PENDING

    def test_waiting_reason_without_message(self):
        stuck = scheduled_pod(
            init=True, init_statuses=[container("init", waiting=("ImagePullBackOff", None))]
        )

        stages = by_key(pod_startup_stages(release(), stuck, []))

        assert stages["init"].detail == "init: ImagePullBackOff"

    def test_routine_waiting_reason_is_not_news(self):
        waiting = scheduled_pod(
            init=True,
            init_statuses=[container("init", waiting=("PodInitializing", None))],
        )
        events = [event("Pulling", "Pulling image init:1", at(4))]

        stages = by_key(pod_startup_stages(release(), waiting, events))

        assert stages["init"].detail == "Pulling image init:1"

    def test_init_follows_volumes(self):
        events = [event("Pulling", "Pulling image", at(5))]

        stages = pod_startup_stages(release(), scheduled_pod(pvc=True, init=True), events)

        assert [s.key for s in stages] == [
            "release",
            "schedule",
            "volumes",
            "init",
            "containers",
            "release_ready",
        ]
        assert by_key(stages)["init"].started_at == at(5)


class TestContainers:
    def test_pulling_progress_event_is_the_detail(self):
        events = [event("Pulling", "Pulling image skuld:1", at(4))]
        starting = scheduled_pod(statuses=[container("skuld", waiting=("ContainerCreating", None))])

        stages = by_key(pod_startup_stages(release(), starting, events))

        assert stages["containers"].state is StartupStageState.ACTIVE
        assert stages["containers"].detail == "Pulling image skuld:1"

    def test_running_unready_container_reports_its_readiness(self):
        events = [
            event("Created", "Created container skuld", at(4)),
            event("Started", "Started container skuld", at(5)),
        ]
        starting = scheduled_pod(
            statuses=[container("skuld", running=True), container("sidecar", running=True)]
        )

        stages = by_key(pod_startup_stages(release(), starting, events))

        assert stages["containers"].detail == (
            "Waiting for skuld, sidecar to pass its readiness check"
        )

    def test_ready_running_container_does_not_claim_waiting(self):
        events = [event("Started", "Started container skuld", at(5))]
        starting = scheduled_pod(statuses=[container("skuld", running=True, ready=True)])

        stages = by_key(pod_startup_stages(release(), starting, events))

        assert stages["containers"].detail == "Started container skuld"

    def test_no_events_and_no_statuses_has_no_detail(self):
        stages = by_key(pod_startup_stages(release(), scheduled_pod(), []))

        assert stages["containers"].detail is None

    def test_events_before_the_stage_started_are_ignored(self):
        events = [event("Pulling", "left over from the schedule", at(0))]

        stages = by_key(pod_startup_stages(release(), scheduled_pod(), events))

        assert stages["containers"].detail is None

    def test_done_when_containers_are_ready(self):
        ready = scheduled_pod(conditions=[cond("ContainersReady", when=at(12))])

        stages = by_key(pod_startup_stages(release(), ready, []))

        assert stages["containers"].state is StartupStageState.DONE
        assert stages["containers"].finished_at == at(12)
        assert stages["release_ready"].state is StartupStageState.ACTIVE
        assert stages["release_ready"].started_at == at(12)


class TestReleaseReady:
    def test_done_when_the_helmrelease_ready_condition_is_true(self):
        ready = scheduled_pod(conditions=[cond("ContainersReady", when=at(12))])
        rel = release(ready={"status": "True", "lastTransitionTime": at(15).isoformat()})

        stages = by_key(pod_startup_stages(rel, ready, []))

        assert stages["release_ready"].state is StartupStageState.DONE
        assert stages["release_ready"].finished_at == at(15)
        assert stages["release_ready"].detail is None

    def test_not_ready_shows_the_release_message(self):
        ready = scheduled_pod(conditions=[cond("ContainersReady", when=at(12))])
        rel = release(ready={"status": "False", "message": "waiting for helm test"})

        stages = by_key(pod_startup_stages(rel, ready, []))

        assert stages["release_ready"].state is StartupStageState.ACTIVE
        assert stages["release_ready"].detail == "waiting for helm test"

    def test_pending_until_the_containers_are_ready(self):
        stages = by_key(pod_startup_stages(release(), scheduled_pod(), []))

        assert stages["release_ready"].state is StartupStageState.PENDING


class TestTimes:
    def test_iso_strings_with_zulu_suffix_parse_as_utc(self):
        iso = scheduled_pod()
        iso.metadata.creation_timestamp = "2026-11-06T12:00:01Z"
        iso.status.conditions = [cond("PodScheduled", when="2026-11-06T12:00:02Z")]

        stages = by_key(pod_startup_stages(release(), iso, []))

        assert stages["schedule"].started_at == at(1)
        assert stages["schedule"].finished_at == at(2)

    def test_naive_datetimes_are_utc(self):
        naive = scheduled_pod()
        naive.metadata.creation_timestamp = datetime(2026, 11, 6, 12, 0, 1)
        naive.status.conditions = [cond("PodScheduled", when=datetime(2026, 11, 6, 12, 0, 2))]

        stages = by_key(pod_startup_stages(release(), naive, []))

        assert stages["schedule"].started_at == at(1)
        assert stages["schedule"].finished_at == at(2)

    def test_malformed_time_raises(self):
        broken = scheduled_pod()
        broken.status.conditions = [cond("PodScheduled", when="not a time")]

        with pytest.raises(ValueError):
            pod_startup_stages(release(), broken, [])


class TestEventTime:
    def test_prefers_last_timestamp(self):
        item = SimpleNamespace(last_timestamp=at(3), event_time=at(2), first_timestamp=at(1))

        assert event_time(item) == at(3)

    def test_falls_through_to_event_time_then_first_timestamp(self):
        assert event_time(
            SimpleNamespace(last_timestamp=None, event_time=at(2), first_timestamp=at(1))
        ) == at(2)
        assert event_time(
            SimpleNamespace(
                last_timestamp=None, event_time=None, first_timestamp="2026-11-06T12:00:01Z"
            )
        ) == at(1)

    def test_event_with_no_stamp_has_no_time(self):
        assert (
            event_time(SimpleNamespace(last_timestamp=None, event_time=None, first_timestamp=None))
            is None
        )

    def test_unstamped_events_are_never_chosen_as_latest(self):
        unstamped = event("Pulling", "no stamp", None)

        stages = by_key(pod_startup_stages(release(), scheduled_pod(), [unstamped]))

        assert stages["containers"].detail is None
