"""The local target provisions one gbrain service per org on this computer, once.

gbrain, the service manager and the health probe are stubbed; the directory, the
0600 secrets, the record and the lock are real.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock

import pytest

from mimir.adapters.local import LocalKnowledgeDeploymentAdapter
from mimir.ports.deployment import (
    MemoryProvisioningError,
    MemorySystemPhase,
    memory_system_key,
    memory_system_name,
)

TENANT = "initech"
KEY = memory_system_key(TENANT)


def _target(tmp_path, *, answers: bool = True, **kwargs) -> LocalKnowledgeDeploymentAdapter:
    target = LocalKnowledgeDeploymentAdapter(
        root=str(tmp_path),
        gbrain_command=["gbrain"],
        startup_timeout=kwargs.pop("startup_timeout", 5),
        poll_interval=0.01,
        **kwargs,
    )
    target._run = AsyncMock(return_value="")
    target._install = AsyncMock()
    target._answers = AsyncMock(return_value=answers)
    return target


async def _settle(target: LocalKnowledgeDeploymentAdapter) -> None:
    await asyncio.gather(*target._memory_tasks.values())


def _directory(tmp_path):
    return tmp_path / ".memory" / KEY


class TestProvisioning:
    async def test_the_first_call_starts_it_in_the_background(self, tmp_path) -> None:
        target = _target(tmp_path)
        record = await target.provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.PREPARING
        assert record.started
        await _settle(target)
        ready = await target.provision_memory_system(TENANT)
        assert ready.phase is MemorySystemPhase.READY
        assert not ready.started
        assert ready.name == memory_system_name(TENANT)
        assert ready.mcp_url.startswith("http://127.0.0.1:") and ready.mcp_url.endswith("/mcp")

    async def test_gbrain_is_initialised_database_only_with_its_owner_token(self, tmp_path) -> None:
        target = _target(tmp_path)
        await target.provision_memory_system(TENANT)
        await _settle(target)
        args = target._run.await_args.args
        assert args[:2] == ("gbrain", "init")
        assert "--db-only" in args and "--pglite" in args and "--no-embedding" in args
        env = target._run.await_args.kwargs["env"]
        directory = _directory(tmp_path)
        assert env["GBRAIN_HOME"] == str(directory)
        token = (directory / "admin-token").read_text()
        assert env["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"] == token
        runner = json.loads((directory / "runtime.json").read_text())
        assert runner["environment"]["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"] == token
        name, command, where = target._install.await_args.args
        assert name == KEY and where == directory
        assert command[-1] == str(directory / "runtime.json")
        assert target._install.await_args.kwargs == {"kind": "memory"}

    async def test_its_secrets_are_private_and_kept(self, tmp_path) -> None:
        target = _target(tmp_path)
        await target.provision_memory_system(TENANT)
        await _settle(target)
        directory = _directory(tmp_path)
        assert directory.stat().st_mode & 0o777 == 0o700
        for name in ("admin-token", "signing-key", "memory.json", "runtime.json"):
            assert (directory / name).stat().st_mode & 0o777 == 0o600
        first = await target.memory_system(TENANT)
        second = await _target(tmp_path).memory_system(TENANT)
        assert (first.admin_token, first.signing_key) == (second.admin_token, second.signing_key)
        assert first.admin_token == (directory / "admin-token").read_text()
        assert first.signing_key == (directory / "signing-key").read_text()

    async def test_concurrent_first_calls_in_one_process_provision_once(self, tmp_path) -> None:
        target = _target(tmp_path)
        records = await asyncio.gather(*(target.provision_memory_system(TENANT) for _ in range(5)))
        await _settle(target)
        assert [r.started for r in records].count(True) == 1
        assert target._install.await_count == 1

    async def test_another_process_provisioning_it_is_preparing_not_a_second_start(
        self, tmp_path
    ) -> None:
        first = _target(tmp_path)
        gate = asyncio.Event()

        async def slow_init(*args, env=None):
            await gate.wait()
            return ""

        first._run = AsyncMock(side_effect=slow_init)
        assert (await first.provision_memory_system(TENANT)).started
        second = _target(tmp_path)  # its own tasks, as another process has
        record = await second.provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.PREPARING
        assert not record.started
        assert (await second.memory_system(TENANT)).phase is MemorySystemPhase.PREPARING
        gate.set()
        await _settle(first)
        assert (await second.memory_system(TENANT)).phase is MemorySystemPhase.READY
        second._run.assert_not_awaited()

    async def test_an_interrupted_attempt_failed_and_is_not_resumed(self, tmp_path) -> None:
        directory = _directory(tmp_path)
        directory.mkdir(parents=True)
        (directory / "memory.json").write_text(json.dumps({"tenant_id": TENANT, "ready": False}))
        target = _target(tmp_path)
        record = await target.provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.FAILED
        assert "stopped before it finished" in record.message
        assert str(directory) in record.message
        assert (await target.memory_system(TENANT)).phase is MemorySystemPhase.FAILED
        target._run.assert_not_awaited()

    async def test_a_failed_install_is_recorded_with_its_remedy(self, tmp_path) -> None:
        target = _target(tmp_path)
        target._install = AsyncMock(side_effect=RuntimeError("launchd unavailable"))
        await target.provision_memory_system(TENANT)
        await _settle(target)
        record = await target.provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.FAILED
        assert "launchd unavailable" in record.message
        assert "remove that directory" in record.message
        target._install.assert_awaited_once()

    async def test_a_gbrain_that_never_answers_failed_naming_the_knob(self, tmp_path) -> None:
        target = _target(tmp_path, answers=False, startup_timeout=0.05)
        await target.provision_memory_system(TENANT)
        await _settle(target)
        record = await target.memory_system(TENANT)
        assert record.phase is MemorySystemPhase.FAILED
        assert "startup_timeout" in record.message

    async def test_without_a_gbrain_command_it_raises(self, tmp_path) -> None:
        target = LocalKnowledgeDeploymentAdapter(root=str(tmp_path))
        with pytest.raises(MemoryProvisioningError, match="gbrain_command"):
            await target.provision_memory_system(TENANT)

    async def test_nothing_provisioned_reads_as_none(self, tmp_path) -> None:
        assert await _target(tmp_path).memory_system(TENANT) is None

    async def test_a_record_of_another_org_is_refused(self, tmp_path) -> None:
        directory = _directory(tmp_path)
        directory.mkdir(parents=True)
        (directory / "memory.json").write_text(json.dumps({"tenant_id": "globex"}))
        with pytest.raises(MemoryProvisioningError, match="collide"):
            await _target(tmp_path).memory_system(TENANT)

    async def test_an_emptied_secret_raises(self, tmp_path) -> None:
        target = _target(tmp_path)
        await target.provision_memory_system(TENANT)
        await _settle(target)
        (_directory(tmp_path) / "signing-key").write_text("")
        with pytest.raises(MemoryProvisioningError, match="restore it"):
            await target.memory_system(TENANT)

    async def test_closing_cancels_an_attempt_in_flight(self, tmp_path) -> None:
        target = _target(tmp_path)
        gate = asyncio.Event()

        async def slow_init(*args, env=None):
            await gate.wait()
            return ""

        target._run = AsyncMock(side_effect=slow_init)
        await target.provision_memory_system(TENANT)
        await target.close()
        # The next process finds the attempt half-way and says so.
        record = await _target(tmp_path).memory_system(TENANT)
        assert record.phase is MemorySystemPhase.FAILED


class TestListingAndDatabase:
    async def test_memory_systems_lists_every_org(self, tmp_path) -> None:
        target = _target(tmp_path)
        await target.provision_memory_system(TENANT)
        await target.provision_memory_system("globex")
        await _settle(target)
        records = {r.tenant_id: r.phase for r in await target.memory_systems()}
        assert records == {TENANT: MemorySystemPhase.READY, "globex": MemorySystemPhase.READY}
        assert (await target.list_deployments())["releases"] == []

    async def test_a_directory_not_made_by_the_gateway_is_refused(self, tmp_path) -> None:
        stray = tmp_path / ".memory" / "0000000000000000"
        stray.mkdir(parents=True)
        (stray / "memory.json").write_text(json.dumps({"tenant_id": TENANT}))
        with pytest.raises(MemoryProvisioningError, match="not made by the gateway"):
            await _target(tmp_path).memory_systems()

    async def test_an_embedded_database_is_started_for_init_and_stopped(
        self, tmp_path, monkeypatch
    ) -> None:
        database = AsyncMock()
        started = AsyncMock(return_value=(database, "postgresql://u:p@127.0.0.1:1/memory_x"))
        monkeypatch.setattr("mimir.local_runtime.start_database", started)
        target = _target(
            tmp_path,
            database={"adapter": "x.Postgres"},
            gbrain_environment={"GBRAIN_EXTRA": "db_{name}"},
        )
        await target.provision_memory_system(TENANT)
        await _settle(target)
        args = target._run.await_args.args
        assert "--non-interactive" in args and "--pglite" not in args
        assert target._run.await_args.kwargs["env"]["GBRAIN_EXTRA"] == f"db_memory_{KEY}"
        database.stop.assert_awaited_once()
        assert (await target.memory_system(TENANT)).phase is MemorySystemPhase.READY

    async def test_a_failure_after_the_database_started_stops_it(
        self, tmp_path, monkeypatch
    ) -> None:
        database = AsyncMock()
        database.stop.side_effect = [RuntimeError("still running")]
        monkeypatch.setattr(
            "mimir.local_runtime.start_database",
            AsyncMock(return_value=(database, "postgresql://u:p@127.0.0.1:1/x")),
        )
        target = _target(tmp_path, database={"adapter": "x.Postgres"})
        target._run = AsyncMock(side_effect=RuntimeError("init failed"))
        await target.provision_memory_system(TENANT)
        await _settle(target)
        record = await target.memory_system(TENANT)
        assert "init failed" in record.message
        database.stop.assert_awaited_once()


async def test_knowledge_instances_never_see_memory_systems(tmp_path) -> None:
    target = _target(tmp_path)
    await target.provision_memory_system(TENANT)
    await _settle(target)
    assert target._records() == []
    assert target.mounted_ports() == []
    assert os.listdir(tmp_path) == [".memory"]
