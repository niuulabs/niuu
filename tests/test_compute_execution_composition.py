"""File-configured execution adapters are composed through their actual ports."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.config import DynamicAdapterConfig
from tests.compute_fakes import Provider
from tests.test_adapters.test_file_execution_catalog import _catalog
from tests.test_adapters.test_profiled_vm_runtime import RecordingRuntime
from tests.test_adapters.test_vm_execution_lifecycle import RecordingPlanRuntime
from volundr.adapters.outbound.file_execution_catalog import FileExecutionCatalog
from volundr.adapters.outbound.profiled_vm_runtime import ProfiledVmRuntime
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.compute import main as module
from volundr.compute.config import ComputeConfig
from volundr.domain.compute_hosts import LINUX_ONLY, HostConnector, HostInspector, HostPlatform
from volundr.domain.guest_access import GuestAccess
from volundr.domain.host_preparation import HostPreparation
from volundr.domain.services.compute_hosts import HostExecution
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SlotLimits, VmRuntime


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


def test_runtime_factory_passes_adapter_configs_for_the_guest_through(tmp_path) -> None:
    # Skuld's preview.router_auth is an adapter config for the guest, not a runtime:
    # building it here failed startup with "must implement VmRuntime".
    from tests.test_adapters.test_ssh_vm_runtime import (
        _ROUTER_AUTH,
        _launch_environment,
        _preview_runtime,
    )

    template = _preview_runtime(tmp_path)
    config = DynamicAdapterConfig(
        adapter="volundr.adapters.outbound.ssh_vm_runtime.SshContainerVmRuntime",
        kwargs={
            "ssh_private_key_file": str(tmp_path / "preview-key"),
            "ssh_public_key_file": str(tmp_path / "preview-key.pub"),
            "data_dir": str(tmp_path / "preview-data"),
            "skuld_image": "test/skuld:dev",
            "preview_router_auth": _ROUTER_AUTH,
        },
    )

    runtime = module.build_runtime(config)

    assert type(runtime) is type(template)
    environment = _launch_environment(runtime)
    assert environment["SKULD__PREVIEW__ENABLED"] == "true"
    assert json.loads(environment["SKULD__PREVIEW__ROUTER_AUTH"]) == _ROUTER_AUTH


def test_runtime_factory_builds_runtimes_only_where_the_constructor_takes_them() -> None:
    class Wrapping(RecordingRuntime):
        def __init__(self, *, inner: VmRuntime, settings: dict) -> None:
            super().__init__(name="wrapping")
            self.inner = inner
            self.settings = settings

    inner = {"adapter": "tests.test_adapters.test_profiled_vm_runtime.RecordingRuntime"}
    settings = {"adapter": "somewhere.else.Adapter", "kwargs": {"x": 1}}
    importer = Mock(
        side_effect=lambda name: Wrapping if name == "wrapping" else module.import_class(name)
    )

    runtime = module.build_runtime(
        {
            "adapter": "wrapping",
            "kwargs": {"inner": {**inner, "kwargs": {"name": "docker"}}, "settings": settings},
        },
        importer=importer,
    )

    assert isinstance(runtime.inner, RecordingRuntime)
    assert runtime.inner.name == "docker"
    assert runtime.settings == settings


def test_runtime_factory_refuses_nested_configs_it_cannot_type() -> None:
    class Unresolvable(RecordingRuntime):
        def __init__(self, *, inner: "NotImported") -> None:  # noqa: F821
            super().__init__(name="unresolvable")

    with pytest.raises(TypeError, match="do not resolve"):
        module.build_runtime(
            {"adapter": "unresolvable", "kwargs": {"inner": {"adapter": "x.Y"}}},
            importer=lambda name: Unresolvable,
        )


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
                    # An OpenShell sandbox never leaves Provisioning without a policy.
                    **({"policy": "version: 1\n"} if backend == "openshell" else {}),
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


# -- registered hosts: provider ports, the hosts/provider pairing and the host components -------

HOSTS = {
    "join_url": "https://forge.example.test",
    "connectors": [
        {
            "adapter": "volundr.adapters.outbound.host_connectors.DirectSshConnector",
            "binding_id": "direct",
            "kwargs": {"allowed_sources": []},
        }
    ],
    "reserved_cpu": 1,
    "reserved_memory_mib": 1024,
    "min_free_disk_mib": 2048,
}


class DeclaringProvider(Provider):
    """Records the keyword arguments the composition root constructed it with."""

    def __init__(self, *, label, pool_id=None, host_repository=None):
        super().__init__()
        self.received = {"label": label, "pool_id": pool_id, "host_repository": host_repository}


class PlainProvider(Provider):
    def __init__(self, *, label):
        super().__init__()
        self.label = label


class FixedProvider(Provider):
    def __init__(self, *, label):
        super().__init__()

    @property
    def fixed_inventory(self):
        return True


def _provider_config(**update) -> ComputeConfig:
    base = {
        "pool_id": "pool",
        "max_machines": 2,
        "provider": {"adapter": "tests.Provider", "kwargs": {"label": "x"}},
    }
    return ComputeConfig(**{**base, **update})


def _use_provider(monkeypatch, provider_cls):
    monkeypatch.setattr(module, "import_class", lambda name: provider_cls)


def test_build_provider_injects_only_the_ports_the_provider_declares(monkeypatch):
    _use_provider(monkeypatch, DeclaringProvider)
    repository = object()
    ports = {
        "pool_id": "pool",
        "host_repository": repository,
        "host_connectors": {"direct": object()},
        "host_inspector": object(),
    }

    provider = module.build_provider(_provider_config(), ports)

    assert provider.received == {"label": "x", "pool_id": "pool", "host_repository": repository}


def test_build_provider_hands_a_vm_provider_none_of_the_ports(monkeypatch):
    _use_provider(monkeypatch, PlainProvider)

    provider = module.build_provider(
        _provider_config(), {"pool_id": "pool", "host_repository": object()}
    )

    assert provider.label == "x"


def test_build_provider_without_ports_constructs_from_config_alone(monkeypatch):
    _use_provider(monkeypatch, DeclaringProvider)

    provider = module.build_provider(_provider_config())

    assert provider.received == {"label": "x", "pool_id": None, "host_repository": None}


def test_registered_hosts_without_a_registered_host_provider_are_refused(monkeypatch):
    _use_provider(monkeypatch, PlainProvider)

    with pytest.raises(ValueError, match="compute.hosts configures registered hosts") as refused:
        module.build_provider(_provider_config(hosts=HOSTS))

    assert "RegisteredHostMachineProvider" in str(refused.value)
    assert "set both or neither" in str(refused.value)


def test_a_registered_host_provider_without_compute_hosts_is_refused(monkeypatch):
    _use_provider(monkeypatch, FixedProvider)

    with pytest.raises(ValueError, match="set both or neither"):
        module.build_provider(_provider_config())


def test_a_registered_host_provider_with_compute_hosts_is_accepted(monkeypatch):
    _use_provider(monkeypatch, FixedProvider)

    provider = module.build_provider(_provider_config(hosts=HOSTS))

    assert provider.fixed_inventory is True


class _HostRuntime(RecordingPlanRuntime):
    def __init__(self, name, kind, *, slots=False, root_daemon=False, limits=None, platforms=None):
        super().__init__(name)
        self._kind, self._slots, self._root, self._limits = kind, slots, root_daemon, limits
        self._platforms = platforms

    @property
    def host_platforms(self):
        return self._platforms or super().host_platforms

    @property
    def session_runtime(self):
        return self._kind

    @property
    def supports_slots(self):
        return self._slots

    @property
    def requires_root_daemon(self):
        return self._root

    @property
    def slot_limits(self):
        return self._limits


def _ssh_access(tmp_path, name, ssh_user):
    key = Ed25519PrivateKey.generate()
    private, public = tmp_path / f"{name}-key", tmp_path / f"{name}-key.pub"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
    )
    return SshGuestAccess(
        ssh_private_key_file=str(private),
        ssh_public_key_file=str(public),
        data_dir=str(tmp_path / f"{name}-data"),
        ssh_user=ssh_user,
        bootstrap_delivery="registered",
    )


def _components(resolver, runtimes, accesses) -> module.ExecutionComponents:
    plans = {plan.execution.id: plan for plan in resolver.plans()}
    return module.ExecutionComponents(
        resolver=resolver,
        default_plan=resolver.resolve(),
        runtimes={plans[name].plan_digest: runtime for name, runtime in runtimes.items()},
        preparations={},
        accesses=tuple(accesses.values()),
        accesses_by_plan={plans[name].plan_digest: access for name, access in accesses.items()},
    )


def _hosts_config(**update) -> ComputeConfig:
    return _provider_config(hosts=HOSTS, **update)


def test_registered_hosts_need_the_hosts_configuration():
    with pytest.raises(ValueError, match="compute.hosts is not configured"):
        module.build_registered_hosts(_provider_config(), object(), None)


def test_registered_hosts_need_an_execution_catalog():
    with pytest.raises(ValueError, match="need compute.execution_catalog"):
        module.build_registered_hosts(_hosts_config(), object(), None)


def test_registered_hosts_refuse_an_access_that_is_not_ssh(tmp_path):
    resolver = _catalog_resolver(tmp_path)
    plan = resolver.resolve()
    execution = module.ExecutionComponents(
        resolver=resolver,
        default_plan=plan,
        runtimes={plan.plan_digest: _HostRuntime("vm", None)},
        preparations={},
        accesses=(),
        accesses_by_plan={plan.plan_digest: AsyncMock(spec=GuestAccess)},
    )

    with pytest.raises(TypeError, match="registered hosts need SshGuestAccess") as refused:
        module.build_registered_hosts(_hosts_config(), object(), execution)

    assert str(plan.execution) in str(refused.value)


def test_a_connector_that_is_not_a_host_connector_is_refused(tmp_path):
    hosts = {
        **HOSTS,
        "connectors": [{"adapter": "pathlib.PurePosixPath", "binding_id": "direct", "kwargs": {}}],
    }
    config = _provider_config(hosts=hosts)

    with pytest.raises(TypeError, match="Connector direct must implement HostConnector"):
        module.build_registered_hosts(config, object(), _two_plan_components(tmp_path))


def _catalog_resolver(tmp_path):
    from tests.test_adapters.test_vm_session_runtimes import _catalog

    return _catalog(tmp_path)


def _two_plan_components(tmp_path):
    resolver = _catalog_resolver(tmp_path)
    return _components(
        resolver,
        {
            "vm": _HostRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": _HostRuntime("vm-b", SessionRuntimeKind.HOST),
        },
        {"vm": _ssh_access(tmp_path, "vm", "ubuntu"), "vm-b": _ssh_access(tmp_path, "b", "niuu")},
    )


def test_registered_hosts_describe_each_execution_with_its_runtime_limits_and_ssh_user(tmp_path):
    resolver = _catalog_resolver(tmp_path)
    plans = {plan.execution.id: plan for plan in resolver.plans()}
    limits = SlotLimits(uid_base=61000, cpu=2.0, memory_mib=4096, pids=512)
    accesses = {
        "vm": _ssh_access(tmp_path, "vm", "ubuntu"),
        "vm-b": _ssh_access(tmp_path, "b", "niuu"),
    }
    execution = _components(
        resolver,
        {
            "vm": _HostRuntime(
                "vm", SessionRuntimeKind.CONTAINER, slots=True, root_daemon=True, limits=limits
            ),
            "vm-b": _HostRuntime("vm-b", SessionRuntimeKind.HOST, slots=False),
        },
        accesses,
    )

    hosts = module.build_registered_hosts(_hosts_config(), MagicMock(), execution)

    container = hosts.executions[str(plans["vm"].execution)]
    assert container == HostExecution(
        execution=str(plans["vm"].execution),
        profile=plans["vm"].provider_profile,
        runtime=SessionRuntimeKind.CONTAINER,
        label="Container (Docker)",
        supports_slots=True,
        requires_root_daemon=True,
        slot_limits=limits,
        forge_public_key=accesses["vm"].public_key,
        ssh_user="ubuntu",
        platforms=LINUX_ONLY,
    )
    assert container.refusal is not None
    host = hosts.executions[str(plans["vm-b"].execution)]
    assert (host.runtime, host.label) == (SessionRuntimeKind.HOST, "Host · Nix + rootless Podman")
    assert (host.supports_slots, host.requires_root_daemon, host.slot_limits) == (
        False,
        False,
        None,
    )
    assert (host.forge_public_key, host.ssh_user) == (accesses["vm-b"].public_key, "niuu")
    assert host.refusal is None and host.exclusive_only


def test_a_mac_host_runtime_is_labelled_as_macos(tmp_path):
    resolver = _catalog_resolver(tmp_path)
    plans = {plan.execution.id: plan for plan in resolver.plans()}
    accesses = {name: _ssh_access(tmp_path, name, "root") for name in ("vm", "vm-b")}
    darwin = frozenset({HostPlatform.DARWIN})
    execution = _components(
        resolver,
        {
            "vm": _HostRuntime("vm", SessionRuntimeKind.HOST),
            "vm-b": _HostRuntime("vm-b", SessionRuntimeKind.HOST, platforms=darwin),
        },
        accesses,
    )

    hosts = module.build_registered_hosts(_hosts_config(), MagicMock(), execution)

    mac = hosts.executions[str(plans["vm-b"].execution)]
    assert (mac.label, mac.platforms) == ("Host · macOS (one session, its own user)", darwin)
    linux = hosts.executions[str(plans["vm"].execution)]
    assert linux.label == "Host · Nix + rootless Podman"


def test_a_runtime_without_a_kind_is_labelled_by_its_execution(tmp_path):
    resolver = _catalog_resolver(tmp_path)
    plans = {plan.execution.id: plan for plan in resolver.plans()}
    execution = _components(
        resolver,
        {
            "vm": _HostRuntime("vm", None),
            "vm-b": _HostRuntime("vm-b", SessionRuntimeKind.OPENSHELL),
        },
        {"vm": _ssh_access(tmp_path, "vm", "ubuntu"), "vm-b": _ssh_access(tmp_path, "b", "ubuntu")},
    )

    hosts = module.build_registered_hosts(_hosts_config(), MagicMock(), execution)

    assert hosts.executions[str(plans["vm"].execution)].label == str(plans["vm"].execution)
    assert hosts.executions[str(plans["vm-b"].execution)].label == "OpenShell"


def test_registered_host_components_carry_the_connectors_inspector_and_provider_ports(tmp_path):
    database = MagicMock()
    hosts = module.build_registered_hosts(_hosts_config(), database, _two_plan_components(tmp_path))

    assert set(hosts.connectors) == {"direct"}
    assert isinstance(hosts.connectors["direct"], HostConnector)
    assert isinstance(hosts.inspector, HostInspector)
    assert hosts.repository._pool is database
    ports = hosts.provider_ports("pool")
    assert ports == {
        "pool_id": "pool",
        "host_repository": hosts.repository,
        "host_connectors": hosts.connectors,
        "host_inspector": hosts.inspector,
    }


def test_the_inspector_reaches_a_host_through_its_executions_access_and_uid_base(tmp_path):
    resolver = _catalog_resolver(tmp_path)
    plans = {plan.execution.id: plan for plan in resolver.plans()}
    limits = SlotLimits(uid_base=62000, cpu=None, memory_mib=None, pids=None)
    accesses = {
        "vm": _ssh_access(tmp_path, "vm", "ubuntu"),
        "vm-b": _ssh_access(tmp_path, "b", "niuu"),
    }
    execution = _components(
        resolver,
        {
            "vm": _HostRuntime("vm", SessionRuntimeKind.CONTAINER, limits=limits),
            "vm-b": _HostRuntime("vm-b", SessionRuntimeKind.HOST),
        },
        accesses,
    )

    hosts = module.build_registered_hosts(_hosts_config(), MagicMock(), execution)

    isolated = SimpleNamespace(execution=str(plans["vm"].execution))
    plain = SimpleNamespace(execution=str(plans["vm-b"].execution))
    assert hosts.inspector._access_for(isolated) is accesses["vm"]
    assert hosts.inspector._access_for(plain) is accesses["vm-b"]
    assert hosts.inspector._uid_base_for(isolated) == 62000
    assert hosts.inspector._uid_base_for(plain) is None
    assert hosts.inspector._floor == 2048


async def test_execution_components_record_each_plans_access(tmp_path, monkeypatch):
    root, _ = _catalog(tmp_path)
    monkeypatch.setenv("COMPUTE_SSH_PASSPHRASE", "test-only-credential")
    access = AsyncMock(spec=GuestAccess)

    def importer(name):
        if name.endswith("FileExecutionCatalog"):
            return FileExecutionCatalog
        if "Transport" in name:
            return lambda **kwargs: access
        if "Preparation" in name:
            return lambda **kwargs: AsyncMock(spec=HostPreparation)
        return lambda **kwargs: AsyncMock(spec=VmRuntime)

    monkeypatch.setattr(module, "import_class", importer)

    result = await module.build_execution_components(configuration(root), MemoryCredentialStore())

    assert result.accesses_by_plan == {result.default_plan.plan_digest: access}
    assert result.accesses == (access,)
    assert set(result.accesses_by_plan) == set(result.runtimes)


class _EndpointConnector(HostConnector):
    """Records the inventory it is attached to; has a connect endpoint socket."""

    def __init__(self, socket=None):
        self.socket = socket
        self.inventory = None

    kind = "fake"
    label = "fake"

    def attach(self, inventory):
        self.inventory = inventory

    @property
    def endpoint_socket(self):
        return self.socket

    def join_setup(self, host):
        raise NotImplementedError

    async def admit(self, host, offered):
        raise NotImplementedError

    def dial(self, host):
        raise NotImplementedError

    async def status(self, host):
        raise NotImplementedError

    async def revoke(self, host):
        raise NotImplementedError


def test_every_connector_is_attached_to_the_host_inventory(tmp_path, monkeypatch):
    connector = _EndpointConnector()
    real_import = module.import_class
    monkeypatch.setattr(
        module,
        "import_class",
        lambda name: (
            (lambda **_kwargs: connector) if name.endswith("Connector") else real_import(name)
        ),
    )
    hosts = module.build_registered_hosts(
        _hosts_config(), MagicMock(), _two_plan_components(tmp_path)
    )
    assert connector.inventory is hosts.repository


def test_one_key_server_per_connect_endpoint_socket():
    async def answer(**_request):
        return None

    connectors = {
        "direct": _EndpointConnector(),
        "reverse-a": _EndpointConnector("/run/niuu-connect/keys.sock"),
        "reverse-b": _EndpointConnector("/run/niuu-connect/keys.sock"),
        "reverse-c": _EndpointConnector("/run/other/keys.sock"),
    }
    servers = module.build_host_connect_key_servers(connectors, answer)
    assert [server.socket_path for server in servers] == [
        "/run/niuu-connect/keys.sock",
        "/run/other/keys.sock",
    ]
    assert module.build_host_connect_key_servers({"direct": _EndpointConnector()}, answer) == []
