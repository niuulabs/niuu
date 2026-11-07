"""Operate real VM allocations through configured providers.

Run `python -m volundr.compute.main --help`. The proof command verifies VM
allocation/readiness/deletion, not a Skuld or agent session. It always attempts
cleanup and never reports success until deletion is confirmed.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import inspect
import json
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import asyncpg
import yaml

from niuu.config import DynamicAdapterConfig
from niuu.ports.credentials import CredentialStorePort
from niuu.utils import import_class, resolve_secret_kwargs
from volundr.adapters.outbound.postgres_compute_leases import PostgresComputeLeaseRepository
from volundr.compute.config import ComputeConfig
from volundr.domain.compute import (
    ComputeLeaseRepository,
    LeaseState,
    MachineProvider,
    MachineState,
)
from volundr.domain.compute_hosts import (
    HostConnector,
    HostInspector,
    HostPlatform,
    RegisteredHostRepository,
)
from volundr.domain.guest_credentials import GuestCredentialDelivery, GuestIdentity
from volundr.domain.service_account_tokens import ServiceAccountTokenPort
from volundr.domain.services.compute_hosts import HostExecution
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.external_modules import load_external_module_manifests

if TYPE_CHECKING:
    from volundr.adapters.inbound.host_connect_keys import (
        EndpointKeyAnswer,
        HostConnectKeyServer,
    )
    from volundr.domain.execution_catalog import ResolvedExecutionPlan
    from volundr.domain.guest_access import GuestAccess
    from volundr.domain.host_preparation import HostPreparation
    from volundr.domain.services.execution_catalog import ExecutionPlanResolver
    from volundr.domain.vm_runtime import SessionImages, VmRuntime


def build_provider(
    config: ComputeConfig, ports: Mapping[str, object] | None = None
) -> MachineProvider:
    """Construct the provider from config kwargs plus the ports its constructor declares.

    A registered-host provider declares its inventory, connectors and inspector; a VM
    provider declares none and receives none.
    """
    kwargs = resolve_secret_kwargs(
        copy.deepcopy(config.provider.kwargs), config.provider.secret_kwargs_env
    )
    if config.auth is not None:
        auth_cls = import_class(config.auth.adapter)
        kwargs["auth"] = auth_cls(
            **resolve_secret_kwargs(
                copy.deepcopy(config.auth.kwargs), config.auth.secret_kwargs_env
            )
        )
    provider_cls = import_class(config.provider.adapter)
    declared = {p.name for p in inspect.signature(provider_cls).parameters.values()}
    provider = provider_cls(
        **kwargs, **{name: port for name, port in (ports or {}).items() if name in declared}
    )
    if not isinstance(provider, MachineProvider):
        raise TypeError("Configured compute provider must implement MachineProvider")
    if provider.fixed_inventory != (config.hosts is not None):
        raise ValueError(
            "compute.hosts configures registered hosts and needs a registered-host provider "
            "(volundr.adapters.outbound.registered_hosts.RegisteredHostMachineProvider); "
            "set both or neither"
        )
    return provider


@dataclass(frozen=True)
class RegisteredHostComponents:
    """Registered-host collaborators, owned by the composition root."""

    repository: RegisteredHostRepository
    connectors: dict[str, HostConnector]
    inspector: HostInspector
    executions: dict[str, HostExecution]

    def provider_ports(self, pool_id: str) -> dict[str, object]:
        return {
            "pool_id": pool_id,
            "host_repository": self.repository,
            "host_connectors": self.connectors,
            "host_inspector": self.inspector,
        }


def build_registered_hosts(
    config: ComputeConfig,
    database_pool: asyncpg.Pool,
    execution: ExecutionComponents | None,
) -> RegisteredHostComponents:
    """Compose the host inventory, its connectors and the inspector for each execution."""
    from volundr.adapters.outbound.host_checks import SshHostInspector
    from volundr.adapters.outbound.postgres_compute_hosts import (
        PostgresRegisteredHostRepository,
    )
    from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess

    hosts = config.hosts
    if hosts is None:
        raise ValueError("compute.hosts is not configured")
    if execution is None:
        raise ValueError(
            "Registered hosts need compute.execution_catalog: each host runs one catalog execution"
        )
    repository = PostgresRegisteredHostRepository(database_pool)
    connectors: dict[str, HostConnector] = {}
    for binding in hosts.connectors:
        connector = import_class(binding.adapter)(
            **resolve_secret_kwargs(copy.deepcopy(binding.kwargs), binding.secret_kwargs_env)
        )
        if not isinstance(connector, HostConnector):
            raise TypeError(f"Connector {binding.binding_id} must implement HostConnector")
        connector.attach(repository)
        connectors[binding.binding_id] = connector
    executions: dict[str, HostExecution] = {}
    accesses: dict[str, GuestAccess] = {}
    runtimes: dict[str, VmRuntime] = {}
    for plan in execution.resolver.plans():
        access = execution.accesses_by_plan[plan.plan_digest]
        if not isinstance(access, SshGuestAccess):
            raise TypeError(
                f"Execution {plan.execution} reaches its hosts through "
                f"{type(access).__name__}; registered hosts need SshGuestAccess"
            )
        runtime = execution.runtimes[plan.plan_digest]
        runtimes[str(plan.execution)] = runtime
        kind = runtime.session_runtime
        executions[str(plan.execution)] = HostExecution(
            execution=str(plan.execution),
            profile=plan.provider_profile,
            runtime=kind,
            label=_runtime_label(runtime, str(plan.execution)),
            supports_slots=runtime.supports_slots,
            requires_root_daemon=runtime.requires_root_daemon,
            slot_limits=runtime.slot_limits,
            forge_public_key=access.public_key,
            ssh_user=access.principal,
            platforms=runtime.host_platforms,
        )
        accesses[str(plan.execution)] = access

    def access_for(host):
        return accesses[host.execution]

    def uid_base_for(host):
        # The uid the host's own platform runs its slots at (a Mac's runtime may differ).
        limits = runtimes[host.execution].for_platform(host.platform).slot_limits
        return limits.uid_base if limits is not None else None

    inspector = SshHostInspector(
        access_for=access_for,
        uid_base_for=uid_base_for,
        connectors=connectors,
        min_free_disk_mib=hosts.min_free_disk_mib,
        command_timeout_seconds=hosts.command_timeout_seconds,
    )
    return RegisteredHostComponents(
        repository=repository,
        connectors=connectors,
        inspector=inspector,
        executions=executions,
    )


def build_host_connect_key_servers(
    connectors: Mapping[str, HostConnector], answer: EndpointKeyAnswer
) -> list[HostConnectKeyServer]:
    """One key-lookup socket per connect endpoint the connectors use; usually none."""
    from volundr.adapters.inbound.host_connect_keys import HostConnectKeyServer

    sockets = sorted(
        {c.endpoint_socket for c in connectors.values() if c.endpoint_socket is not None}
    )
    return [HostConnectKeyServer(socket_path=path, answer=answer) for path in sockets]


def _runtime_label(runtime, execution: str) -> str:
    """What the Add host form calls an execution's runtime."""
    kind = runtime.session_runtime
    if kind is None:
        return execution
    if kind is SessionRuntimeKind.HOST and runtime.host_platforms == {HostPlatform.DARWIN}:
        return _MAC_HOST_LABEL
    if kind is SessionRuntimeKind.HOST and len(runtime.host_platforms) > 1:
        return _ANY_HOST_LABEL
    return _RUNTIME_LABELS.get(kind, type(runtime).__name__)


#: A Mac runs one session at a time, as a fresh macOS user.
_MAC_HOST_LABEL = "Host · macOS (one session, its own user)"
#: One execution for Linux machines and Macs alike (``PlatformHostRuntime``).
_ANY_HOST_LABEL = "Host · Linux (Nix + rootless Podman) or macOS (one session)"
#: What the Add host form calls each runtime a host can run.
_RUNTIME_LABELS = {
    SessionRuntimeKind.HOST: "Host · Nix + rootless Podman",
    SessionRuntimeKind.CONTAINER: "Container (Docker)",
    SessionRuntimeKind.OPENSHELL: "OpenShell",
}


@dataclass(frozen=True)
class GuestCredentialComponents:
    """VM guest credential collaborators, owned (and closed) by the composition root."""

    service_account_tokens: ServiceAccountTokenPort | None
    guest_identity: GuestIdentity | None
    delivery: GuestCredentialDelivery | None


def _build_adapter(config: DynamicAdapterConfig, ports: Mapping[str, object]):
    """Construct with config kwargs plus the ports the constructor declares.

    An adapter that accepts ``**kwargs`` receives every port; one with an exact
    signature receives only its own, so a mistyped config kwarg still fails.
    """
    kwargs = resolve_secret_kwargs(copy.deepcopy(config.kwargs), config.secret_kwargs_env)
    cls = import_class(config.adapter)
    parameters = inspect.signature(cls).parameters.values()
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
        declared = {p.name for p in parameters}
        ports = {name: port for name, port in ports.items() if name in declared}
    return cls(**{**kwargs, **ports})


def build_guest_credentials(
    config: ComputeConfig, ports: Mapping[str, object]
) -> GuestCredentialComponents:
    """Compose the token issuer, guest identity and delivery from dynamic config.

    Each adapter receives its kwargs plus the shared ``ports`` and the components
    built before it; adapters ignore the ports they do not use (``**_extra``).
    """
    tokens = identity = delivery = None
    if config.service_account_tokens is not None:
        tokens = _build_adapter(config.service_account_tokens, ports)
        if not isinstance(tokens, ServiceAccountTokenPort):
            raise TypeError("compute.service_account_tokens must implement ServiceAccountTokenPort")
    if config.guest_identity is not None:
        identity = _build_adapter(config.guest_identity, ports)
        if not isinstance(identity, GuestIdentity):
            raise TypeError("compute.guest_identity must implement GuestIdentity")
    if config.credential_delivery is not None:
        delivery = _build_adapter(
            config.credential_delivery,
            {**ports, "service_account_tokens": tokens, "guest_identity": identity},
        )
        if not isinstance(delivery, GuestCredentialDelivery):
            raise TypeError("compute.credential_delivery must implement GuestCredentialDelivery")
    return GuestCredentialComponents(tokens, identity, delivery)


def build_runtime(
    config: DynamicAdapterConfig | Mapping[str, object],
    *,
    importer: Callable[[str], type] = import_class,
) -> VmRuntime:
    """Compose a runtime tree from dynamic adapter configs at the package root."""
    from volundr.domain.vm_runtime import VmRuntime

    parsed = (
        config
        if isinstance(config, DynamicAdapterConfig)
        else DynamicAdapterConfig.model_validate(dict(config))
    )
    kwargs = resolve_secret_kwargs(parsed.kwargs, parsed.secret_kwargs_env)
    runtime_class = importer(parsed.adapter)
    nested = _runtime_parameters(runtime_class, kwargs)
    composed = {
        name: _runtime_value(value, importer=importer) if name in nested else value
        for name, value in kwargs.items()
    }
    runtime = runtime_class(**composed)
    if not isinstance(runtime, VmRuntime):
        raise TypeError("Configured compute runtime must implement VmRuntime")
    return runtime


def _runtime_parameters(runtime_class: type, kwargs: Mapping[str, object]) -> set[str]:
    """The kwargs whose adapter configs are nested runtimes to build here.

    Only parameters the constructor types as runtimes (``default: VmRuntime``,
    ``profiles: Mapping[str, VmRuntime]``). Any other adapter-shaped mapping is data
    the runtime passes on, such as Skuld's ``preview.router_auth`` for the guest.
    """
    if not any(_has_adapter_config(value) for value in kwargs.values()):
        return set()
    from volundr.domain.vm_runtime import VmRuntime

    try:
        hints = typing.get_type_hints(runtime_class.__init__)
    except NameError as exc:
        raise TypeError(
            f"Compute runtime {runtime_class.__qualname__} has constructor annotations that "
            f"do not resolve ({exc}); nested runtime configs need resolvable types"
        ) from exc
    return {name for name in kwargs if _holds_runtime(hints.get(name), VmRuntime)}


def _has_adapter_config(value) -> bool:
    if isinstance(value, Mapping):
        return "adapter" in value or any(_has_adapter_config(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_has_adapter_config(item) for item in value)
    return False


def _holds_runtime(annotation, runtime_type: type) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, runtime_type):
        return True
    return any(_holds_runtime(arg, runtime_type) for arg in typing.get_args(annotation))


def _runtime_value(value, *, importer: Callable[[str], type]):
    if isinstance(value, Mapping):
        if "adapter" in value and set(value) <= {"adapter", "kwargs", "secret_kwargs_env"}:
            return build_runtime(value, importer=importer)
        return {name: _runtime_value(item, importer=importer) for name, item in value.items()}
    if isinstance(value, list):
        return [_runtime_value(item, importer=importer) for item in value]
    if isinstance(value, tuple):
        return tuple(_runtime_value(item, importer=importer) for item in value)
    return value


@dataclass(frozen=True)
class ExecutionComponents:
    """Composition-root-owned collaborators, keyed by immutable plan digest."""

    resolver: ExecutionPlanResolver
    default_plan: ResolvedExecutionPlan
    runtimes: dict[str, VmRuntime]
    preparations: dict[str, HostPreparation]
    accesses: tuple[GuestAccess, ...]
    accesses_by_plan: dict[str, GuestAccess]


def provider_fingerprint(config: ComputeConfig) -> str:
    """Pin deployment/provider settings without retaining resolved secret values."""
    from volundr.domain.services.execution_catalog import canonical_digest

    provider = config.provider.model_dump(mode="json")
    # Profiles are independently revisioned by MachineProvider.profiles(). An
    # unrelated profile edit must not prevent an existing allocation's deletion.
    provider["kwargs"].pop("profiles", None)
    return canonical_digest(
        {
            "provider": provider,
            "binding": config.provider_binding,
            "auth": config.auth.model_dump(mode="json") if config.auth else None,
        }
    )


def _execution_binding_kwargs(binding) -> dict:
    """Resolve explicit secret references without defaulting to another value."""
    secret_values = resolve_secret_kwargs({}, binding.secret_kwargs_env)
    if set(secret_values) != set(binding.secret_kwargs_env):
        raise ValueError("A configured execution adapter secret environment reference is missing")
    return {**copy.deepcopy(binding.kwargs), **secret_values}


def _execution_component(binding, **collaborators):
    kwargs = _execution_binding_kwargs(binding)
    if set(kwargs) & set(collaborators):
        raise ValueError("Execution adapter kwargs conflict with composed collaborators")
    return import_class(binding.adapter)(**kwargs, **collaborators)


def _execution_runtime(binding, **collaborators) -> VmRuntime:
    """An execution's runtime, with any runtimes it holds built here with the same collaborators.

    A runtime whose constructor types a parameter as runtimes (``platforms: Mapping[str,
    VmRuntime]``) gets them built from their nested adapter configs, each with the
    execution's guest access; it takes no collaborators itself.
    """
    kwargs = _execution_binding_kwargs(binding)
    runtime_class = import_class(binding.adapter)
    nested = _runtime_parameters(runtime_class, kwargs)
    if not nested:
        # The release's session images go only to runtimes that run them.
        accepted = inspect.signature(runtime_class).parameters
        if "session_images" not in accepted:
            collaborators = {k: v for k, v in collaborators.items() if k != "session_images"}
        return _execution_component(binding, **collaborators)
    composed = {
        name: _nested_execution_runtimes(value, collaborators) if name in nested else value
        for name, value in kwargs.items()
    }
    return runtime_class(**composed)


def _nested_execution_runtimes(value, collaborators: Mapping[str, object]):
    if isinstance(value, Mapping) and "adapter" in value:
        child = DynamicAdapterConfig.model_validate(dict(value))
        return _execution_runtime(child, **collaborators)
    if isinstance(value, Mapping):
        return {
            name: _nested_execution_runtimes(item, collaborators) for name, item in value.items()
        }
    raise TypeError("Nested execution runtimes must be adapter configs or mappings of them")


async def resolve_session_images(config: ComputeConfig) -> SessionImages | None:
    """The release's session images, each pinned to the digest its registry serves now."""
    import httpx

    from volundr.adapters.outbound.registry_digests import resolve_image_digest
    from volundr.domain.vm_runtime import SessionImages

    images = config.session_images
    if images is None or not (images.linux or images.darwin):
        return None
    timeout = images.resolve_timeout_seconds
    async with httpx.AsyncClient(follow_redirects=True) as client:
        pinned = {
            name: await resolve_image_digest(reference, client=client, timeout_seconds=timeout)
            if reference
            else None
            for name, reference in (("linux", images.linux), ("darwin", images.darwin))
        }
    return SessionImages(**pinned)


async def build_execution_components(
    config: ComputeConfig, credential_store: CredentialStorePort
) -> ExecutionComponents:
    """Build approved adapters from the file catalog; never import lease payloads."""
    from volundr.domain.guest_access import GuestAccess
    from volundr.domain.host_preparation import HostPreparation
    from volundr.domain.services.execution_catalog import ExecutionPlanResolver
    from volundr.domain.vm_runtime import CredentialAwareVmRuntime, VmRuntime
    from volundr.ports.execution_catalog import ExecutionCatalogPort

    if config.execution_catalog is None or not config.provider_binding:
        raise ValueError("Execution catalogs require compute.provider_binding")
    catalog_config = config.execution_catalog
    catalog = _execution_component(catalog_config)
    if not isinstance(catalog, ExecutionCatalogPort):
        raise TypeError("Configured execution catalog must implement ExecutionCatalogPort")
    resolver = ExecutionPlanResolver(catalog)
    default, plans = resolver.resolve_all()
    session_images = await resolve_session_images(config)
    runtimes, preparations = {}, {}
    opened = []
    accesses = []
    accesses_by_plan = {}
    try:
        for plan in plans:
            if plan.pool_id != config.pool_id or plan.provider_binding != config.provider_binding:
                raise ValueError(
                    "Execution profile does not belong to the configured pool/provider"
                )
            access_binding = plan.access.transport
            access = _execution_component(access_binding)
            if not isinstance(access, GuestAccess):
                raise TypeError("Configured guest access must implement GuestAccess")
            opened.append(access)
            accesses.append(access)
            accesses_by_plan[plan.plan_digest] = access
            access.configure_trust(
                principal=plan.access.principal,
                mode=plan.access.trust.mode.value,
                kwargs=plan.access.trust.kwargs,
            )
            preparation_binding = plan.host_recipe.preparation
            preparation = _execution_component(preparation_binding, guest_access=access)
            if not isinstance(preparation, HostPreparation):
                raise TypeError("Configured host preparation must implement HostPreparation")
            opened.append(preparation)
            runtime_binding = plan.runtime.runtime
            runtime = _execution_runtime(
                runtime_binding,
                guest_access=access,
                host_prepared=True,
                session_images=session_images,
            )
            if not isinstance(runtime, VmRuntime):
                raise TypeError("Configured execution runtime must implement VmRuntime")
            opened.append(runtime)
            if isinstance(runtime, CredentialAwareVmRuntime):
                runtime.configure_credentials(credential_store)
            runtimes[plan.plan_digest] = runtime
            preparations[plan.plan_digest] = preparation
    except BaseException:
        await asyncio.gather(*(item.close() for item in reversed(opened)), return_exceptions=True)
        raise
    return ExecutionComponents(
        resolver, default, runtimes, preparations, tuple(accesses), accesses_by_plan
    )


def emit(event: str, **data) -> None:
    print(json.dumps({"event": event, **data}, default=str), flush=True)


async def prove(
    service: ComputeLeaseService,
    repository: ComputeLeaseRepository,
    config: ComputeConfig,
    *,
    profile: str,
    session_id: UUID,
    owner_id: str,
    tenant_id: str,
) -> None:
    slots = await repository.list_slots(config.pool_id, live_only=False)
    if any(slot.session_id == session_id for slot in slots):
        raise ValueError("Proof requires a new session_id; refusing to touch an existing claim")
    # Print the durable claim key before making any external provisioning request.
    emit("proof_started", pool_id=config.pool_id, session_id=session_id)
    lease_id = None
    try:
        async with asyncio.timeout(config.provisioning_timeout_seconds):
            claim = await service.acquire(
                session_id=session_id, owner_id=owner_id, tenant_id=tenant_id, profile=profile
            )
            lease = claim.allocation
            lease_id = lease.id
            emit(
                "allocation_created",
                lease_id=lease.id,
                state=lease.state,
                machine=lease.machine.model_dump(mode="json") if lease.machine else None,
            )
            while lease.state in {LeaseState.PROVISIONING, LeaseState.READY}:
                if (
                    lease.state == LeaseState.READY
                    and lease.machine
                    and lease.machine.state == MachineState.RUNNING
                    and lease.machine.addresses
                ):
                    break
                await asyncio.sleep(config.poll_interval_seconds)
                lease = await service.reconcile(lease.id)
        if lease.state != LeaseState.READY or lease.machine is None or not lease.machine.addresses:
            raise RuntimeError("VM proof failed: machine is not ready with a reported address")
        emit("vm_ready", lease_id=lease.id, machine=lease.machine.model_dump(mode="json"))
    finally:
        if lease_id is None:
            # A provider may have failed after admission but before acquire returned: find
            # the allocation through the session's binding.
            for slot in await repository.list_slots(config.pool_id, live_only=False):
                if (
                    slot.session_id != session_id
                    or slot.owner_id != owner_id
                    or slot.tenant_id != tenant_id
                ):
                    continue
                machine = await repository.get(slot.allocation_id)
                if machine is not None and machine.state != LeaseState.RELEASED:
                    lease_id = machine.id
                    break
        if lease_id is not None:
            emit("cleanup_started", lease_id=lease_id)
            async with asyncio.timeout(config.cleanup_timeout_seconds):
                lease = await service.release(lease_id)
                while lease.state != LeaseState.RELEASED:
                    await asyncio.sleep(config.poll_interval_seconds)
                    lease = await service.reconcile(lease_id)
            emit("cleanup_verified", lease_id=lease_id)
    emit("proof_passed", session_id=session_id, scope="VM lifecycle only; no agent runtime tested")


async def run(args) -> None:
    load_external_module_manifests(list(getattr(args, "module_manifest", ())))
    config = ComputeConfig.model_validate(yaml.safe_load(Path(args.config).read_text()))
    provider = build_provider(config)
    try:
        if args.command == "inventory":
            emit("inventory", machines=[m.model_dump(mode="json") for m in await provider.list()])
            return
        db = config.database
        if db is None:
            raise ValueError("The standalone compute CLI requires database configuration")
        async with asyncpg.create_pool(
            host=db.host,
            port=db.port,
            user=db.user,
            password=db.password,
            database=db.name,
            min_size=1,
            max_size=2,
        ) as pool:
            repository = PostgresComputeLeaseRepository(pool)
            service = ComputeLeaseService(
                repository,
                provider,
                pool_id=config.pool_id,
                max_machines=config.max_machines,
                bootstrap=config.bootstrap,
                provider_binding=config.provider_binding,
                provider_fingerprint=provider_fingerprint(config),
            )
            if args.command == "prove":
                await prove(
                    service,
                    repository,
                    config,
                    profile=args.profile,
                    session_id=args.session_id,
                    owner_id=args.owner_id,
                    tenant_id=args.tenant_id,
                )
                return
            if args.command == "leases":
                emit(
                    "leases",
                    leases=[
                        lease.model_dump(mode="json")
                        for lease in await repository.list(config.pool_id)
                    ],
                    slots=[
                        slot.model_dump(mode="json")
                        for slot in await repository.list_slots(config.pool_id, live_only=False)
                    ],
                )
                return
            if args.command == "release":
                lease = await service.release(args.lease_id)
            else:
                lease = await service.reconcile(args.lease_id)
            emit("lease", lease=lease.model_dump(mode="json"))
    finally:
        await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True, help="Path to the operator's compute YAML config"
    )
    parser.add_argument(
        "--module-manifest",
        action="append",
        default=[],
        help=(
            "External module manifest to validate before constructing the provider; "
            "repeat for multiple packages"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "inventory", help="Read managed provider resources, including partial creates"
    )
    commands.add_parser("leases", help="Read durable allocation records")
    for command in ("release", "reconcile"):
        item = commands.add_parser(command)
        item.add_argument("--lease-id", type=UUID, required=True)
    proof = commands.add_parser(
        "prove", help="Create one VM, verify readiness, then verify cleanup"
    )
    proof.add_argument("--profile", required=True)
    proof.add_argument("--owner-id", required=True)
    proof.add_argument("--tenant-id", required=True)
    proof.add_argument("--session-id", type=UUID, default=None)
    args = parser.parse_args()
    if args.command == "prove" and args.session_id is None:
        args.session_id = uuid4()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
