"""Tests for the Ravn FastAPI sub-application."""

from __future__ import annotations

import asyncio
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


@pytest.fixture
def client() -> TestClient:
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
    assert data["liveReport"]["routeSubject"] == "flock.k8s.>"
    assert data["telemetry"]["verified"] is False
    assert "demo projection" in data["telemetry"]["gaps"][1]
    assert any(learning["scope"] == "flock" for learning in data["learnings"])


def test_valkyrie_dashboard_aggregates_verified_telemetry_events():
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.runtime.started",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
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

    telemetry = projection.dashboard()["telemetry"]

    assert telemetry["verified"] is True
    assert telemetry["source"] == "sleipnir_events"
    assert telemetry["totals"]["eventsObserved"] == 4
    assert telemetry["totals"]["pollsCompleted"] == 1
    assert telemetry["totals"]["signalsCollected"] == 8
    assert telemetry["totals"]["signalsPublished"] == 5
    assert telemetry["totals"]["duplicateSignals"] == 3
    assert telemetry["totals"]["tasksEnqueued"] == 2
    assert telemetry["totals"]["learningEvents"] == 1
    assert telemetry["totals"]["dreamCyclesCompleted"] == 1
    assert telemetry["totals"]["judgments"] == 1
    assert telemetry["byEnvironment"][0]["environmentId"] == "ymir"
    assert telemetry["byEnvironment"][0]["tasksEnqueued"] == 2
    assert telemetry["byEnvironment"][0]["judgments"] == 1
    assert telemetry["recentOutcomes"][0]["taskId"] == "task-k8s-1"
    assert (
        telemetry["recentOutcomes"][0]["summary"]
        == "Persistent ImagePullBackOff requires inspection."
    )
    assert telemetry["recentPolls"][0]["sourceId"] == "kubernetes-events"
    assert telemetry["runtime"][0]["driveLoopEnabled"] is True
    assert telemetry["llm"]["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"


def test_valkyrie_dashboard_keeps_runtime_telemetry_when_raw_signals_are_noisy():
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.runtime.started",
            source="ravn:valkyrie:ymir",
            payload={
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
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
    assert telemetry["llm"]["status"] == "configured"
    assert telemetry["llm"]["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert "No valkyrie.runtime.started events observed." not in telemetry["gaps"]


def test_valkyrie_dashboard_telemetry_nats_subscription_is_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("NATS_URL", "nats://should-not-be-used:4222")
    monkeypatch.delenv("RAVN_VALKYRIE_TELEMETRY_NATS_URL", raising=False)

    assert build_nats_telemetry_subscription_from_env(ValkyrieDashboardProjection()) is None


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

    joined = client.post("/api/v1/ravn/valkyrie/huddles/huddle-valhalla-now/join", json={})
    assert joined.status_code == 200
    assert joined.json()["joined"] is True

    message = client.post(
        "/api/v1/ravn/valkyrie/huddles/huddle-valhalla-now/messages",
        json={"huddleId": "huddle-valhalla-now", "body": "joining from the test flock"},
    )
    assert message.status_code == 200
    assert message.json()["authorId"] == "operator"

    learning = client.post(
        "/api/v1/ravn/valkyrie/learnings/learning-k8s-rollout-noise/adopt",
        json={"learningId": "learning-k8s-rollout-noise", "reason": "test"},
    )
    assert learning.status_code == 200
    assert learning.json()["status"] == "adopted"


def test_valkyrie_signal_stream_replays_events(client: TestClient):
    with client.stream(
        "GET",
        "/api/v1/ravn/valkyrie/signals?replay_once=true",
    ) as resp:
        assert resp.status_code == 200
        body = resp.read().decode("utf-8")

    assert "text/event-stream" in resp.headers["content-type"]
    assert "data: " in body
    assert "signal-k8s-checkout-probe" in body


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
