"""File-configured execution adapters are composed through their actual ports."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.config import DynamicAdapterConfig
from tests.test_adapters.test_file_execution_catalog import _catalog
from tests.test_adapters.test_profiled_vm_runtime import RecordingRuntime
from volundr.adapters.outbound.file_execution_catalog import FileExecutionCatalog
from volundr.adapters.outbound.profiled_vm_runtime import ProfiledVmRuntime
from volundr.compute import main as module
from volundr.compute.config import ComputeConfig
from volundr.domain.guest_access import GuestAccess
from volundr.domain.host_preparation import HostPreparation
from volundr.domain.vm_runtime import VmRuntime


def configuration(root):
    return ComputeConfig(
        pool_id="default",
        max_machines=1,
        provider_binding="compute-provider",
        provider={"adapter": "tests.Provider"},
        execution_catalog={
            "adapter": "volundr.adapters.outbound.file_execution_catalog.FileExecutionCatalog",
            "kwargs": {"root": str(root)},
        },
    )


def test_runtime_factory_composes_nested_profile_runtimes() -> None:
    config = DynamicAdapterConfig(
        adapter="volundr.adapters.outbound.profiled_vm_runtime.ProfiledVmRuntime",
        kwargs={
            "default": {
                "adapter": ("tests.test_adapters.test_profiled_vm_runtime.RecordingRuntime"),
                "kwargs": {"name": "docker"},
            },
            "profiles": {
                "openshell": {
                    "adapter": ("tests.test_adapters.test_profiled_vm_runtime.RecordingRuntime"),
                    "kwargs": {"name": "openshell"},
                }
            },
            "default_profile": "openshell",
        },
    )

    runtime = module.build_runtime(config)

    assert isinstance(runtime, ProfiledVmRuntime)
    assert isinstance(runtime._default, RecordingRuntime)
    assert runtime._default.name == "docker"
    assert runtime._profiles["openshell"].name == "openshell"


async def test_composes_each_plan_with_shared_access_and_resolved_secret(tmp_path, monkeypatch):
    root, _ = _catalog(tmp_path)
    monkeypatch.setenv("COMPUTE_SSH_PASSPHRASE", "test-only-credential")
    access, preparation, runtime = (
        AsyncMock(spec=GuestAccess),
        AsyncMock(spec=HostPreparation),
        AsyncMock(spec=VmRuntime),
    )
    constructors = []

    def importer(name):
        if name.endswith("FileExecutionCatalog"):
            return FileExecutionCatalog
        target = (
            access if "Transport" in name else preparation if "Preparation" in name else runtime
        )
        constructor = Mock(return_value=target)
        constructors.append((target, constructor))
        return constructor

    monkeypatch.setattr(module, "import_class", importer)
    result = await module.build_execution_components(configuration(root), MemoryCredentialStore())
    digest = result.default_plan.plan_digest
    assert result.runtimes[digest] is runtime
    assert result.preparations[digest] is preparation
    assert constructors[0][1].call_args.kwargs["passphrase"] == "test-only-credential"
    assert constructors[1][1].call_args.kwargs["guest_access"] is access
    assert constructors[2][1].call_args.kwargs["guest_access"] is access
    assert constructors[2][1].call_args.kwargs["host_prepared"] is True
    assert "test-only-credential" not in result.default_plan.model_dump_json()
    access.configure_trust.assert_called_once_with(
        principal="niuu", mode="ssh_ca", kwargs={"ca_path": "/etc/niuu/ssh_ca.pub"}
    )


async def test_missing_execution_secret_never_uses_plain_kwarg_default(tmp_path, monkeypatch):
    root, _ = _catalog(tmp_path)
    monkeypatch.delenv("COMPUTE_SSH_PASSPHRASE", raising=False)
    with pytest.raises(ValueError, match="secret environment reference is missing"):
        await module.build_execution_components(configuration(root), MemoryCredentialStore())


async def test_wrong_pool_fails_before_constructing_access(tmp_path, monkeypatch):
    root, _ = _catalog(tmp_path)
    config = configuration(root).model_copy(update={"pool_id": "another-pool"})
    with pytest.raises(ValueError, match="configured pool/provider"):
        await module.build_execution_components(config, MemoryCredentialStore())


async def test_failed_composition_closes_already_created_access(tmp_path, monkeypatch):
    root, _ = _catalog(tmp_path)
    monkeypatch.setenv("COMPUTE_SSH_PASSPHRASE", "test-only-credential")
    access = AsyncMock(spec=GuestAccess)

    def importer(name):
        if name.endswith("FileExecutionCatalog"):
            return FileExecutionCatalog
        if "Transport" in name:
            return lambda **kwargs: access
        raise ValueError("Preparation adapter missing")

    monkeypatch.setattr(module, "import_class", importer)
    with pytest.raises(ValueError, match="Preparation adapter missing"):
        await module.build_execution_components(configuration(root), MemoryCredentialStore())
    access.close.assert_awaited_once()


def test_provider_fingerprint_tracks_identity_not_resolved_secret_values(tmp_path, monkeypatch):
    root, _ = _catalog(tmp_path)
    config = configuration(root)
    config.provider.secret_kwargs_env = {"token": "PROVIDER_TOKEN"}
    monkeypatch.setenv("PROVIDER_TOKEN", "test-first-token")
    original = module.provider_fingerprint(config)
    monkeypatch.setenv("PROVIDER_TOKEN", "test-rotated-token")
    assert module.provider_fingerprint(config) == original
    changed = config.model_copy(update={"provider_binding": "another-provider"})
    assert module.provider_fingerprint(changed) != original


@pytest.mark.parametrize(
    "adapter,backend",
    [
        ("volundr.adapters.outbound.ssh_vm_runtime.SshContainerVmRuntime", "vm"),
        ("volundr.adapters.outbound.ssh_openshell_vm_runtime.SshOpenShellVmRuntime", "openshell"),
    ],
)
async def test_real_file_catalog_composes_both_ssh_runtimes(tmp_path, adapter, backend):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from tests.test_adapters.test_file_execution_catalog import _yaml
    from volundr.domain.compute import MachineBootstrap

    root, _ = _catalog(tmp_path / "catalog")
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "id_ed25519"
    public = tmp_path / "id_ed25519.pub"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
    )
    _yaml(
        root / "access" / "ssh.yaml",
        {
            "id": "ssh",
            "revision": "v1",
            "transport": {
                "binding_id": "ssh-v1",
                "adapter": "volundr.adapters.outbound.ssh_guest_access.SshGuestAccess",
                "kwargs": {
                    "ssh_private_key_file": str(private),
                    "ssh_public_key_file": str(public),
                    "data_dir": str(tmp_path / "access"),
                    "ssh_user": "ubuntu",
                },
            },
            "principal": "ubuntu",
            "trust": {"mode": "provider_identity"},
        },
    )
    import yaml

    recipe = yaml.safe_load((root / "host-recipes" / "podman.yaml").read_text())
    recipe["preparation"] = {
        "binding_id": "ssh-preparation-v1",
        "adapter": "volundr.adapters.outbound.ssh_host_preparation.SshHostPreparation",
    }
    _yaml(root / "host-recipes" / "podman.yaml", recipe)
    _yaml(
        root / "runtimes" / "openshell.yaml",
        {
            "id": "openshell",
            "revision": "0.3.1-v1",
            "runtime": {
                "binding_id": "test-runtime",
                "adapter": adapter,
                "kwargs": {
                    "skuld_image": "test-only-image@sha256:" + "a" * 64,
                    "data_dir": str(tmp_path / "runtime"),
                },
            },
            "contributor_backend": backend,
            "storage_mode": "vm",
        },
    )
    components = await module.build_execution_components(
        configuration(root), MemoryCredentialStore()
    )
    plan = components.default_plan
    runtime = components.runtimes[plan.plan_digest]
    preparation = components.preparations[plan.plan_digest]
    try:
        bootstrap = preparation.machine_bootstrap(plan.host_recipe, MachineBootstrap())
        assert bootstrap.ssh_authorized_keys
        assert all("session-launch" not in item.path for item in bootstrap.files)
        assert runtime._host_prepared
    finally:
        await asyncio.gather(
            runtime.close(),
            preparation.close(),
            *(access.close() for access in components.accesses),
        )


def test_provider_identity_excludes_independently_revisioned_profile_catalog(tmp_path):
    root, _ = _catalog(tmp_path)
    config = configuration(root)
    config.provider.kwargs = {"base_url": "https://provider.test", "profiles": {"a": {"cpu": 1}}}
    original = module.provider_fingerprint(config)
    config.provider.kwargs["profiles"] = {"a": {"cpu": 2}, "b": {"cpu": 4}}
    assert module.provider_fingerprint(config) == original
    config.provider.kwargs["base_url"] = "https://different-provider.test"
    assert module.provider_fingerprint(config) != original
