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
from cli.services.stack_control import (
    DockerStackController,
    hf_cache_dir,
    parse_pull_progress,
    vllm_progress,
)
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
            "vllm": {"image": "nvcr.io/nvidia/vllm:test"},
            "models": [
                {
                    "id": "nemotron-3-nano-30b",
                    "model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16",
                    "name": "NVIDIA Nemotron 3 Nano 30B",
                    "weight_gib": 62,
                    "recommended": True,
                    "trust_remote_code": True,
                },
                {
                    "id": "qwen3-coder-30b",
                    "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                    "name": "Qwen",
                    "weight_gib": 24,
                },
            ],
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
    (tmp_path / "bundle" / "secrets.env").write_text(
        "NIUU_POSTGRES_PASSWORD=p\nNIUU_CREDENTIAL_KEY=k\n"
    )
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
    assert validate_stack_changes({"vllm_trust_remote_code": True}) == {
        "docker": {"vllm": {"trust_remote_code": True}}
    }
    with pytest.raises(ValueError, match="vllm_trust_remote_code"):
        validate_stack_changes({"vllm_trust_remote_code": "yes"})
    # the session cap lands on the platform's pod manager, next to the docker keys
    assert validate_stack_changes({"max_sessions": 8, "bind_host": "0.0.0.0"}) == {
        "docker": {"bind_host": "0.0.0.0"},
        "pod_manager": {"max_concurrent": 8},
    }
    # the operator's own model server; models come as a list or one comma-separated string
    assert validate_stack_changes(
        {
            "model_server_enabled": True,
            "model_server_url": " http://host.docker.internal:8000 ",
            "model_server_models": "a/b, c:latest,,\n d ",
            "model_server_api_key": " k ",
        }
    ) == {
        "docker": {
            "model_server": {
                "enabled": True,
                "base_url": "http://host.docker.internal:8000",
                "models": ["a/b", "c:latest", "d"],
                "api_key": "k",
            }
        }
    }
    assert validate_stack_changes(
        {
            "external_integrations": [
                {
                    "source_dir": "/var/lib/niuu/private-integrations/compute",
                    "manifest_file": "niuu-module.yaml",
                }
            ]
        }
    ) == {
        "docker": {
            "external_integrations": [
                {
                    "source_dir": "/var/lib/niuu/private-integrations/compute",
                    "definition_files": [],
                    "manifest_file": "niuu-module.yaml",
                }
            ]
        }
    }
    assert validate_stack_changes({"model_server_models": ["x", " y "]}) == {
        "docker": {"model_server": {"models": ["x", "y"]}}
    }
    assert validate_stack_changes(
        {
            "external_integrations": [
                {
                    "source_dir": "/var/lib/niuu/private-integrations/acme",
                    "definition_files": ["integration.yaml", "catalog/more.yaml"],
                }
            ]
        }
    ) == {
        "docker": {
            "external_integrations": [
                {
                    "source_dir": "/var/lib/niuu/private-integrations/acme",
                    "definition_files": ["integration.yaml", "catalog/more.yaml"],
                }
            ]
        }
    }
    for bad in (
        {"model_server_enabled": "yes"},
        {"model_server_url": "models.lan:8000"},
        {"model_server_models": ""},
        {"model_server_models": [1]},
        {"model_server_api_key": 1},
        {"max_sessions": 0},
        {"max_sessions": True},
        {"max_sessions": "8"},
        {"bind_host": "10.0.0.1"},
        {"vllm_enabled": "yes"},
        {"vllm_model": 3},
        {"vllm_max_model_len": 0},
        {"vllm_max_model_len": True},
        {"vllm_gpu_memory_utilization": 2},
        {"vllm_enabled": True, "vllm_model": ""},
        {"external_integrations": "no"},
        {"external_integrations": [{"source_dir": "x", "definition_files": ["../x"]}]},
        {"external_integrations": [{"source_dir": "x"}]},
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

    assert view.current.max_sessions == 4

    view = await controller.stage(
        {"bind_host": "0.0.0.0", "vllm_enabled": True, "vllm_model": "org/m", "max_sessions": 8}
    )
    assert view.staged == {
        "docker": {"bind_host": "0.0.0.0", "vllm": {"enabled": True, "model": "org/m"}},
        "pod_manager": {"max_concurrent": 8},
    }
    assert view.current.bind_host == "127.0.0.1"
    assert view.current.max_sessions == 4
    assert view.effective.max_sessions == 8
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
    (tmp_path / "stack.yaml").write_text(
        f"docker:\n  compose_dir: {tmp_path / 'bundle'}\n  data_dir: {tmp_path}\n"
    )
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
    # The outcome stays readable: the poll that would have seen it can fall
    # into the platform restart. A new apply replaces it.
    again = await controller.status()
    assert again.state == "applied" and again.detail == "Applied."
    assert again.changes == {"docker": {"bind_host": "0.0.0.0"}}
    await controller.stage({"bind_host": "127.0.0.1"})
    with patch.object(sc, "write_bundle"):
        assert (await controller.apply()).state == "applying"
    assert (await controller.status()).state == "applying"


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
    kept = await controller.status()
    assert kept.state == "failed" and "port already allocated" in kept.detail

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


def test_stack_settings_view_reports_the_model_server() -> None:
    settings = CLISettings(
        mode="docker",
        docker={
            "model_server": {
                "enabled": True,
                "base_url": "http://host.docker.internal:8000",
                "models": ["a", "b"],
                "api_key": "k",
            }
        },
    )
    view = sc.stack_settings_view(settings, "10.0.0.5")
    assert view.model_server == sc.ModelServerSettings(
        enabled=True,
        base_url="http://host.docker.internal:8000",
        models=("a", "b"),
        has_api_key=True,
    )
    assert sc.stack_settings_view(CLISettings(mode="docker"), "h").model_server == (
        sc.ModelServerSettings()
    )


@pytest.mark.asyncio
async def test_external_integration_validation_is_real_and_confined_to_managed_root(
    controller: DockerStackController, stack_dir: Path
) -> None:
    package = stack_dir / "private-integrations" / "acme"
    package.mkdir(parents=True)
    (package / "integration.yaml").write_text(
        """\
slug: acme
name: Acme
integration_type: issue_tracker
auth_type: api_key
"""
    )

    result = await controller.validate_external_integration(str(package), ["integration.yaml"])
    assert result.ok is True
    assert [(item.slug, item.name) for item in result.definitions] == [("acme", "Acme")]
    assert await controller.external_integrations_root() == str(stack_dir / "private-integrations")

    outside = await controller.validate_external_integration(
        str(stack_dir.parent / "outside"), ["integration.yaml"]
    )
    assert outside.ok is False
    assert "must be inside" in outside.errors[0]


@pytest.mark.asyncio
async def test_registered_external_integration_validates_its_container_mount(
    controller: DockerStackController, stack_dir: Path, tmp_path: Path
) -> None:
    host_source = tmp_path / "host-owned" / "compute"
    stack = yaml.safe_load((stack_dir / "stack.yaml").read_text())
    stack["docker"]["external_integrations"] = [
        {
            "source_dir": str(host_source),
            "definition_files": [],
            "manifest_file": "niuu-module.yaml",
        }
    ]
    (stack_dir / "stack.yaml").write_text(yaml.safe_dump(stack))

    mount_root = tmp_path / "mounted-integrations"
    package = mount_root / "0"
    package.mkdir(parents=True)
    (package / "niuu-module.yaml").write_text(
        "schema_version: 1\n"
        "id: private-compute\n"
        "requires:\n"
        "  niuu_machine_provider: 1\n"
        "components:\n"
        "  - kind: machine_provider\n"
        "    name: private\n"
        "    adapter: volundr.adapters.outbound.harvester.HarvesterMachineProvider\n"
    )

    with patch.object(sc, "EXTERNAL_INTEGRATION_MOUNT_ROOT", mount_root):
        result = await controller.validate_external_integration(
            str(host_source), [], "niuu-module.yaml"
        )
        changed = await controller.validate_external_integration(
            str(host_source), ["unregistered.yaml"], "niuu-module.yaml"
        )

    assert result.ok is True
    assert result.source_dir == str(host_source)
    assert result.module_id == "private-compute"
    assert changed.ok is False
    assert "must be inside" in changed.errors[0]


@pytest.mark.asyncio
async def test_external_integration_validation_checks_adapter_imports(
    controller: DockerStackController, stack_dir: Path
) -> None:
    package = stack_dir / "private-integrations" / "broken"
    package.mkdir(parents=True)
    (package / "integration.yaml").write_text(
        """\
slug: private-broken
name: Broken
integration_type: issue_tracker
adapter: package_that_does_not_exist.Adapter
"""
    )
    result = await controller.validate_external_integration(str(package), ["integration.yaml"])
    assert result.ok is False
    assert "package_that_does_not_exist" in result.errors[0]


@pytest.mark.asyncio
async def test_external_compute_only_module_validation(
    controller: DockerStackController, stack_dir: Path
) -> None:
    package = stack_dir / "private-integrations" / "compute"
    package.mkdir(parents=True)
    (package / "niuu-module.yaml").write_text(
        "schema_version: 1\n"
        "id: private-compute\n"
        "requires:\n"
        "  niuu_machine_provider: 1\n"
        "components:\n"
        "  - kind: machine_provider\n"
        "    name: private\n"
        "    adapter: volundr.adapters.outbound.harvester.HarvesterMachineProvider\n"
    )

    result = await controller.validate_external_integration(str(package), [], "niuu-module.yaml")

    assert result.ok is True
    assert result.module_id == "private-compute"
    assert [(component.kind, component.name) for component in result.components] == [
        ("machine_provider", "private")
    ]
    assert result.definitions == ()


@pytest.mark.asyncio
async def test_external_module_revalidation_does_not_reuse_import_cache(
    controller: DockerStackController, stack_dir: Path
) -> None:
    package = stack_dir / "private-integrations" / "replaceable"
    package.mkdir(parents=True)
    provider = package / "replaceable_provider.py"
    provider.write_text(
        "from volundr.adapters.outbound.harvester import HarvesterMachineProvider\n"
        "class Provider(HarvesterMachineProvider):\n"
        "    pass\n"
    )
    (package / "niuu-module.yaml").write_text(
        "schema_version: 1\n"
        "id: replaceable\n"
        "requires:\n"
        "  niuu_machine_provider: 1\n"
        "components:\n"
        "  - kind: machine_provider\n"
        "    name: replaceable\n"
        "    adapter: replaceable_provider.Provider\n"
    )

    initial = await controller.validate_external_integration(str(package), [], "niuu-module.yaml")
    assert initial.ok is True

    provider.write_text("class Provider:\n    pass\n")
    replaced = await controller.validate_external_integration(str(package), [], "niuu-module.yaml")

    assert replaced.ok is False
    assert "must implement" in replaced.errors[0]


@pytest.mark.asyncio
async def test_view_judges_fit_by_host_memory_on_a_unified_memory_gpu(
    controller: DockerStackController, stack_dir: Path
) -> None:
    """A DGX Spark's GB10 owns no memory; the 128 GiB of host memory is what models load into."""
    facts = json.loads((stack_dir / "host-facts.json").read_text())
    facts["gpus"] = [
        {
            "name": "NVIDIA GB10",
            "memory_total_mib": 0,
            "driver_version": "580",
            "shares_system_memory": True,
        }
    ]
    (stack_dir / "host-facts.json").write_text(json.dumps(facts))
    view = await controller.view()
    assert view.accelerator_memory_gib == 128
    assert all(m.fits is True for m in view.models)


PULL_OUTPUT = """\
 vllm Pulling
 9b37ee547aa6 Pulling fs layer
 0743781e50d5 Pulling fs layer
 f5cceb5df98d Pulling fs layer
 9b37ee547aa6 Downloading [==>                                                ]  207.6MB/4.311GB
 0743781e50d5 Downloading [===========================================>       ]  404.8MB/463MB
 f5cceb5df98d Downloading [============================================>      ]  232.8MB/263.9MB
 0743781e50d5 Download complete
 f5cceb5df98d Extracting 14 s
 9b37ee547aa6 Downloading [=====>                                             ]  500MB/4.311GB
"""


class TestPullProgress:
    def test_sums_layers_with_the_last_line_per_layer(self) -> None:
        progress = parse_pull_progress(PULL_OUTPUT)
        assert progress is not None
        assert progress.phase == "pulling"
        assert progress.total_bytes == 4_311_000_000 + 463_000_000 + 263_900_000
        # 500 MB in flight + the two layers whose download finished
        assert progress.completed_bytes == 500_000_000 + 463_000_000 + 263_900_000
        assert (
            progress.detail
            == "Pulling the vllm image · 1.2 of 5.0 GB · 1 of 3 layers done · extracting 1"
        )

    def test_all_layers_done_means_starting(self) -> None:
        text = (
            PULL_OUTPUT + " 9b37ee547aa6 Pull complete\n f5cceb5df98d Pull complete\n vllm Pulled\n"
        )
        progress = parse_pull_progress(text)
        assert progress is not None
        assert progress.phase == "starting"
        assert progress.completed_bytes == progress.total_bytes

    def test_nothing_pulled_is_none_and_service_only_is_a_sentence(self) -> None:
        assert parse_pull_progress(" niuu Recreate\n niuu Recreated\n") is None
        early = parse_pull_progress(" vllm Pulling\n")
        assert early is not None and early.detail == "Pulling the vllm image…"


class TestVllmProgress:
    MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16"

    def test_download_measured_on_disk(self, tmp_path: Path) -> None:
        cache = hf_cache_dir(tmp_path / "models", self.MODEL)
        assert (
            cache
            == tmp_path / "models" / "hub" / "models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-BF16"
        )
        (cache / "blobs").mkdir(parents=True)
        (cache / "blobs" / "abc.incomplete").write_bytes(b"x" * 3_000)
        progress = vllm_progress(
            "INFO Starting to load model nvidia/...\n",
            model=self.MODEL,
            cache_dir=cache,
            expected_bytes=62 * 1024**3,
        )
        assert progress.phase == "downloading"
        assert progress.completed_bytes == 3_000
        assert progress.total_bytes == 62 * 1024**3
        assert "of ~67 GB" in progress.detail

    def test_download_complete_then_loading(self, tmp_path: Path) -> None:
        cache = hf_cache_dir(tmp_path / "models", self.MODEL)
        (cache / "blobs").mkdir(parents=True)
        (cache / "blobs" / "abc").write_bytes(b"x" * 10)
        progress = vllm_progress(
            "INFO Starting to load model nvidia/...\n",
            model=self.MODEL,
            cache_dir=cache,
            expected_bytes=0,
        )
        assert progress.phase == "loading"

    def test_log_markers_win_in_order(self, tmp_path: Path) -> None:
        cache = tmp_path / "missing"
        shards = vllm_progress(
            "x\rLoading safetensors checkpoint shards:  23% Completed | 3/13 [00:10<00:40]\r",
            model="org/m",
            cache_dir=cache,
            expected_bytes=0,
        )
        assert shards.phase == "loading"
        assert shards.completed_bytes == 3 and shards.total_bytes == 13
        assert "shard 3 of 13" in shards.detail
        warm = vllm_progress(
            "Loading safetensors checkpoint shards: 100% Completed | 13/13\n"
            "INFO Capturing CUDA graph shapes\n",
            model="org/m",
            cache_dir=cache,
            expected_bytes=0,
        )
        assert warm.phase == "warming"
        serving = vllm_progress(
            "INFO Capturing CUDA graph\nINFO: Application startup complete.\n",
            model="org/m",
            cache_dir=cache,
            expected_bytes=0,
        )
        assert serving.phase == "serving"
        blank = vllm_progress("", model="org/m", cache_dir=cache, expected_bytes=0)
        assert blank.phase == "starting" and "Starting" in blank.detail


@pytest.mark.asyncio
async def test_status_carries_pull_progress_while_applying(
    controller: DockerStackController, client: _Client
) -> None:
    await controller.stage({"vllm_enabled": True, "vllm_model": "org/m"})
    with patch.object(sc, "write_bundle"):
        await controller.apply()
    applier = client.containers.by_name[client.containers.run_kwargs[0]["name"]]
    applier.log_text = PULL_OUTPUT.encode()
    status = await controller.status()
    assert status.state == "applying"
    assert status.progress is not None and status.progress.phase == "pulling"
    assert status.detail.startswith("Pulling the vllm image")

    vllm = _Container("niuu-test-vllm-1")
    vllm.log_text = b"Loading safetensors checkpoint shards:  50% Completed | 2/4\n"
    client.containers.by_name[vllm.name] = vllm
    status = await controller.status()
    assert status.vllm is not None
    assert status.vllm.state == "starting"
    assert status.vllm.progress is not None and status.vllm.progress.phase == "loading"
    assert status.vllm.detail == status.vllm.progress.detail


@pytest.mark.asyncio
async def test_test_model_talks_to_vllm_once_ready(
    controller: DockerStackController, client: _Client
) -> None:
    with pytest.raises(ValueError, match="No local model"):
        await controller.test_model()
    await controller.stage({"vllm_enabled": True, "vllm_model": "org/m"})
    with pytest.raises(ValueError, match="not serving yet"):
        await controller.test_model()

    vllm = _Container("niuu-test-vllm-1")
    vllm.attrs["State"]["Health"] = {"Status": "healthy"}
    client.containers.by_name[vllm.name] = vllm

    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, Any]:
            return {"choices": [{"message": {"content": " OK \n"}}]}

    posted: list[tuple[str, dict[str, Any]]] = []

    class _AsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def __aenter__(self) -> _AsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> _Response:
            posted.append((url, json))
            return _Response()

    with patch.object(sc.httpx, "AsyncClient", _AsyncClient):
        result = await controller.test_model()
    assert result.ok is True and result.reply == "OK" and result.model == "org/m"
    assert posted[0][0] == "http://vllm:8000/v1/chat/completions"
    assert posted[0][1]["model"] == "org/m"
    assert posted[0][1]["max_tokens"] == sc.MODEL_TEST_MAX_TOKENS

    class _Failing(_AsyncClient):
        async def post(self, url: str, json: dict[str, Any]) -> _Response:
            raise sc.httpx.ConnectError("refused")

    with patch.object(sc.httpx, "AsyncClient", _Failing):
        failed = await controller.test_model()
    assert failed.ok is False and "refused" in failed.detail


@pytest.mark.asyncio
async def test_apply_refuses_a_bundle_it_cannot_see(
    controller: DockerStackController, client: _Client, stack_dir: Path, tmp_path: Path
) -> None:
    """Without the secrets file the controller is not looking at the bundle
    `niuu up` wrote; rendering there would give Postgres a new password."""
    del stack_dir
    (tmp_path / "bundle" / "secrets.env").unlink()
    await controller.stage({"bind_host": "0.0.0.0"})
    with patch.object(sc, "write_bundle") as write_bundle, pytest.raises(FileNotFoundError):
        await controller.apply()
    write_bundle.assert_not_called()
    assert client.containers.run_kwargs == []


@pytest.mark.asyncio
async def test_vllm_created_is_starting_not_failed(
    controller: DockerStackController, client: _Client
) -> None:
    await controller.stage({"vllm_enabled": True, "vllm_model": "org/m"})
    vllm = _Container("niuu-test-vllm-1", status="created")
    client.containers.by_name[vllm.name] = vllm
    status = (await controller.status()).vllm
    assert status is not None and status.state == "starting"
    assert status.progress is not None and status.progress.phase == "starting"
    vllm.status = "restarting"
    vllm.log_text = b"exec: --: invalid option\n"
    crashed = (await controller.status()).vllm
    assert crashed is not None and crashed.state == "failed"
    assert "invalid option" in crashed.detail
