from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from ravn.adapters.deployment import (
    KubernetesApplyWardenDeploymentAdapter,
    KubernetesGitOpsWardenDeploymentAdapter,
    LaunchdWardenDeploymentAdapter,
    SystemdUserWardenDeploymentAdapter,
)
from ravn.ports.warden_deployer import WardenDeploymentError
from ravn.warden import WardenSpec


def _completed(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_launchd_install_writes_files_and_registers_service(tmp_path: Path) -> None:
    adapter = LaunchdWardenDeploymentAdapter(
        launch_agents_dir=str(tmp_path / "LaunchAgents"),
        launchctl_bin="launchctl",
        user_id=501,
    )
    spec = WardenSpec(id="research-warden", name="Research Warden", autostart=True)

    with patch(
        "subprocess.run",
        side_effect=[_completed(), _completed(), _completed(), _completed()],
    ) as run:
        result = adapter.install(spec, warden_dir=tmp_path / spec.id, workspace_root=tmp_path)

    assert result.runtime_state == "active"
    assert Path(result.supervisor.config_file).exists()
    assert Path(result.supervisor.service_file).exists()
    assert "bootstrap" in " ".join(run.call_args_list[1].args[0])
    assert "kickstart" in " ".join(run.call_args_list[3].args[0])


def test_launchd_start_raises_on_launchctl_failure(tmp_path: Path) -> None:
    adapter = LaunchdWardenDeploymentAdapter(
        launch_agents_dir=str(tmp_path / "LaunchAgents"),
        launchctl_bin="launchctl",
        user_id=501,
    )
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={
            "installed": True,
            "service_file": str(
                tmp_path / "LaunchAgents" / "dev.niuu.ravn.warden.research-warden.plist"
            ),
            "config_file": str(tmp_path / "research-warden" / "config.yaml"),
        },
    )

    with patch("subprocess.run", return_value=_completed(stderr="boom", returncode=1)):
        try:
            adapter.start(spec, warden_dir=tmp_path / spec.id)
        except WardenDeploymentError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected WardenDeploymentError")


def test_launchd_stop_and_uninstall_remove_local_artifacts(tmp_path: Path) -> None:
    adapter = LaunchdWardenDeploymentAdapter(
        launch_agents_dir=str(tmp_path / "LaunchAgents"),
        launchctl_bin="launchctl",
        user_id=501,
    )
    service_path = tmp_path / "LaunchAgents" / "dev.niuu.ravn.warden.research-warden.plist"
    config_path = tmp_path / "research-warden" / "config.yaml"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("service", encoding="utf-8")
    config_path.write_text("config", encoding="utf-8")
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={
            "installed": True,
            "service_file": str(service_path),
            "config_file": str(config_path),
        },
    )

    with patch("subprocess.run", side_effect=[_completed(), _completed(), _completed()]) as run:
        stopped = adapter.stop(spec, warden_dir=tmp_path / spec.id)
        uninstalled = adapter.uninstall(spec, warden_dir=tmp_path / spec.id)

    assert stopped.runtime_state == "idle"
    assert stopped.supervisor.installed is True
    assert uninstalled.runtime_state == "offline"
    assert uninstalled.supervisor.installed is False
    assert not service_path.exists()
    assert not config_path.exists()
    commands = [" ".join(call.args[0]) for call in run.call_args_list]
    assert any("bootout gui/501/dev.niuu.ravn.warden.research-warden" in cmd for cmd in commands)
    assert any("disable gui/501/dev.niuu.ravn.warden.research-warden" in cmd for cmd in commands)


def test_launchd_observe_reports_running_agent(tmp_path: Path) -> None:
    adapter = LaunchdWardenDeploymentAdapter(
        launch_agents_dir=str(tmp_path / "LaunchAgents"),
        launchctl_bin="launchctl",
        user_id=501,
    )
    service_path = tmp_path / "LaunchAgents" / "dev.niuu.ravn.warden.research-warden.plist"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("service", encoding="utf-8")
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={"installed": True, "service_file": str(service_path)},
    )

    with patch(
        "subprocess.run",
        return_value=_completed(stdout="state = running\npid = 1234\n"),
    ):
        observed = adapter.observe(spec, warden_dir=tmp_path / spec.id)

    assert observed.supervisor.observation.status == "running"
    assert observed.supervisor.observation.source == "launchctl"


def test_systemd_install_writes_files_and_enables_unit(tmp_path: Path) -> None:
    adapter = SystemdUserWardenDeploymentAdapter(
        unit_dir=str(tmp_path / "systemd"),
        systemctl_bin="systemctl",
    )
    spec = WardenSpec(id="research-warden", name="Research Warden")

    with patch("subprocess.run", side_effect=[_completed(), _completed()]) as run:
        result = adapter.install(spec, warden_dir=tmp_path / spec.id, workspace_root=tmp_path)

    assert result.runtime_state == "idle"
    assert Path(result.supervisor.config_file).exists()
    assert Path(result.supervisor.service_file).exists()
    joined = " ".join(" ".join(call.args[0]) for call in run.call_args_list)
    assert "--user daemon-reload" in joined
    assert "enable dev.niuu.ravn.warden.research-warden.service" in joined


def test_systemd_start_runs_user_unit(tmp_path: Path) -> None:
    adapter = SystemdUserWardenDeploymentAdapter(
        unit_dir=str(tmp_path / "systemd"),
        systemctl_bin="systemctl",
    )
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={
            "installed": True,
            "service_file": str(
                tmp_path / "systemd" / "dev.niuu.ravn.warden.research-warden.service"
            ),
            "config_file": str(tmp_path / "research-warden" / "config.yaml"),
        },
    )

    with patch("subprocess.run", return_value=_completed()) as run:
        result = adapter.start(spec, warden_dir=tmp_path / spec.id)

    assert result.runtime_state == "active"
    assert "start dev.niuu.ravn.warden.research-warden.service" in " ".join(run.call_args.args[0])


def test_systemd_stop_and_uninstall_remove_unit_files(tmp_path: Path) -> None:
    adapter = SystemdUserWardenDeploymentAdapter(
        unit_dir=str(tmp_path / "systemd"),
        systemctl_bin="systemctl",
    )
    service_path = tmp_path / "systemd" / "dev.niuu.ravn.warden.research-warden.service"
    config_path = tmp_path / "research-warden" / "config.yaml"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("service", encoding="utf-8")
    config_path.write_text("config", encoding="utf-8")
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={
            "installed": True,
            "service_file": str(service_path),
            "config_file": str(config_path),
        },
    )

    with patch(
        "subprocess.run",
        side_effect=[_completed(), _completed(), _completed(), _completed()],
    ) as run:
        stopped = adapter.stop(spec, warden_dir=tmp_path / spec.id)
        uninstalled = adapter.uninstall(spec, warden_dir=tmp_path / spec.id)

    assert stopped.runtime_state == "idle"
    assert stopped.supervisor.installed is True
    assert uninstalled.runtime_state == "offline"
    assert uninstalled.supervisor.installed is False
    assert not service_path.exists()
    assert not config_path.exists()
    commands = [" ".join(call.args[0]) for call in run.call_args_list]
    assert any("stop dev.niuu.ravn.warden.research-warden.service" in cmd for cmd in commands)
    assert any("disable dev.niuu.ravn.warden.research-warden.service" in cmd for cmd in commands)
    assert any("--user daemon-reload" in cmd for cmd in commands)


def test_systemd_observe_reports_active_unit(tmp_path: Path) -> None:
    adapter = SystemdUserWardenDeploymentAdapter(
        unit_dir=str(tmp_path / "systemd"),
        systemctl_bin="systemctl",
    )
    service_path = tmp_path / "systemd" / "dev.niuu.ravn.warden.research-warden.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("service", encoding="utf-8")
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={"installed": True, "service_file": str(service_path)},
    )

    with patch(
        "subprocess.run",
        return_value=_completed(
            stdout="LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n"
        ),
    ):
        observed = adapter.observe(spec, warden_dir=tmp_path / spec.id)

    assert observed.supervisor.observation.status == "running"
    assert observed.supervisor.observation.source == "systemctl --user"


class FakeKubernetesApplyAdapter(KubernetesApplyWardenDeploymentAdapter):
    def __init__(self) -> None:
        super().__init__(
            namespace="ravn-test",
            image="ghcr.io/example/ravn:test",
            create_namespace=True,
        )
        self.applied: list[tuple[str, int, str]] = []
        self.scaled: list[int] = []
        self.deleted: list[str] = []
        self.observed = {
            "deployment_present": True,
            "config_map_present": True,
            "desired_replicas": 1,
            "ready_replicas": 1,
            "available_replicas": 1,
        }

    async def _apply_install(self, spec: WardenSpec, *, config_text: str, replicas: int) -> None:
        self.applied.append((spec.id, replicas, config_text))

    async def _scale_deployment(self, spec: WardenSpec, *, replicas: int) -> None:
        self.scaled.append(replicas)

    async def _delete_install(self, spec: WardenSpec) -> None:
        self.deleted.append(spec.id)

    async def _observe_install(self, spec: WardenSpec) -> dict[str, int | bool]:
        del spec
        return self.observed


def test_k8s_apply_install_writes_bundle_and_tracks_idle_state(tmp_path: Path) -> None:
    adapter = FakeKubernetesApplyAdapter()
    spec = WardenSpec(id="research-warden", name="Research Warden")

    result = adapter.install(spec, warden_dir=tmp_path / spec.id, workspace_root=tmp_path)

    assert result.runtime_state == "idle"
    assert Path(result.supervisor.config_file).exists()
    assert Path(result.supervisor.service_file).exists()
    bundle_text = Path(result.supervisor.service_file).read_text(encoding="utf-8")
    assert "kind: ConfigMap" in bundle_text
    assert "kind: Deployment" in bundle_text
    assert adapter.applied == [
        (
            "research-warden",
            0,
            Path(result.supervisor.config_file).read_text(encoding="utf-8"),
        )
    ]


def test_k8s_apply_start_scales_existing_deployment(tmp_path: Path) -> None:
    adapter = FakeKubernetesApplyAdapter()
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={
            "installed": True,
            "service_file": str(tmp_path / "research-warden" / "k8s-bundle.yaml"),
            "config_file": str(tmp_path / "research-warden" / "config.yaml"),
        },
    )

    result = adapter.start(spec, warden_dir=tmp_path / spec.id)

    assert result.runtime_state == "active"
    assert adapter.scaled == [1]


def test_k8s_apply_stop_and_uninstall_cleanup_bundle(tmp_path: Path) -> None:
    adapter = FakeKubernetesApplyAdapter()
    warden_dir = tmp_path / "research-warden"
    bundle_path = warden_dir / "k8s-bundle.yaml"
    config_path = warden_dir / "config.yaml"
    warden_dir.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text("bundle", encoding="utf-8")
    config_path.write_text("config", encoding="utf-8")
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={
            "installed": True,
            "service_file": str(bundle_path),
            "config_file": str(config_path),
        },
    )

    stopped = adapter.stop(spec, warden_dir=warden_dir)
    uninstalled = adapter.uninstall(spec, warden_dir=warden_dir)

    assert stopped.runtime_state == "idle"
    assert stopped.supervisor.installed is True
    assert adapter.scaled == [0]
    assert uninstalled.runtime_state == "offline"
    assert uninstalled.supervisor.installed is False
    assert adapter.deleted == ["research-warden"]
    assert not bundle_path.exists()
    assert not config_path.exists()


def test_k8s_apply_observe_reports_degraded_when_unavailable(tmp_path: Path) -> None:
    adapter = FakeKubernetesApplyAdapter()
    adapter.observed = {
        "deployment_present": True,
        "config_map_present": True,
        "desired_replicas": 1,
        "ready_replicas": 0,
        "available_replicas": 0,
    }
    spec = WardenSpec(id="research-warden", name="Research Warden")

    observed = adapter.observe(spec, warden_dir=tmp_path / spec.id)

    assert observed.supervisor.observation.status == "degraded"
    assert observed.supervisor.observation.source == "kubernetes"


def test_k8s_gitops_install_writes_manifest_and_commits(tmp_path: Path) -> None:
    from git import Repo

    repo_path = tmp_path / "gitops"
    repo = Repo.init(repo_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Codex")
        config.set_value("user", "email", "codex@example.com")
    (repo_path / "README.md").write_text("gitops\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("init")

    adapter = KubernetesGitOpsWardenDeploymentAdapter(
        repo_path=str(repo_path),
        manifests_subdir="clusters/dev/wardens",
        auto_commit=True,
    )
    spec = WardenSpec(id="research-warden", name="Research Warden", autostart=True)

    result = adapter.install(spec, warden_dir=tmp_path / spec.id, workspace_root=tmp_path)

    assert result.runtime_state == "active"
    manifest_path = Path(result.supervisor.service_file)
    assert manifest_path.exists()
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "replicas: 1" in manifest_text
    assert repo.head.commit.message.startswith("Deploy warden research-warden")


def test_k8s_gitops_start_requires_repo_path(tmp_path: Path) -> None:
    adapter = KubernetesGitOpsWardenDeploymentAdapter()
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={"installed": True},
    )

    try:
        adapter.start(spec, warden_dir=tmp_path / spec.id)
    except WardenDeploymentError as exc:
        assert str(exc) == "repo_path is required for k8s-gitops deployment"
    else:
        raise AssertionError("expected WardenDeploymentError")


def test_k8s_gitops_stop_and_uninstall_rewrite_repo_state(tmp_path: Path) -> None:
    from git import Repo

    repo_path = tmp_path / "gitops"
    repo = Repo.init(repo_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Codex")
        config.set_value("user", "email", "codex@example.com")
    (repo_path / "README.md").write_text("gitops\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("init")

    adapter = KubernetesGitOpsWardenDeploymentAdapter(
        repo_path=str(repo_path),
        manifests_subdir="clusters/dev/wardens",
        auto_commit=True,
    )
    warden_dir = tmp_path / "research-warden"
    config_path = warden_dir / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("config", encoding="utf-8")
    spec = WardenSpec(
        id="research-warden",
        name="Research Warden",
        supervisor={
            "installed": True,
            "config_file": str(config_path),
            "service_file": str(repo_path / "clusters/dev/wardens/research-warden.yaml"),
        },
    )

    started = adapter.start(spec, warden_dir=warden_dir)
    manifest_path = Path(started.supervisor.service_file)
    stopped = adapter.stop(spec, warden_dir=warden_dir)
    stopped_manifest = manifest_path.read_text(encoding="utf-8")
    uninstalled = adapter.uninstall(spec, warden_dir=warden_dir)

    assert started.runtime_state == "active"
    assert stopped.runtime_state == "idle"
    assert "replicas: 0" in stopped_manifest
    assert uninstalled.runtime_state == "offline"
    assert uninstalled.supervisor.installed is False
    assert not manifest_path.exists()
    assert not config_path.exists()
    assert repo.head.commit.message.startswith("Deploy warden research-warden (removed)")


def test_k8s_gitops_observe_reads_desired_scale_from_manifest(tmp_path: Path) -> None:
    from git import Repo

    repo_path = tmp_path / "gitops"
    Repo.init(repo_path)
    (repo_path / "README.md").write_text("gitops\n", encoding="utf-8")
    adapter = KubernetesGitOpsWardenDeploymentAdapter(
        repo_path=str(repo_path),
        manifests_subdir="clusters/dev/wardens",
    )
    spec = WardenSpec(id="research-warden", name="Research Warden")
    manifest_path = repo_path / "clusters/dev/wardens/research-warden.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "apiVersion: apps/v1\nkind: Deployment\nspec:\n  replicas: 1\n",
        encoding="utf-8",
    )

    observed = adapter.observe(spec, warden_dir=tmp_path / spec.id)

    assert observed.supervisor.observation.status == "running"
    assert observed.supervisor.observation.source == "gitops"
