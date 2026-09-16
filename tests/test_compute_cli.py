"""Proof-command checks use explicit mocked infrastructure, not live VM evidence."""

import asyncio
import json
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import yaml

from volundr.compute import main as cli
from volundr.compute.config import ComputeConfig
from volundr.domain.compute import ComputeLease, LeaseState, Machine, MachineState


@pytest.fixture
def config():
    return ComputeConfig(
        pool_id="test-pool",
        max_machines=1,
        provider={
            "adapter": "volundr.adapters.outbound.harvester.HarvesterMachineProvider",
            "kwargs": {
                "base_url": "https://harvester.test",
                "namespace": "test",
                "installation_id": "test",
                "profiles": {
                    "small": {
                        "image": "test/image",
                        "network": "test/net",
                        "cpu": 1,
                        "memory_mib": 512,
                        "disk_gib": 1,
                    }
                },
            },
        },
        auth={"adapter": "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"},
        database={},
        provisioning_timeout_seconds=0.1,
        cleanup_timeout_seconds=0.1,
        poll_interval_seconds=0.001,
    )


@pytest.fixture
def lease():
    allocation_id = uuid4()
    return ComputeLease(
        id=allocation_id,
        pool_id="test-pool",
        session_id=uuid4(),
        tenant_id="tenant",
        owner_id="owner",
        profile="small",
        request_fingerprint="test",
        state=LeaseState.READY,
        machine=Machine(
            allocation_id=allocation_id,
            resource_id="test-vm",
            state=MachineState.RUNNING,
            addresses=("192.0.2.10",),
        ),
    )


async def prove(config, lease, service, repository):
    await cli.prove(
        service,
        repository,
        config,
        profile=lease.profile,
        session_id=lease.session_id,
        owner_id=lease.owner_id,
        tenant_id=lease.tenant_id,
    )


def events(capsys):
    return [json.loads(line)["event"] for line in capsys.readouterr().out.splitlines()]


async def test_proof_waits_for_guest_address_and_confirmed_cleanup(config, lease, capsys):
    service, repository = AsyncMock(), AsyncMock()
    repository.list.return_value = []
    service.acquire.return_value = lease.model_copy(
        update={"machine": lease.machine.model_copy(update={"addresses": ()})}
    )
    service.release.return_value = lease.model_copy(update={"state": LeaseState.DRAINING})
    service.reconcile.side_effect = [lease, lease.model_copy(update={"state": LeaseState.RELEASED})]
    await prove(config, lease, service, repository)
    assert events(capsys) == [
        "proof_started",
        "allocation_created",
        "vm_ready",
        "cleanup_started",
        "cleanup_verified",
        "proof_passed",
    ]
    service.release.assert_awaited_once_with(lease.id)
    assert service.reconcile.await_count == 2


async def test_proof_never_touches_existing_claim(config, lease, capsys):
    service, repository = AsyncMock(), AsyncMock()
    repository.list.return_value = [lease]
    with pytest.raises(ValueError, match="existing claim"):
        await prove(config, lease, service, repository)
    service.acquire.assert_not_awaited()
    service.release.assert_not_awaited()
    assert events(capsys) == []


@pytest.mark.parametrize("failure", ["provider", "timeout", "capacity"])
async def test_acquire_failure_recovers_own_claim_for_cleanup(config, lease, capsys, failure):
    service, repository = AsyncMock(), AsyncMock()
    repository.list.side_effect = [[], [] if failure == "capacity" else [lease]]
    service.release.return_value = lease.model_copy(update={"state": LeaseState.RELEASED})

    async def acquire(**kwargs):
        if failure == "timeout":
            await asyncio.sleep(1)
        raise RuntimeError("allocation failed")

    service.acquire.side_effect = acquire
    with pytest.raises((RuntimeError, TimeoutError)):
        await prove(config, lease, service, repository)
    if failure == "capacity":
        service.release.assert_not_awaited()
    else:
        service.release.assert_awaited_once_with(lease.id)
    assert "proof_passed" not in events(capsys)


@pytest.mark.parametrize("failure", ["failed", "no-address", "cleanup"])
async def test_unready_or_uncleaned_vm_never_reports_success(config, lease, capsys, failure):
    service, repository = AsyncMock(), AsyncMock()
    repository.list.return_value = []
    service.acquire.return_value = lease
    service.release.return_value = lease.model_copy(update={"state": LeaseState.RELEASED})
    if failure == "failed":
        service.acquire.return_value = lease.model_copy(update={"state": LeaseState.FAILED})
    if failure == "no-address":
        pending = lease.model_copy(
            update={"machine": lease.machine.model_copy(update={"addresses": ()})}
        )
        service.acquire.return_value = pending
        service.reconcile.return_value = pending
    if failure == "cleanup":
        draining = lease.model_copy(update={"state": LeaseState.DRAINING})
        service.release.return_value = draining
        service.reconcile.return_value = draining
    with pytest.raises((RuntimeError, TimeoutError)):
        await prove(config, lease, service, repository)
    result = events(capsys)
    assert "proof_passed" not in result
    assert ("cleanup_verified" in result) == (failure != "cleanup")


async def test_dynamic_composition_rejects_non_provider(config):
    provider = cli.build_provider(config)
    await provider.close()
    config.provider.adapter = "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"
    with pytest.raises(TypeError):
        cli.build_provider(config)


@pytest.mark.parametrize("command", ["inventory", "leases", "release", "reconcile", "prove"])
async def test_commands_close_provider(config, lease, tmp_path, monkeypatch, capsys, command):
    path = tmp_path / "compute.yaml"
    path.write_text(yaml.safe_dump(config.model_dump()))
    provider, repository, service = AsyncMock(), AsyncMock(), AsyncMock()
    provider.list.return_value = [lease.machine]
    repository.list.return_value = [lease]
    service.release.return_value = service.reconcile.return_value = lease
    monkeypatch.setattr(cli, "build_provider", lambda config: provider)
    monkeypatch.setattr(cli, "PostgresComputeLeaseRepository", lambda pool: repository)
    monkeypatch.setattr(cli, "ComputeLeaseService", lambda *args, **kwargs: service)
    pool = MagicMock()
    pool.__aenter__ = AsyncMock()
    pool.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(cli.asyncpg, "create_pool", lambda **kwargs: pool)
    proof = AsyncMock()
    monkeypatch.setattr(cli, "prove", proof)
    await cli.run(
        Namespace(
            config=path,
            command=command,
            lease_id=lease.id,
            session_id=lease.session_id,
            profile=lease.profile,
            tenant_id=lease.tenant_id,
            owner_id=lease.owner_id,
        )
    )
    provider.close.assert_awaited_once()
    if command == "prove":
        proof.assert_awaited_once()
        return
    expected = "lease" if command in {"release", "reconcile"} else command
    assert events(capsys) == [expected]


def test_cli_assigns_new_proof_session_id(monkeypatch):
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compute",
            "--config",
            "test.yaml",
            "prove",
            "--profile",
            "small",
            "--owner-id",
            "owner",
            "--tenant-id",
            "tenant",
        ],
    )
    run = AsyncMock()
    monkeypatch.setattr(cli, "run", run)
    cli.main()
    assert run.call_args.args[0].session_id is not None


def test_provider_native_auth_requires_no_http_auth_adapter(config, monkeypatch):
    from volundr.domain.compute import MachineProvider

    provider = MagicMock(spec=MachineProvider)
    factory = MagicMock(return_value=provider)
    importer = MagicMock(return_value=factory)
    monkeypatch.setattr(cli, "import_class", importer)
    native = config.model_copy(update={"auth": None})
    assert cli.build_provider(native) is provider
    importer.assert_called_once_with(native.provider.adapter)
    assert "auth" not in factory.call_args.kwargs
