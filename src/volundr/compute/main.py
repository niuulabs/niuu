"""Operate real VM allocations through configured providers.

Run `python -m volundr.compute.main --help`. The proof command verifies VM
allocation/readiness/deletion, not a Skuld or agent session. It always attempts
cleanup and never reports success until deletion is confirmed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import yaml

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


def build_provider(config: ComputeConfig) -> MachineProvider:
    auth_cls = import_class(config.auth.adapter)
    auth = auth_cls(**resolve_secret_kwargs(config.auth.kwargs, config.auth.secret_kwargs_env))
    provider_cls = import_class(config.provider.adapter)
    provider = provider_cls(
        auth=auth,
        **resolve_secret_kwargs(config.provider.kwargs, config.provider.secret_kwargs_env),
    )
    if not isinstance(provider, MachineProvider):
        raise TypeError("Configured compute provider must implement MachineProvider")
    return provider


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
