"""A Kubernetes session's launch stages, read from the cluster's own record.

The HelmRelease says when Flux was asked to install the session; the session
pod's conditions say when it was scheduled, initialized and ready (each with
the time the kubelet or scheduler changed it); the pod's events say what the
kubelet is doing in between (attaching a volume, pulling an image) in its own
words. Nothing here is estimated.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from volundr.domain.session_startup import StartupStage, StartupStageState

# Kubelet event reasons that mean a container of the pod is being set up, which
# the kubelet only does once every volume of the pod is attached and mounted.
_CONTAINER_EVENT_REASONS = frozenset({"Pulling", "Pulled", "Created", "Started"})
_VOLUME_EVENT_REASONS = frozenset(
    {"SuccessfulAttachVolume", "FailedAttachVolume", "FailedMount", "SuccessfulMountVolume"}
)
# Waiting reasons that are the ordinary steps of starting a container, not news.
_ROUTINE_WAITING_REASONS = frozenset({"PodInitializing", "ContainerCreating"})
# Events that say what the kubelet is doing now, not that it finished something.
_PROGRESS_EVENT_REASONS = frozenset({"Pulling", "Pulled", "Created"})

PENDING = StartupStageState.PENDING
ACTIVE = StartupStageState.ACTIVE
DONE = StartupStageState.DONE


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def event_time(event: Any) -> datetime | None:
    """When a Kubernetes event last happened; events carry one of three stamps."""
    return _parse_time(event.last_timestamp or event.event_time or event.first_timestamp)


def _condition(pod: Any, kind: str) -> Any | None:
    return next(
        (c for c in (pod.status.conditions or []) if c.type == kind),
        None,
    )


def _condition_true_at(pod: Any, kind: str) -> datetime | None:
    condition = _condition(pod, kind)
    if condition is None or condition.status != "True":
        return None
    return _parse_time(condition.last_transition_time)


def _release_ready(release: dict) -> dict | None:
    conditions = (release.get("status") or {}).get("conditions") or []
    return next((c for c in conditions if c.get("type") == "Ready"), None)


def _stage(
    key: str,
    label: str,
    started_at: datetime | None,
    finished_at: datetime | None,
    detail: str | None,
) -> StartupStage:
    if started_at is None:
        return StartupStage(key=key, label=label, state=PENDING)
    if finished_at is None:
        return StartupStage(
            key=key, label=label, state=ACTIVE, detail=detail, started_at=started_at
        )
    return StartupStage(
        key=key,
        label=label,
        state=DONE,
        detail=detail,
        started_at=started_at,
        finished_at=max(started_at, finished_at),
    )


def _latest(events: Iterable[Any], reasons: frozenset[str] | None = None) -> Any | None:
    chosen = [e for e in events if reasons is None or e.reason in reasons]
    timed = [(event_time(e), e) for e in chosen]
    timed = [(t, e) for t, e in timed if t is not None]
    if not timed:
        return None
    return max(timed, key=lambda item: item[0])[1]


def _container_detail(statuses: Sequence[Any] | None, events: Sequence[Any]) -> str | None:
    """What the kubelet is doing with a group of containers, in its own words."""
    for status in statuses or []:
        waiting = status.state.waiting if status.state else None
        if waiting is not None and waiting.reason not in _ROUTINE_WAITING_REASONS:
            message = f": {waiting.message}" if waiting.message else ""
            return f"{status.name}: {waiting.reason}{message}"
    progress = _latest(events, _PROGRESS_EVENT_REASONS)
    latest = _latest(events)
    if progress is not None and progress is latest:
        return progress.message
    unready = [
        status.name
        for status in statuses or []
        if status.state and status.state.running is not None and not status.ready
    ]
    if unready:
        return f"Waiting for {', '.join(unready)} to pass its readiness check"
    return latest.message if latest is not None else None


def _since(events: Sequence[Any], start: datetime | None) -> list[Any]:
    if start is None:
        return []
    return [e for e in events if (event_time(e) or start) >= start]


def pod_startup_stages(
    release: dict, pod: Any | None, events: Sequence[Any]
) -> tuple[StartupStage, ...]:
    """The stages of a Flux-installed session pod, oldest first.

    ``events`` are the events of ``pod`` (the session's newest pod), and
    nothing else, so a previous launch's pod never speaks for this one.
    """
    created = _parse_time((release.get("metadata") or {}).get("creationTimestamp"))
    ready = _release_ready(release)
    ready_message = (ready or {}).get("message") or None
    if pod is None:
        return (
            _stage("release", "Applying Helm release", created, None, ready_message),
            _stage("schedule", "Scheduling", None, None, None),
            _stage("containers", "Starting containers", None, None, None),
            _stage("release_ready", "Finishing the Helm release", None, None, None),
        )

    pod_created = _parse_time(pod.metadata.creation_timestamp)
    stages = [_stage("release", "Applying Helm release", created, pod_created, None)]

    scheduled_at = _condition_true_at(pod, "PodScheduled")
    if scheduled_at is not None:
        schedule_detail = f"On node {pod.spec.node_name}" if pod.spec.node_name else None
    else:
        condition = _condition(pod, "PodScheduled")
        unscheduled = _latest(events, frozenset({"FailedScheduling"}))
        schedule_detail = (condition.message if condition is not None else None) or (
            unscheduled.message if unscheduled is not None else None
        )
    stages.append(_stage("schedule", "Scheduling", pod_created, scheduled_at, schedule_detail))

    first_container_event = min(
        (t for e in events if e.reason in _CONTAINER_EVENT_REASONS if (t := event_time(e))),
        default=None,
    )
    containers_from = scheduled_at
    if any(v.persistent_volume_claim is not None for v in pod.spec.volumes or []):
        volumes_done = first_container_event if scheduled_at is not None else None
        attach = _latest(_since(events, scheduled_at), _VOLUME_EVENT_REASONS)
        stages.append(
            _stage(
                "volumes",
                "Attaching volumes",
                scheduled_at,
                volumes_done,
                attach.message if attach is not None else None,
            )
        )
        containers_from = volumes_done

    if pod.spec.init_containers:
        initialized_at = _condition_true_at(pod, "Initialized")
        stages.append(
            _stage(
                "init",
                "Running init containers",
                containers_from,
                initialized_at if containers_from is not None else None,
                _container_detail(
                    pod.status.init_container_statuses, _since(events, containers_from)
                ),
            )
        )
        containers_from = initialized_at if containers_from is not None else None

    containers_ready_at = _condition_true_at(pod, "ContainersReady")
    stages.append(
        _stage(
            "containers",
            "Starting containers",
            containers_from,
            containers_ready_at if containers_from is not None else None,
            _container_detail(pod.status.container_statuses, _since(events, containers_from)),
        )
    )

    release_ready_at = (
        _parse_time(ready.get("lastTransitionTime"))
        if ready is not None and ready.get("status") == "True"
        else None
    )
    stages.append(
        _stage(
            "release_ready",
            "Finishing the Helm release",
            containers_ready_at if containers_from is not None else None,
            release_ready_at,
            None if release_ready_at else ready_message,
        )
    )
    return tuple(stages)
