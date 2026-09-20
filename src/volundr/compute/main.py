"""Operate real VM allocations through configured providers.

Run `python -m volundr.compute.main --help`. The proof command verifies VM
allocation/readiness/deletion, not a Skuld or agent session. It always attempts
cleanup and never reports success until deletion is confirmed.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
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
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.external_modules import load_external_module_manifests

if TYPE_CHECKING:
    from volundr.domain.execution_catalog import ResolvedExecutionPlan
    from volundr.domain.guest_access import GuestAccess
    from volundr.domain.host_preparation import HostPreparation
    from volundr.domain.services.execution_catalog import ExecutionPlanResolver
    from volundr.domain.vm_runtime import VmRuntime


def build_provider(config: ComputeConfig) -> MachineProvider:
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
    provider = provider_cls(**kwargs)
    if not isinstance(provider, MachineProvider):
        raise TypeError("Configured compute provider must implement MachineProvider")
    return provider


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
    composed = {name: _runtime_value(value, importer=importer) for name, value in kwargs.items()}
    runtime = importer(parsed.adapter)(**composed)
    if not isinstance(runtime, VmRuntime):
        raise TypeError("Configured compute runtime must implement VmRuntime")
    return runtime


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
    runtimes, preparations = {}, {}
    opened = []
    accesses = []
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
            runtime = _execution_component(runtime_binding, guest_access=access, host_prepared=True)
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
    return ExecutionComponents(resolver, default, runtimes, preparations, tuple(accesses))


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
    if any(lease.session_id == session_id for lease in await repository.list(config.pool_id)):
        raise ValueError("Proof requires a new session_id; refusing to touch an existing claim")
    # Print the durable claim key before making any external provisioning request.
    emit("proof_started", pool_id=config.pool_id, session_id=session_id)
    lease_id = None
    try:
        async with asyncio.timeout(config.provisioning_timeout_seconds):
            lease = await service.acquire(
                session_id=session_id, owner_id=owner_id, tenant_id=tenant_id, profile=profile
            )
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
            # A provider may have failed after admission but before acquire returned.
            candidates = [
                lease
                for lease in await repository.list(config.pool_id)
                if lease.session_id == session_id
                and lease.owner_id == owner_id
                and lease.tenant_id == tenant_id
                and lease.state != LeaseState.RELEASED
            ]
            if candidates:
                lease_id = candidates[0].id
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
