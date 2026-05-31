from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ravn.ports.warden_deployer import WardenDeploymentResult, WardenObservationResult
from ravn.warden import WardenSpec, WardenStore
from ravn.warden.artifacts import service_label, start_command, write_runtime_config
from ravn.warden.models import WardenObservation, WardenSupervisor


class FakeWardenDeployer:
    def install(self, spec: WardenSpec, *, warden_dir: Path, workspace_root: Path | None = None):
        config_path = write_runtime_config(
            spec,
            warden_dir=warden_dir,
            workspace_root=workspace_root,
        )
        service_path = warden_dir / "warden.service"
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
        return WardenDeploymentResult(
            supervisor=spec.supervisor,
            runtime_state="active",
        )

    def stop(self, spec: WardenSpec, *, warden_dir: Path):
        return WardenDeploymentResult(
            supervisor=spec.supervisor,
            runtime_state="idle",
        )

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path):
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(),
            runtime_state="offline",
        )

    def observe(self, spec: WardenSpec, *, warden_dir: Path):
        del warden_dir
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


def _store(tmp_path: Path) -> WardenStore:
    return WardenStore(root=tmp_path, deployer_factory=lambda spec: FakeWardenDeployer())


def test_create_persists_and_loads_spec(tmp_path):
    store = _store(tmp_path)

    created = store.create(
        WardenSpec(
            id="",
            name="Research Warden",
            persona="mimir-warden",
            mimir={"mount_names": ["local", "shared"], "write_mount": "local"},
            created_by="test",
        )
    )

    assert created.id == "research-warden"
    assert store.spec_path(created.id).exists()

    loaded = store.get(created.id)
    assert loaded is not None
    assert loaded.name == "Research Warden"
    assert loaded.mimir.mount_names == ["local", "shared"]
    assert loaded.mimir.write_mount == "local"


def test_allocate_id_deduplicates_existing_names(tmp_path):
    store = _store(tmp_path)

    first = store.create(WardenSpec(id="", name="Research Warden"))
    second = store.create(WardenSpec(id="", name="Research Warden"))

    assert first.id == "research-warden"
    assert second.id == "research-warden-2"


def test_list_returns_sorted_specs(tmp_path):
    store = _store(tmp_path)
    store.create(WardenSpec(id="", name="Zulu"))
    store.create(WardenSpec(id="", name="Alpha"))

    specs = store.list()
    assert [spec.name for spec in specs] == ["Alpha", "Zulu"]


def test_install_generates_config_and_service_artifacts(tmp_path):
    store = _store(tmp_path)
    created = store.create(
        WardenSpec(
            id="",
            name="Research Warden",
            persona="mimir-warden",
            profile="infra-synthesis",
            mimir={
                "mount_names": ["local", "shared"],
                "write_mount": "local",
                "category_scope": ["infra"],
            },
        )
    )

    installed = store.install(created.id, workspace_root=tmp_path)

    assert installed is not None
    assert installed.supervisor.installed is True
    assert store.runtime_config_path(created.id).exists()
    assert Path(installed.supervisor.service_file).exists()
    assert installed.runtime.state == "idle"
    config_text = store.runtime_config_path(created.id).read_text(encoding="utf-8")
    assert "initiative:" in config_text
    assert "mimir-warden" in config_text


def test_start_marks_installed_warden_active(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store.install(created.id, workspace_root=tmp_path)

    started = store.start(created.id)

    assert started is not None
    assert started.runtime.state == "active"
    assert started.runtime.last_started_at is not None


def test_stop_marks_installed_warden_idle(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store.install(created.id, workspace_root=tmp_path)
    store.start(created.id)

    stopped = store.stop(created.id)

    assert stopped is not None
    assert stopped.runtime.state == "idle"
    assert stopped.supervisor.installed is True


def test_uninstall_marks_warden_offline_and_clears_supervisor(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))
    store.install(created.id, workspace_root=tmp_path)

    uninstalled = store.uninstall(created.id)

    assert uninstalled is not None
    assert uninstalled.runtime.state == "offline"
    assert uninstalled.supervisor.installed is False
    assert uninstalled.supervisor.service_file == ""


def test_observe_updates_supervisor_observation(tmp_path):
    store = _store(tmp_path)
    created = store.create(WardenSpec(id="", name="Research Warden"))

    observed = store.observe(created.id)

    assert observed is not None
    assert observed.runtime.state == "idle"
    assert observed.supervisor.observation.status == "idle"
    assert observed.supervisor.observation.source == "fake"


def test_get_enriches_last_dream_from_attached_mimir_log(tmp_path, monkeypatch):
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
        "pages_updated=3 entities_created=2 lint_fixes=1 duration_ms=4000\n",
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

    loaded = store.get(created.id)

    assert loaded is not None
    assert loaded.runtime.last_dream is not None
    assert loaded.runtime.last_dream.mounts == ["local"]
    assert loaded.runtime.last_dream.pages_updated == 3
    assert loaded.runtime.last_dream.entities_created == 2
    assert loaded.runtime.last_dream.lint_fixes == 1
    assert loaded.runtime.pages_touched == 3


def test_get_does_not_assign_shared_dream_without_warden_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dream_log = tmp_path / ".ravn" / "mimir" / "local" / "wiki" / "log.md"
    dream_log.parent.mkdir(parents=True, exist_ok=True)
    dream_log.write_text(
        "# Mimir log\n\n"
        "## [2026-05-12] dream | 2026-05-12T20:58:59+00:00\n"
        "pages_updated=3 entities_created=2 lint_fixes=1 duration_ms=4000\n",
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

    loaded = store.get(created.id)

    assert loaded is not None
    assert loaded.runtime.last_dream is None
    assert loaded.runtime.pages_touched == 0
