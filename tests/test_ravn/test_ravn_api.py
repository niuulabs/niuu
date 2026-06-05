"""Tests for the Ravn FastAPI sub-application."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ravn.api import create_app
from ravn.api.valkyries import (
    ValkyrieDashboardProjection,
    build_nats_telemetry_subscription_from_env,
)
from ravn.api.warden_stream import WardenStreamBroker
from ravn.ports.warden_deployer import (
    WardenDeploymentError,
    WardenDeploymentResult,
    WardenObservationResult,
)
from ravn.warden import WardenSpec, WardenStore
from ravn.warden.artifacts import service_label, start_command, write_runtime_config
from ravn.warden.models import WardenObservation, WardenSupervisor
from sleipnir.domain.events import SleipnirEvent


class FakeWardenDeployer:
    def __init__(self, *, fail_on: str = "") -> None:
        self._fail_on = fail_on

    def install(self, spec: WardenSpec, *, warden_dir: Path, workspace_root: Path | None = None):
        if self._fail_on == "install":
            raise WardenDeploymentError("install failed")
        config_path = write_runtime_config(
            spec,
            warden_dir=warden_dir,
            workspace_root=workspace_root,
        )
        service_path = warden_dir / "warden.plist"
        service_path.write_text("service", encoding="utf-8")
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(
                installed=True,
                service_label=service_label(spec.id),
                service_file=str(service_path),
                config_file=str(config_path),
                start_command=start_command(spec, config_path=config_path),
                last_install_at=datetime.now(UTC),
            ),
            runtime_state="idle",
        )

    def start(self, spec: WardenSpec, *, warden_dir: Path):
        if self._fail_on == "start":
            raise WardenDeploymentError("start failed")
        return WardenDeploymentResult(
            supervisor=spec.supervisor,
            runtime_state="active",
        )

    def stop(self, spec: WardenSpec, *, warden_dir: Path):
        if self._fail_on == "stop":
            raise WardenDeploymentError("stop failed")
        return WardenDeploymentResult(
            supervisor=spec.supervisor,
            runtime_state="idle",
        )

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path):
        if self._fail_on == "uninstall":
            raise WardenDeploymentError("uninstall failed")
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(),
            runtime_state="offline",
        )

    def observe(self, spec: WardenSpec, *, warden_dir: Path):
        del warden_dir
        if self._fail_on == "observe":
            raise WardenDeploymentError("observe failed")
        return WardenObservationResult(
            supervisor=spec.supervisor.model_copy(
                update={
                    "observation": WardenObservation(
                        status="idle",
                        detail="fake backend observed the warden as idle",
                        source="fake",
                    )
                }
            )
        )


def _store(tmp_path: Path, *, fail_on: str = "") -> WardenStore:
    return WardenStore(
        root=tmp_path,
        deployer_factory=lambda spec: FakeWardenDeployer(fail_on=fail_on),
    )


def _valkyrie_catalog() -> str:
    return json.dumps(
        {
            "environments": [
                {
                    "id": "valhalla",
                    "name": "Valhalla k8s",
                    "kind": "kubernetes",
                    "health": "watch",
                    "flockId": "flock-k8s",
                    "flock": {
                        "name": "K8s Valkyrie flock",
                        "domain": "kubernetes operations",
                        "natsSubject": "flock.k8s.>",
                    },
                    "valkyrie": {
                        "valkyrieId": "valkyrie-valhalla-k8s",
                        "valkyrieName": "Sigrun",
                        "persona": "k8s-valkyrie",
                        "specialty": "cluster event triage and flock learning exchange",
                        "confidence": 0.82,
                    },
                    "transport": {
                        "account": "obs-valhalla",
                        "streamName": "obs-valhalla-events",
                        "subjectPrefix": "obs.valhalla",
                    },
                }
            ]
        }
    )


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", _valkyrie_catalog())
    return TestClient(create_app())


@pytest.fixture
def client_with_personas(tmp_path) -> TestClient:
    """TestClient wired with a filesystem persona loader."""

    from ravn.adapters.personas.loader import FilesystemPersonaAdapter

    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    loader = FilesystemPersonaAdapter(persona_dirs=[str(persona_dir)], include_builtin=True)
    return TestClient(create_app(persona_loader=loader))


def test_status_endpoint(client: TestClient):
    resp = client.get("/api/v1/ravn/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "ravn"
    assert data["healthy"] is True
    assert "session_count" in data


def test_valkyrie_dashboard_projection(client: TestClient):
    resp = client.get("/api/v1/ravn/valkyrie/dashboard")
    assert resp.status_code == 200

    data = resp.json()
    assert data["environments"][0]["name"] == "Valhalla k8s"
    assert data["flocks"][0]["natsSubject"] == "flock.k8s.>"
    assert data["liveReport"]["routeSubject"] == "obs.valhalla"
    assert data["telemetry"]["verified"] is False
    assert "demo projection" in data["telemetry"]["gaps"][1]
    assert data["signals"] == []
    assert data["learnings"] == []


def test_valkyrie_dashboard_aggregates_verified_telemetry_events():
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.runtime.started",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "valkyrie_name": "Sigrun",
                "resident_personality": "Evidence-first cluster guardian.",
                "source_count": 1,
                "drive_loop_enabled": True,
                "initiative_enabled": True,
                "poll_interval_seconds": 15,
                "llm_model": "Qwen/Qwen3.6-35B-A3B-FP8",
                "reflection_model": "Qwen/Qwen3.6-35B-A3B-FP8",
                "post_session_reflection_enabled": True,
            },
            summary="runtime started",
            urgency=0.2,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 0, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.signal_poll.completed",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "source_id": "kubernetes-events",
                "collected_count": 8,
                "published_count": 5,
                "duplicate_count": 3,
                "enqueued_task_count": 2,
                "duration_ms": 123,
            },
            summary="poll complete",
            urgency=0.4,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 1, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="learning.dream.completed",
            source="ravn:valkyrie:ymir",
            payload={"environment_id": "ymir", "dream_id": "dream:ymir:1"},
            summary="dream complete",
            urgency=0.2,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 2, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="learning.dream.noop",
            source="valkyrie:valkyrie-ymir-k8s",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "dream_id": "dream:ymir:noop",
                "summary": "No improvement extracted.",
            },
            summary="dream produced no improvement",
            urgency=0.1,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 2, 30, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.judgment.proposed",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "task_id": "task-k8s-1",
                "fields": {
                    "verdict": "investigate",
                    "tier": "present",
                    "confidence": 0.84,
                    "recommended_action": "k8s.inspect_pod",
                    "summary": "Persistent ImagePullBackOff requires inspection.",
                },
            },
            summary="judgment proposed",
            urgency=0.7,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 3, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="ravn.task.dropped",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "task_id": "task-budget",
                "title": "Budget capped task",
                "reason": "daily budget cap reached",
                "persona": "k8s-valkyrie",
            },
            summary="task dropped",
            urgency=0.5,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 5, 30, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="ravn.llm.call.completed",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "model": "Qwen/Qwen3.6-35B-A3B-FP8",
                "total_tokens": 42,
            },
            summary="llm call completed",
            urgency=0.1,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 6, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="ravn.log.warning",
            source="ravn.drive_loop",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "valkyrie_name": "Sigrun",
                "level": "warning",
                "component": "drive_loop",
                "message": "daily budget warning",
                "task_id": "task-budget",
            },
            summary="daily budget warning",
            urgency=0.3,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 7, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.action.proposed",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "task_id": "task-k8s-1",
                "fields": {
                    "action_capability": "k8s.inspect_pod",
                    "summary": "Needs pod logs before remediation.",
                },
            },
            summary="action proposed",
            urgency=0.6,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 4, tzinfo=UTC),
        )
    )
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.wakefulness.changed",
            source="valkyrie:valkyrie-ymir-k8s",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "previous_state": "watchful",
                "new_state": "dreaming",
                "reason": "dream cycle started",
            },
            summary="wakefulness changed",
            urgency=0.2,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 5, tzinfo=UTC),
        )
    )

    telemetry = projection.dashboard()["telemetry"]

    assert telemetry["verified"] is True
    assert telemetry["source"] == "sleipnir_events"
    assert telemetry["totals"]["eventsObserved"] == 10
    assert telemetry["totals"]["pollsCompleted"] == 1
    assert telemetry["totals"]["signalsCollected"] == 8
    assert telemetry["totals"]["signalsPublished"] == 5
    assert telemetry["totals"]["duplicateSignals"] == 3
    assert telemetry["totals"]["tasksEnqueued"] == 2
    assert telemetry["totals"]["learningEvents"] == 2
    assert telemetry["totals"]["dreamCyclesCompleted"] == 2
    assert telemetry["totals"]["dreamCyclesNoop"] == 1
    assert telemetry["totals"]["judgments"] == 1
    assert telemetry["totals"]["actions"] == 1
    assert telemetry["totals"]["toolRequests"] == 1
    assert telemetry["totals"]["wakefulnessChanges"] == 1
    assert telemetry["totals"]["llmCalls"] == 1
    assert telemetry["totals"]["llmTokens"] == 42
    assert telemetry["totals"]["logEvents"] == 1
    assert telemetry["totals"]["budgetDrops"] == 1
    assert telemetry["byEnvironment"][0]["environmentId"] == "ymir"
    assert telemetry["byEnvironment"][0]["tasksEnqueued"] == 2
    assert telemetry["byEnvironment"][0]["judgments"] == 1
    assert telemetry["recentOutcomes"][0]["type"] == "action"
    assert telemetry["recentOutcomes"][1]["taskId"] == "task-k8s-1"
    assert (
        telemetry["recentOutcomes"][1]["summary"]
        == "Persistent ImagePullBackOff requires inspection."
    )
    assert telemetry["recentEvents"][0]["kind"] == "log"
    assert telemetry["recentEvents"][0]["environmentId"] == "ymir"
    assert telemetry["recentLogs"][0]["message"] == "daily budget warning"
    assert telemetry["recentLearning"][0]["status"] == "wakefulness"
    assert telemetry["recentToolNeeds"][0]["capability"] == "k8s.inspect_pod"
    assert telemetry["recentPolls"][0]["sourceId"] == "kubernetes-events"
    assert telemetry["runtime"][0]["driveLoopEnabled"] is True
    assert telemetry["runtime"][0]["valkyrieName"] == "Sigrun"
    assert telemetry["runtime"][0]["residentPersonality"] == "Evidence-first cluster guardian."
    assert any(event.get("valkyrieName") == "Sigrun" for event in telemetry["recentEvents"])
    assert telemetry["llm"]["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert projection.logs()[0]["component"] == "drive_loop"


def test_valkyrie_dashboard_marks_observed_runtime_identity(monkeypatch):
    monkeypatch.setenv("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", _valkyrie_catalog())
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.presence.announced",
            source="ravn:valkyrie:valhalla",
            payload={
                "environment_id": "valhalla",
                "valkyrie_id": "valkyrie-valhalla-k8s",
                "valkyrie_name": "Runa",
                "resident_personality": "Pattern-minded state maintainer.",
                "source_count": 2,
                "drive_loop_enabled": True,
                "initiative_enabled": True,
                "poll_interval_seconds": 15,
            },
            summary="presence announced",
            urgency=0.1,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 0, tzinfo=UTC),
        )
    )

    dashboard = projection.dashboard()

    valkyrie = dashboard["valkyries"][0]
    assert valkyrie["name"] == "Runa"
    assert valkyrie["identitySource"] == "observed"
    assert valkyrie["specialty"] == "Pattern-minded state maintainer."
    assert dashboard["environments"][0]["identitySource"] == "observed"
    assert dashboard["telemetry"]["runtime"][0]["valkyrieName"] == "Runa"


def test_valkyrie_dashboard_keeps_runtime_telemetry_when_raw_signals_are_noisy():
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.runtime.started",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "valkyrie_name": "Sigrun",
                "source_count": 1,
                "drive_loop_enabled": True,
                "initiative_enabled": True,
                "poll_interval_seconds": 15,
                "llm_model": "Qwen/Qwen3.6-35B-A3B-FP8",
                "reflection_model": "Qwen/Qwen3.6-35B-A3B-FP8",
                "post_session_reflection_enabled": True,
            },
            summary="runtime started",
            urgency=0.2,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 0, tzinfo=UTC),
        )
    )
    for index in range(1_200):
        projection.record_event(
            SleipnirEvent(
                event_type="signal.kubernetes.event",
                source="ravn:valkyrie:ymir",
                payload={
                    "environment_id": "ymir",
                    "signal_id": f"signal-{index}",
                },
                summary="raw signal",
                urgency=0.1,
                domain="infrastructure",
                timestamp=datetime(2026, 6, 4, 20, 1, tzinfo=UTC),
            )
        )

    telemetry = projection.dashboard()["telemetry"]

    assert telemetry["totals"]["rawSignalEvents"] == 1_000
    assert telemetry["runtime"][0]["valkyrieId"] == "valkyrie-ymir-k8s"
    assert telemetry["runtime"][0]["valkyrieName"] == "Sigrun"
    assert telemetry["llm"]["status"] == "configured"
    assert telemetry["llm"]["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert "No valkyrie.runtime.started events observed." not in telemetry["gaps"]


def test_valkyrie_dashboard_keeps_runtime_telemetry_when_control_events_are_noisy():
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.runtime.started",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "valkyrie_name": "Sigrun",
                "source_count": 1,
                "drive_loop_enabled": True,
                "initiative_enabled": True,
                "poll_interval_seconds": 15,
                "llm_model": "Qwen/Qwen3.6-35B-A3B-FP8",
                "reflection_model": "Qwen/Qwen3.6-35B-A3B-FP8",
                "post_session_reflection_enabled": True,
            },
            summary="runtime started",
            urgency=0.2,
            domain="infrastructure",
            timestamp=datetime(2026, 6, 4, 20, 0, tzinfo=UTC),
        )
    )
    for index in range(2_200):
        projection.record_event(
            SleipnirEvent(
                event_type="valkyrie.signal_poll.completed",
                source="ravn:valkyrie:ymir",
                payload={
                    "environment_id": "ymir",
                    "source_id": "kubernetes-events",
                    "collected_count": 1,
                    "published_count": 0,
                    "duplicate_count": 1,
                    "enqueued_task_count": 0,
                    "duration_ms": 10,
                },
                summary=f"poll complete {index}",
                urgency=0.1,
                domain="infrastructure",
                timestamp=datetime(2026, 6, 4, 20, 1, tzinfo=UTC),
            )
        )

    telemetry = projection.dashboard()["telemetry"]

    assert telemetry["totals"]["pollsCompleted"] == 2_000
    assert telemetry["runtime"][0]["valkyrieId"] == "valkyrie-ymir-k8s"
    assert telemetry["runtime"][0]["valkyrieName"] == "Sigrun"
    assert telemetry["llm"]["status"] == "configured"
    assert telemetry["llm"]["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert "No valkyrie.runtime.started events observed." not in telemetry["gaps"]


def test_valkyrie_dashboard_uses_configured_environment_catalog(monkeypatch):
    monkeypatch.setenv(
        "RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON",
        json.dumps(
            {
                "environments": [
                    {
                        "id": "asgard",
                        "name": "Asgard k8s",
                        "kind": "kubernetes",
                        "health": "healthy",
                        "flockId": "flock-k8s",
                        "flock": {
                            "name": "K8s Valkyries",
                            "domain": "kubernetes operations",
                            "natsSubject": "flock.k8s.>",
                        },
                        "valkyrie": {
                            "valkyrieId": "valkyrie-asgard-k8s",
                            "valkyrieName": "Mist",
                            "persona": "k8s-valkyrie",
                            "specialty": "cluster operations",
                            "confidence": 0.74,
                            "inboxSubjects": ["signal.kubernetes.*"],
                        },
                        "transport": {
                            "account": "obs-asgard",
                            "streamName": "obs-asgard-events",
                            "subjectPrefix": "obs.asgard",
                        },
                    }
                ]
            }
        ),
    )

    dashboard = ValkyrieDashboardProjection().dashboard()

    assert [environment["id"] for environment in dashboard["environments"]] == ["env-k8s-asgard"]
    assert dashboard["environments"][0]["name"] == "Asgard k8s"
    assert [valkyrie["id"] for valkyrie in dashboard["valkyries"]] == ["valkyrie-asgard-k8s"]
    assert dashboard["valkyries"][0]["name"] == "Mist"
    assert dashboard["flocks"][0]["environmentIds"] == ["env-k8s-asgard"]
    assert dashboard["liveReport"]["transports"][0]["streamName"] == "obs-asgard-events"
    assert dashboard["signals"] == []
    assert dashboard["learnings"] == []


def test_valkyrie_dashboard_telemetry_nats_subscription_is_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("NATS_URL", "nats://should-not-be-used:4222")
    monkeypatch.delenv("RAVN_VALKYRIE_TELEMETRY_NATS_URL", raising=False)

    assert build_nats_telemetry_subscription_from_env(ValkyrieDashboardProjection()) is None


def test_valkyrie_dashboard_telemetry_nats_subscription_supports_multiple_streams(
    monkeypatch,
):
    import sleipnir.adapters.nats_transport as nats_transport

    created: list[FakeNatsSubscriber] = []

    class FakeSubscription:
        def __init__(self) -> None:
            self.unsubscribed = False

        async def unsubscribe(self) -> None:
            self.unsubscribed = True

    class FakeNatsSubscriber:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.subscription = FakeSubscription()
            self.event_types: list[str] = []
            created.append(self)

        async def start(self) -> None:
            self.started = True

        async def subscribe(self, event_types, handler):
            del handler
            self.event_types = list(event_types)
            return self.subscription

        async def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(nats_transport, "NatsSubscriber", FakeNatsSubscriber)
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_NATS_URL", "tls://nats:4222")
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_CONSUMER_GROUP", "dashboard")
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_NATS_PASSWORD", "ymir-pass")
    monkeypatch.setenv("VALHALLA_PASS", "valhalla-pass")
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_REPLAY_SECONDS", "3600")
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_STARTUP_DELAY_SECONDS", "0")
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_START_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_NATS_CONNECT_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("RAVN_VALKYRIE_TELEMETRY_NATS_MAX_RECONNECT_ATTEMPTS", "0")
    monkeypatch.setenv(
        "RAVN_VALKYRIE_TELEMETRY_NATS_STREAMS",
        (
            "obs-ymir-events:obs.ymir:valkyrie-ymir:"
            "RAVN_VALKYRIE_TELEMETRY_NATS_PASSWORD,"
            "obs-valhalla-events:obs.valhalla:valkyrie-dashboard-valhalla:VALHALLA_PASS"
        ),
    )

    subscription = build_nats_telemetry_subscription_from_env(ValkyrieDashboardProjection())

    assert subscription is not None
    assert len(created) == 2
    assert created[0].kwargs["stream_name"] == "obs-ymir-events"
    assert created[0].kwargs["subject_prefix"] == "obs.ymir"
    assert created[0].kwargs["consumer_group"] == "dashboard-obs-ymir-events"
    assert created[0].kwargs["user"] == "valkyrie-ymir"
    assert created[0].kwargs["password"] == "ymir-pass"
    assert created[0].kwargs["replay_from_time"] is not None
    assert created[0].kwargs["connect_timeout_s"] == 1.5
    assert created[0].kwargs["max_reconnect_attempts"] == 0
    assert created[1].kwargs["stream_name"] == "obs-valhalla-events"
    assert created[1].kwargs["subject_prefix"] == "obs.valhalla"
    assert created[1].kwargs["consumer_group"] == "dashboard-obs-valhalla-events"
    assert created[1].kwargs["user"] == "valkyrie-dashboard-valhalla"
    assert created[1].kwargs["password"] == "valhalla-pass"

    async def start_and_flush() -> None:
        await subscription.start()
        await asyncio.sleep(0.1)

    asyncio.run(start_and_flush())
    assert all(entry.started for entry in created)
    assert all(entry.event_types == ["*"] for entry in created)

    asyncio.run(subscription.stop())
    assert all(entry.subscription.unsubscribed for entry in created)
    assert all(entry.stopped for entry in created)


def test_valkyrie_dashboard_mutations(client: TestClient):
    autonomy = client.post(
        "/api/v1/ravn/valkyrie/autonomy",
        json={
            "valkyrieId": "valkyrie-valhalla-k8s",
            "mode": "yolo",
            "reason": "test",
        },
    )
    assert autonomy.status_code == 200
    valhalla = next(
        entry
        for entry in autonomy.json()["valkyries"]
        if entry["id"] == "valkyrie-valhalla-k8s"
    )
    assert valhalla["autonomyMode"] == "yolo"


def test_valkyrie_signal_stream_replays_events(client: TestClient):
    with client.stream(
        "GET",
        "/api/v1/ravn/valkyrie/signals?replay_once=true",
    ) as resp:
        assert resp.status_code == 200
        body = resp.read().decode("utf-8")

    assert "text/event-stream" in resp.headers["content-type"]
    assert "signal-k8s-checkout-probe" not in body


def test_list_sessions_returns_seeded_runtime_sessions(client: TestClient):
    resp = client.get("/api/v1/ravn/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data
    assert data[0]["ravn_id"]


def test_stop_session(client: TestClient):
    resp = client.post("/api/v1/ravn/sessions/my-session-id/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "my-session-id"
    assert data["status"] == "stopped"


def test_personas_not_mounted_without_loader(client: TestClient):
    """Persona routes should not exist when no loader is provided."""
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 404


def test_personas_mounted_with_loader(client_with_personas: TestClient):
    """Persona list endpoint exists and returns 200 when a loader is wired."""
    resp = client_with_personas.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_app_no_args_returns_fastapi():
    from fastapi import FastAPI

    assert isinstance(create_app(), FastAPI)


def test_list_wardens_returns_empty_list_when_store_is_empty(tmp_path):
    client = TestClient(create_app(warden_store=_store(tmp_path)))

    resp = client.get("/api/v1/ravn/wardens")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_warden_persists_spec(tmp_path):
    store = _store(tmp_path)
    client = TestClient(create_app(warden_store=store))

    resp = client.post(
        "/api/v1/ravn/wardens",
        json={
            "name": "Research Warden",
            "persona": "mimir-warden",
            "mount_names": ["local", "shared"],
            "write_mount": "local",
            "category_scope": ["infra"],
            "autostart": True,
        },
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["id"] == "research-warden"
    assert payload["mimir"]["mount_names"] == ["local", "shared"]
    assert payload["mimir"]["write_mount"] == "local"
    assert payload["mimir"]["category_scope"] == ["infra"]
    assert payload["autostart"] is True
    assert payload["runtime"]["state"] == "offline"
    assert payload["supervisor"]["installed"] is False

    persisted = store.get("research-warden")
    assert persisted is not None
    assert persisted.name == "Research Warden"
    assert persisted.persona == "mimir-warden"


def test_create_warden_persists_deployment_kwargs(tmp_path):
    store = _store(tmp_path)
    client = TestClient(create_app(warden_store=store))

    resp = client.post(
        "/api/v1/ravn/wardens",
        json={
            "name": "Cluster Warden",
            "deployment": "k8s-gitops",
            "deployment_kwargs": {
                "repo_path": "/tmp/gitops",
                "namespace": "ravn-dev",
                "auto_commit": True,
            },
            "features": {
                "wakefulness_enabled": False,
                "dream_cycle_enabled": True,
                "thread_queue_enabled": False,
            },
        },
    )

    assert resp.status_code == 201
    payload = resp.json()
    assert payload["deployment"] == "k8s-gitops"
    assert payload["deployment_kwargs"]["repo_path"] == "/tmp/gitops"
    assert payload["deployment_kwargs"]["namespace"] == "ravn-dev"
    assert payload["deployment_kwargs"]["auto_commit"] is True
    assert payload["features"]["wakefulness_enabled"] is False
    assert payload["features"]["thread_queue_enabled"] is False


def test_create_warden_persists_console_mount_and_schedule_config(tmp_path):
    store = _store(tmp_path)
    client = TestClient(create_app(warden_store=store))

    resp = client.post(
        "/api/v1/ravn/wardens",
        json={
            "name": "Console Warden",
            "profile": "deep-research",
            "model": "gpt-5.5",
            "mount_names": ["scratch", "permanent"],
            "read_mount_names": ["scratch"],
            "write_mount_names": ["permanent"],
            "schedules": {"dream_cycle_cron_expression": "*/30 * * * *"},
            "console": {"enabled": True, "host": "0.0.0.0", "port": 8610, "auth_mode": "token"},
        },
    )

    assert resp.status_code == 201
    payload = resp.json()
    assert payload["profile"] == "deep-research"
    assert payload["model"] == "gpt-5.5"
    assert payload["mimir"]["read_mount_names"] == ["scratch"]
    assert payload["mimir"]["write_mount_names"] == ["permanent"]
    assert payload["schedules"]["dream_cycle_cron_expression"] == "*/30 * * * *"
    assert payload["console"]["enabled"] is True
    assert payload["console"]["port"] == 8610


def test_get_warden_returns_404_when_missing(tmp_path):
    client = TestClient(create_app(warden_store=_store(tmp_path)))

    resp = client.get("/api/v1/ravn/wardens/missing")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Warden not found"


@pytest.mark.asyncio
async def test_warden_stream_broker_fans_out_updates():
    broker = WardenStreamBroker()
    warden = WardenSpec(id="research-warden", name="Research Warden")
    queue = broker.subscribe(warden.id)
    second_queue = broker.subscribe(warden.id)

    await broker.publish("warden.started", warden)
    event = await asyncio.wait_for(queue.get(), timeout=0.1)
    second_event = await asyncio.wait_for(second_queue.get(), timeout=0.1)

    assert event.event == "warden.started"
    assert event.warden.id == "research-warden"
    assert second_event.event == "warden.started"
    assert second_event.warden.id == "research-warden"


def test_observe_warden_refreshes_backend_status(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/observe")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["supervisor"]["observation"]["status"] == "idle"
    assert payload["supervisor"]["observation"]["source"] == "fake"


def test_observe_warden_returns_502_when_deployer_fails(tmp_path):
    store = _store(tmp_path, fail_on="observe")
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/observe")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "observe failed"


def test_install_warden_generates_service_artifacts(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/install")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["supervisor"]["installed"] is True
    assert payload["supervisor"]["service_file"].endswith(".plist")
    assert payload["runtime"]["state"] == "idle"


def test_start_warden_requires_install_first(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/start")
    assert resp.status_code == 409
    assert "installed before it can be started" in resp.json()["detail"]


def test_start_warden_marks_installed_warden_active(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store.install(created.id, workspace_root=tmp_path)
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/start")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["runtime"]["state"] == "active"
    assert payload["runtime"]["last_started_at"] is not None


def test_install_warden_returns_502_when_deployer_fails(tmp_path):
    store = _store(tmp_path, fail_on="install")
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/install")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "install failed"


def test_start_warden_returns_502_when_deployer_fails(tmp_path):
    store = _store(tmp_path, fail_on="start")
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store = _store(tmp_path, fail_on="")
    installed = store.install(created.id, workspace_root=tmp_path)
    assert installed is not None
    client = TestClient(create_app(warden_store=_store(tmp_path, fail_on="start")))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/start")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "start failed"


def test_stop_warden_requires_install_first(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/stop")
    assert resp.status_code == 409
    assert "installed before it can be stopped" in resp.json()["detail"]


def test_stop_warden_marks_installed_warden_idle(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store.install(created.id, workspace_root=tmp_path)
    store.start(created.id)
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/stop")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["runtime"]["state"] == "idle"
    assert payload["supervisor"]["installed"] is True


def test_uninstall_warden_marks_spec_offline(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store.install(created.id, workspace_root=tmp_path)
    client = TestClient(create_app(warden_store=store))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/uninstall")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["runtime"]["state"] == "offline"
    assert payload["supervisor"]["installed"] is False


def test_stop_warden_returns_502_when_deployer_fails(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store.install(created.id, workspace_root=tmp_path)
    client = TestClient(create_app(warden_store=_store(tmp_path, fail_on="stop")))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/stop")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "stop failed"


def test_uninstall_warden_returns_502_when_deployer_fails(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=_store(tmp_path, fail_on="uninstall")))

    resp = client.post(f"/api/v1/ravn/wardens/{created.id}/uninstall")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "uninstall failed"


def test_get_warden_logs_includes_dream_summaries_from_mimir_log(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dream_log = tmp_path / ".ravn" / "mimir" / "local" / "wiki" / "log.md"
    dream_log.parent.mkdir(parents=True, exist_ok=True)
    state_file = (
        tmp_path / ".ravn" / "wardens" / "research-warden" / "state" / "dream_cycle_state.json"
    )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text('{"last_dream_at":"2026-05-12T21:00:00+00:00"}', encoding="utf-8")
    dream_log.write_text(
        "# Mimir log\n\n"
        "## [2026-05-12] dream | 2026-05-12T20:58:59+00:00\n"
        "pages_updated=4 entities_created=1 lint_fixes=2\n",
        encoding="utf-8",
    )
    store = _store(tmp_path / "wardens")
    created = store.create(
        WardenSpec(
            id="",
            name="Research Warden",
            mimir={"mount_names": ["local"], "write_mount": "local"},
        )
    )
    client = TestClient(create_app(warden_store=store))

    resp = client.get(f"/api/v1/ravn/wardens/{created.id}/logs?stream=stdout")

    assert resp.status_code == 200
    payload = resp.json()
    assert any(entry["logger"] == "mimir.dream" for entry in payload)
    assert any("pages_updated=4" in entry["message"] for entry in payload)


def test_get_warden_logs_supports_all_streams_and_parsed_lines(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    warden_dir = store.warden_dir(created.id)
    warden_dir.mkdir(parents=True, exist_ok=True)
    (warden_dir / "warden.log").write_text(
        "2026-05-12 14:01:20,123 ravn.daemon INFO ravn daemon started.\n",
        encoding="utf-8",
    )
    (warden_dir / "warden.error.log").write_text("plain stderr line\n", encoding="utf-8")
    client = TestClient(create_app(warden_store=store))

    stdout = client.get(f"/api/v1/ravn/wardens/{created.id}/logs?stream=stdout").json()
    stderr = client.get(f"/api/v1/ravn/wardens/{created.id}/logs?stream=stderr").json()
    merged = client.get(f"/api/v1/ravn/wardens/{created.id}/logs?stream=all").json()

    assert stdout[0]["logger"] == "ravn.daemon"
    assert stdout[0]["level"] == "INFO"
    assert stderr[0]["source"] == "stderr"
    assert stderr[0]["message"] == "plain stderr line"
    assert {entry["source"] for entry in merged} == {"stdout", "stderr"}


def test_get_warden_logs_rejects_invalid_stream(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    client = TestClient(create_app(warden_store=store))

    resp = client.get(f"/api/v1/ravn/wardens/{created.id}/logs?stream=nope")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "stream must be 'stdout', 'stderr', or 'all'"


def test_get_warden_logs_returns_404_when_missing(tmp_path):
    client = TestClient(create_app(warden_store=_store(tmp_path)))

    resp = client.get("/api/v1/ravn/wardens/missing/logs")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Warden not found"


def test_get_warden_activity_returns_merged_entries_and_handles_missing(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    warden_dir = store.warden_dir(created.id)
    warden_dir.mkdir(parents=True, exist_ok=True)
    (warden_dir / "warden.log").write_text(
        "2026-05-12 14:01:20,123 ravn.daemon INFO stdout line\n",
        encoding="utf-8",
    )
    (warden_dir / "warden.error.log").write_text(
        "2026-05-12 14:01:21,123 ravn.daemon ERROR stderr line\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(warden_store=store))

    resp = client.get(f"/api/v1/ravn/wardens/{created.id}/activity?limit=10")
    assert resp.status_code == 200
    payload = resp.json()
    assert [entry["message"] for entry in payload] == ["stdout line", "stderr line"]

    missing = client.get("/api/v1/ravn/wardens/missing/activity")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Warden not found"


def test_warden_stream_returns_404_when_missing(tmp_path):
    client = TestClient(create_app(warden_store=_store(tmp_path)))

    resp = client.get("/api/v1/ravn/wardens/missing/stream")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Warden not found"


def test_get_warden_includes_last_dream_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dream_log = tmp_path / ".ravn" / "mimir" / "local" / "wiki" / "log.md"
    dream_log.parent.mkdir(parents=True, exist_ok=True)
    state_file = (
        tmp_path / ".ravn" / "wardens" / "research-warden" / "state" / "dream_cycle_state.json"
    )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text('{"last_dream_at":"2026-05-12T21:00:00+00:00"}', encoding="utf-8")
    dream_log.write_text(
        "# Mimir log\n\n"
        "## [2026-05-12] dream | 2026-05-12T20:58:59+00:00\n"
        "pages_updated=2 entities_created=0 lint_fixes=1\n",
        encoding="utf-8",
    )
    store = _store(tmp_path / "wardens")
    created = store.create(
        WardenSpec(
            id="",
            name="Research Warden",
            mimir={"mount_names": ["local"], "write_mount": "local"},
        )
    )
    client = TestClient(create_app(warden_store=store))

    resp = client.get(f"/api/v1/ravn/wardens/{created.id}")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["runtime"]["pages_touched"] == 2
    assert payload["runtime"]["last_dream"]["pages_updated"] == 2


def test_get_warden_does_not_borrow_shared_dream_without_state_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dream_log = tmp_path / ".ravn" / "mimir" / "local" / "wiki" / "log.md"
    dream_log.parent.mkdir(parents=True, exist_ok=True)
    dream_log.write_text(
        "# Mimir log\n\n"
        "## [2026-05-12] dream | 2026-05-12T20:58:59+00:00\n"
        "pages_updated=2 entities_created=0 lint_fixes=1\n",
        encoding="utf-8",
    )
    store = _store(tmp_path / "wardens")
    created = store.create(
        WardenSpec(
            id="",
            name="Research Warden",
            mimir={"mount_names": ["local"], "write_mount": "local"},
        )
    )
    client = TestClient(create_app(warden_store=store))

    resp = client.get(f"/api/v1/ravn/wardens/{created.id}")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["runtime"]["last_dream"] is None
    assert payload["runtime"]["pages_touched"] == 0


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/ravn/wardens/missing/observe", "post"),
        ("/api/v1/ravn/wardens/missing/install", "post"),
        ("/api/v1/ravn/wardens/missing/start", "post"),
        ("/api/v1/ravn/wardens/missing/stop", "post"),
        ("/api/v1/ravn/wardens/missing/uninstall", "post"),
    ],
)
def test_missing_warden_mutation_endpoints_return_404(tmp_path, path: str, method: str):
    client = TestClient(create_app(warden_store=_store(tmp_path)))

    resp = getattr(client, method)(path)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Warden not found"
