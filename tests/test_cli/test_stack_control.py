"""Stack controller tests; Docker and the bundle writer are faked boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from docker.errors import ImageNotFound, NotFound

from cli.config import CLISettings
from cli.services import stack_control as sc
from cli.services.compose_bundle import (
    STACK_OVERRIDES_FILE,
    STACK_STAGED_FILE,
    apply_stack_overrides,
    write_stack_file,
)
from cli.services.docker_host import GpuFacts, HostFacts
from cli.services.stack_control import DockerStackController
from niuu.domain.stack import validate_stack_changes


class _Container:
    def __init__(self, name: str, status: str = "running", exit_code: int = 0) -> None:
        self.name = name
        self.status = status
        self.attrs: dict[str, Any] = {"State": {"ExitCode": exit_code, "Health": {}}}
        self.removed = False
        self.log_text = b"compose output"

    def reload(self) -> None:
        return None

    def logs(self, **kwargs: Any) -> bytes:
        del kwargs
        return self.log_text

    def remove(self, **kwargs: Any) -> None:
        del kwargs
        self.removed = True


class _Containers:
    def __init__(self) -> None:
        self.by_name: dict[str, _Container] = {}
        self.run_kwargs: list[dict[str, Any]] = []

    def run(self, image: str, **kwargs: Any) -> _Container:
        self.run_kwargs.append({"image": image, **kwargs})
        container = _Container(kwargs["name"])
        self.by_name[kwargs["name"]] = container
        return container

    def get(self, name: str) -> _Container:
        if name not in self.by_name:
            raise NotFound(name)
        return self.by_name[name]


class _Images:
    def __init__(self) -> None:
        self.present: set[str] = set()
        self.pulled: list[str] = []

    def get(self, image: str) -> None:
        if image not in self.present:
            raise ImageNotFound(image)

    def pull(self, image: str) -> None:
        self.pulled.append(image)
        self.present.add(image)


class _Client:
    def __init__(self) -> None:
        self.containers = _Containers()
        self.images = _Images()


@pytest.fixture
def client() -> _Client:
    fake = _Client()
    with patch.object(sc.docker, "from_env", return_value=fake):
        yield fake


@pytest.fixture
def stack_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    settings = CLISettings(
        mode="docker",
        server={"port": 18080},
        docker={
            "data_dir": str(data),
            "compose_dir": str(tmp_path / "bundle"),
            "bind_host": "127.0.0.1",
            "project_name": "niuu-test",
        },
    )
    write_stack_file(settings, data)
    facts = HostFacts(
        hostname="spark",
        os_name="Ubuntu",
        os_version="24.04",
        arch="aarch64",
        cpu_count=20,
        memory_total_bytes=128 * 1024**3,
        docker_version="27",
        compose_version="2",
        nvidia_runtime=True,
        gpus=[GpuFacts(name="NVIDIA GB10", memory_total_mib=131072, driver_version="570")],
        data_dir=str(data),
        disk_free_bytes=1,
        disk_total_bytes=2,
        bind_host="127.0.0.1",
        external_host="192.168.1.5",
        port=18080,
        skuld_image="skuld:test",
    )
    (data / "host-facts.json").write_text(facts.to_json())
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / ".env").write_text("NIUU_EXTERNAL_HOST=192.168.1.5\n")
    return data


@pytest.fixture
def controller(client: _Client, stack_dir: Path) -> DockerStackController:
    del client
    return DockerStackController(stack_dir=str(stack_dir))


def test_validate_stack_changes_whitelists_keys() -> None:
    assert validate_stack_changes({}) == {}
    assert validate_stack_changes({"bind_host": "0.0.0.0"}) == {"docker": {"bind_host": "0.0.0.0"}}
    assert validate_stack_changes({"vllm_enabled": True, "vllm_model": " m "}) == {
        "docker": {"vllm": {"enabled": True, "model": "m"}}
    }
    assert validate_stack_changes(
        {"vllm_max_model_len": 4096, "vllm_gpu_memory_utilization": 0.5}
    ) == {"docker": {"vllm": {"max_model_len": 4096, "gpu_memory_utilization": 0.5}}}
    for bad in (
        {"bind_host": "10.0.0.1"},
        {"vllm_enabled": "yes"},
        {"vllm_model": 3},
        {"vllm_max_model_len": 0},
        {"vllm_max_model_len": True},
        {"vllm_gpu_memory_utilization": 2},
        {"vllm_enabled": True, "vllm_model": ""},
        {"image": "x"},
    ):
        with pytest.raises(ValueError):
            validate_stack_changes(bad)


@pytest.mark.asyncio
async def test_view_reports_current_staged_effective_and_models(
    controller: DockerStackController, stack_dir: Path
) -> None:
    view = await controller.view()
    assert view.current.bind_host == "127.0.0.1"
    assert view.current.access_urls == ["http://127.0.0.1:18080"]
    assert view.staged == {}
    assert view.has_staged_changes is False
    assert view.accelerator_memory_gib == 128
    names = [m.id for m in view.models]
    assert "nemotron-3-nano-30b" in names
    assert all(m.fits is True for m in view.models)

    view = await controller.stage(
        {"bind_host": "0.0.0.0", "vllm_enabled": True, "vllm_model": "org/m"}
    )
    assert view.staged == {
        "docker": {"bind_host": "0.0.0.0", "vllm": {"enabled": True, "model": "org/m"}}
    }
    assert view.current.bind_host == "127.0.0.1"
    assert view.effective.bind_host == "0.0.0.0"
    assert view.effective.access_urls == ["http://127.0.0.1:18080", "http://192.168.1.5:18080"]
    assert view.effective.vllm.model == "org/m"
    assert yaml.safe_load((stack_dir / STACK_STAGED_FILE).read_text()) == view.staged

    with pytest.raises(ValueError, match="bind_host must be one of"):
        await controller.stage({"bind_host": "nope"})
    view = await controller.discard()
    assert view.staged == {}
    assert not (stack_dir / STACK_STAGED_FILE).exists()


@pytest.mark.asyncio
async def test_view_without_gpu_cannot_judge_fit(
    controller: DockerStackController, stack_dir: Path
) -> None:
    facts = json.loads((stack_dir / "host-facts.json").read_text())
    facts["gpus"] = []
    (stack_dir / "host-facts.json").write_text(json.dumps(facts))
    view = await controller.view()
    assert view.accelerator_memory_gib == 0
    assert all(m.fits is None for m in view.models)


@pytest.mark.asyncio
async def test_missing_stack_file_is_explicit(client: _Client, tmp_path: Path) -> None:
    del client
    controller = DockerStackController(stack_dir=str(tmp_path))
    with pytest.raises(FileNotFoundError, match="niuu up"):
        await controller.view()
    (tmp_path / "stack.yaml").write_text("docker: {}\n")
    with pytest.raises(FileNotFoundError, match="host-facts"):
        await controller.view()


@pytest.mark.asyncio
async def test_apply_renders_bundle_runs_applier_and_reports(
    controller: DockerStackController, client: _Client, stack_dir: Path
) -> None:
    with pytest.raises(ValueError, match="Nothing is staged"):
        await controller.apply()
    await controller.stage({"bind_host": "0.0.0.0"})
    with patch.object(sc, "write_bundle") as write_bundle:
        status = await controller.apply()
    assert status.state == "applying"
    assert status.changes == {"docker": {"bind_host": "0.0.0.0"}}
    settings = write_bundle.call_args.args[0]
    assert settings.docker.bind_host == "0.0.0.0"
    facts = write_bundle.call_args.kwargs["host_facts"]
    assert facts.bind_host == "0.0.0.0"
    assert write_bundle.call_args.kwargs["external_host"] == "192.168.1.5"
    # staged became applied overrides, and `niuu up` will merge them
    assert not (stack_dir / STACK_STAGED_FILE).exists()
    assert yaml.safe_load((stack_dir / STACK_OVERRIDES_FILE).read_text()) == {
        "docker": {"bind_host": "0.0.0.0"}
    }
    host_settings = CLISettings(mode="docker", docker={"data_dir": str(stack_dir)})
    assert apply_stack_overrides(host_settings).docker.bind_host == "0.0.0.0"

    run = client.containers.run_kwargs[0]
    assert run["image"] == "docker:28-cli"
    assert client.images.pulled == ["docker:28-cli"]
    assert run["command"][:3] == ["compose", "--project-name", "niuu-test"]
    assert run["command"][-3:] == ["up", "--detach", "--remove-orphans"]
    assert run["volumes"]["/var/run/docker.sock"]["bind"] == "/var/run/docker.sock"
    assert run["labels"]["niuu.managed-by"] == "docker_container"
    assert run["environment"] == {}

    # while the applier runs
    assert (await controller.status()).state == "applying"
    with pytest.raises(ValueError, match="already running"):
        await controller.stage({"bind_host": "127.0.0.1"})
        await controller.apply()
    # the current view now includes the applied override
    assert (await controller.view()).current.bind_host == "0.0.0.0"

    # finished successfully: reported once, then cleaned up
    applier = client.containers.by_name[run["name"]]
    applier.status = "exited"
    done = await controller.status()
    assert done.state == "applied"
    assert applier.removed is True
    assert (await controller.status()).state == "idle"


@pytest.mark.asyncio
async def test_apply_failure_keeps_logs(
    controller: DockerStackController, client: _Client, stack_dir: Path
) -> None:
    await controller.stage({"bind_host": "0.0.0.0"})
    with patch.object(sc, "write_bundle"):
        await controller.apply()
    name = client.containers.run_kwargs[0]["name"]
    applier = client.containers.by_name[name]
    applier.status = "exited"
    applier.attrs["State"]["ExitCode"] = 17
    applier.log_text = b"error: port already allocated"
    failed = await controller.status()
    assert failed.state == "failed"
    assert "port already allocated" in failed.detail
    assert (await controller.status()).state == "idle"

    await controller.stage({"bind_host": "127.0.0.1"})
    with patch.object(sc, "write_bundle"):
        await controller.apply()
    del client.containers.by_name[client.containers.run_kwargs[1]["name"]]
    gone = await controller.status()
    assert gone.state == "failed"
    assert "disappeared" in gone.detail


@pytest.mark.asyncio
async def test_vllm_status_follows_the_container(
    controller: DockerStackController, client: _Client
) -> None:
    assert (await controller.status()).vllm is None
    await controller.stage({"vllm_enabled": True, "vllm_model": "org/m"})
    absent = (await controller.status()).vllm
    assert absent is not None and absent.state == "absent"

    vllm = _Container("niuu-test-vllm-1")
    vllm.log_text = b"Downloading shards: 40%\n"
    client.containers.by_name[vllm.name] = vllm
    starting = (await controller.status()).vllm
    assert starting is not None
    assert starting.state == "starting"
    assert "40%" in starting.detail

    vllm.attrs["State"]["Health"] = {"Status": "healthy"}
    ready = (await controller.status()).vllm
    assert ready is not None and ready.state == "ready"
    assert "org/m" in ready.detail

    vllm.status = "exited"
    failed = (await controller.status()).vllm
    assert failed is not None and failed.state == "failed"


def test_stack_view_dict_is_json_ready(controller: DockerStackController) -> None:
    import asyncio

    view = asyncio.run(controller.view())
    data = sc.stack_view_dict(view)
    assert json.dumps(data)
    assert data["current"]["bind_host"] == "127.0.0.1"
