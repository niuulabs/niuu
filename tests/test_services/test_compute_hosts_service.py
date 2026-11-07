"""ComputeHostService: pairing, join, operator actions and checks."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import COMPUTE_HOST_JOIN_SCOPE, VALKYRIE_BUILD_TOKEN_USE
from tests.compute_host_fakes import (
    HOST_KEY,
    POOL,
    Connector,
    HostPool,
    HostRepository,
    Inspector,
    Issuer,
    SlotLedger,
    make_execution,
    make_host,
    other_host_key,
)
from volundr.domain.compute import ComputeSlot, SlotState
from volundr.domain.compute_hosts import HostHealth, HostService
from volundr.domain.services.compute_hosts import (
    ComputeHostAccessError,
    ComputeHostError,
    ComputeHostService,
    ComputeHostStateError,
    PairingCodeRejectedError,
    code_hash,
    key_fingerprint,
    suggest_slots,
    validate_host_public_key,
)
from volundr.domain.vm_runtime import SlotLimits

ADMIN = Principal(user_id="admin", email="a@x", tenant_id="tenant", roles=["volundr:admin"])
USER = Principal(user_id="user", email="u@x", tenant_id="tenant", roles=["volundr:developer"])


def _service(
    *hosts, issuer=None, inspector=None, connector=None, ledger=None, executions=None, pool=None
):
    repository = HostRepository(*hosts)
    if pool is not None:
        pool.repository = repository
    connector = connector or Connector()
    inspector = inspector or Inspector()
    service = ComputeHostService(
        pool_id=POOL,
        repository=repository,
        compute_repository=ledger or SlotLedger(),
        compute_pool=pool or HostPool(repository),
        connectors={"direct-ssh": connector},
        inspector=inspector,
        executions=executions
        if executions is not None
        else {
            "host@1": make_execution(),
            "docker@1": make_execution(
                execution="docker@1", requires_root_daemon=True, label="Container (Docker)"
            ),
            "openshell@1": make_execution(
                execution="openshell@1", supports_slots=False, slot_limits=None
            ),
        },
        token_issuer=issuer or Issuer(),
        join_url="https://forge.example",
        pairing_code_ttl_seconds=600,
        check_interval_seconds=300,
        pending_check_interval_seconds=5,
        reserved_cpu=4,
        reserved_memory_mib=8192,
    )
    return service, repository, connector, inspector


def _binding(allocation_id, slot=0, state=SlotState.BUSY):
    return ComputeSlot(
        id=uuid4(),
        allocation_id=allocation_id,
        slot=slot,
        pool_id=POOL,
        session_id=uuid4(),
        tenant_id="tenant",
        owner_id="owner",
        state=state,
    )


class TestHelpers:
    def test_public_key_is_validated_and_comment_dropped(self):
        assert validate_host_public_key(HOST_KEY + " root@host") == HOST_KEY

    @pytest.mark.parametrize(
        "key",
        [
            "ssh-rsa AAAA",
            "ssh-ed25519",
            "ssh-ed25519 not-base64!!",
            "ssh-ed25519 " + base64.b64encode(b"short").decode(),
        ],
    )
    def test_public_key_refusals(self, key):
        with pytest.raises(ComputeHostError):
            validate_host_public_key(key)

    def test_fingerprint_is_openssh_sha256(self):
        assert key_fingerprint(HOST_KEY).startswith("SHA256:")
        assert not key_fingerprint(HOST_KEY).endswith("=")

    def test_suggestion_takes_the_tighter_limit_and_shows_arithmetic(self):
        limits = SlotLimits(uid_base=61000, cpu=4, memory_mib=16384, pids=None)
        slots, lines = suggest_slots(
            {"cpus": 48, "memory_mib": 131072},
            limits,
            reserved_cpu=4,
            reserved_memory_mib=8192,
        )
        assert slots == 7  # memory: (131072 - 8192) // 16384
        assert lines == (
            "CPU (48 − 4) ÷ 4 = 11",
            "memory (131072 − 8192) MiB ÷ 16384 MiB = 7",
        )

    def test_no_suggestion_without_limits_or_facts(self):
        assert suggest_slots({}, None, reserved_cpu=0, reserved_memory_mib=0) is None
        limits = SlotLimits(uid_base=61000, cpu=None, memory_mib=None, pids=10)
        assert suggest_slots({"cpus": 8}, limits, reserved_cpu=0, reserved_memory_mib=0) is None

    def test_suggestion_never_goes_negative(self):
        limits = SlotLimits(uid_base=61000, cpu=8, memory_mib=None, pids=None)
        slots, _ = suggest_slots({"cpus": 4}, limits, reserved_cpu=6, reserved_memory_mib=0)
        assert slots == 0


class TestMint:
    async def test_mints_a_roleless_scoped_single_use_code(self):
        issuer = Issuer()
        service, repository, _, _ = _service(issuer=issuer)

        minted = await service.mint(
            ADMIN, execution="host@1", connector="direct-ssh", tags=["gpu", "gpu"], slots=None
        )

        [call] = issuer.issued
        assert call["principal"].roles == []
        assert call["token_use"] == VALKYRIE_BUILD_TOKEN_USE
        assert call["claims"] == {"scopes": [COMPUTE_HOST_JOIN_SCOPE]}
        stored = repository.codes[code_hash(minted.code)]
        assert stored.tags == ("gpu",)
        assert stored.profile == "bm"
        assert stored.expires_at <= datetime.now(UTC) + timedelta(seconds=600)
        assert minted.command.startswith("curl -fsSL -H ")
        assert "https://forge.example/api/v1/forge/compute/hosts/join.sh | sudo sh" in (
            minted.command
        )
        assert minted.code in minted.command
        assert minted.command_wget.startswith("wget -qO- --header=")
        assert await service.pairing_codes() == [stored]

    async def test_code_expiry_is_the_shorter_of_token_and_ttl(self):
        service, repository, _, _ = _service(issuer=Issuer(ttl_seconds=30))
        minted = await service.mint(
            ADMIN, execution="host@1", connector="direct-ssh", tags=[], slots=2
        )
        assert minted.pairing.expires_at <= datetime.now(UTC) + timedelta(seconds=31)

    async def test_only_admins_mint(self):
        service, *_ = _service()
        with pytest.raises(ComputeHostAccessError):
            await service.mint(USER, execution="host@1", connector="direct-ssh", tags=[], slots=1)

    async def test_refuses_without_workload_identity(self):
        service, *_ = _service(issuer=Issuer(enabled=False))
        with pytest.raises(ComputeHostError, match="workload identity is disabled"):
            await service.mint(ADMIN, execution="host@1", connector="direct-ssh", tags=[], slots=1)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"execution": "nope@1"}, "Unknown execution"),
            ({"execution": "docker@1"}, "root daemon"),
            ({"connector": "tunnel"}, "Unknown connector"),
            ({"slots": 0}, "at least one"),
            ({"execution": "openshell@1", "slots": 2}, "exclusive"),
            ({"tags": ["two words"]}, "whitespace"),
        ],
    )
    async def test_refusals(self, kwargs, message):
        service, *_ = _service()
        request = {"execution": "host@1", "connector": "direct-ssh", "tags": [], "slots": 2}
        request.update(kwargs)
        with pytest.raises(ComputeHostError, match=message):
            await service.mint(ADMIN, **request)

    async def test_revoke(self):
        service, repository, _, _ = _service()
        minted = await service.mint(
            ADMIN, execution="host@1", connector="direct-ssh", tags=[], slots=1
        )
        with pytest.raises(ComputeHostAccessError):
            await service.revoke(USER, minted.pairing.id)
        await service.revoke(ADMIN, minted.pairing.id)
        assert await service.pairing_codes() == []
        with pytest.raises(ComputeHostStateError):
            await service.revoke(ADMIN, minted.pairing.id)
        with pytest.raises(PairingCodeRejectedError):
            await service.usable_code(minted.code)


class TestJoin:
    async def _minted(self, service, **kwargs):
        request = {"execution": "host@1", "connector": "direct-ssh", "tags": ["gpu"], "slots": 2}
        request.update(kwargs)
        return await service.mint(ADMIN, **request)

    async def test_registers_what_the_code_was_minted_for(self):
        service, repository, connector, _ = _service()
        minted = await self._minted(service)

        joined = await service.join(
            minted.code,
            name="glad",
            public_key=HOST_KEY + " root@glad",
            address="192.168.90.35",
            facts={"cpus": 48, "memory_mib": 196608, "secret": "dropped"},
        )

        host = joined.host
        assert (host.name, host.tags, host.slots, host.execution) == ("glad", ("gpu",), 2, "host@1")
        assert host.health is HostHealth.PENDING
        assert host.connection == {"address": "192.168.90.35", "port": 22}
        assert host.facts == {"cpus": 48, "memory_mib": 196608}
        assert host.registered_by == "admin"
        assert joined.ssh_user == "root"
        assert joined.forge_public_key == "ssh-ed25519 FORGEKEY forge"
        assert joined.authorized_key_options == "restrict,port-forwarding"
        assert repository.hosts[host.id] == host
        with pytest.raises(PairingCodeRejectedError):
            await service.usable_code(minted.code)

    async def test_a_code_is_used_once(self):
        service, *_ = _service()
        minted = await self._minted(service)
        await service.join(minted.code, name="a", public_key=HOST_KEY, address="10.0.0.2", facts={})
        with pytest.raises(PairingCodeRejectedError):
            await service.join(
                minted.code, name="b", public_key=other_host_key(1), address="10.0.0.3", facts={}
            )

    async def test_conflict_keeps_the_code_and_revokes_the_connection(self):
        existing = make_host(name="glad")
        service, repository, connector, _ = _service(existing)
        minted = await self._minted(service)
        with pytest.raises(ComputeHostStateError):
            await service.join(
                minted.code, name="glad", public_key=other_host_key(3), address="10.0.0.4", facts={}
            )
        assert len(connector.revoked) == 1
        assert await service.usable_code(minted.code)

    @pytest.mark.parametrize(
        ("name", "key", "address", "error"),
        [
            ("bad name", HOST_KEY, "10.0.0.1", ComputeHostError),
            ("ok", "ssh-rsa AAAA", "10.0.0.1", ComputeHostError),
            ("ok", HOST_KEY, "", ComputeHostError),
        ],
    )
    async def test_refusals(self, name, key, address, error):
        service, *_ = _service()
        minted = await self._minted(service)
        with pytest.raises(error):
            await service.join(minted.code, name=name, public_key=key, address=address, facts={})

    async def test_unknown_code(self):
        service, *_ = _service()
        with pytest.raises(PairingCodeRejectedError):
            await service.join("forged", name="x", public_key=HOST_KEY, address="1.2.3.4", facts={})

    async def test_lost_race_revokes_the_connection(self):
        service, repository, connector, _ = _service()
        minted = await self._minted(service)

        async def consumed(*_):
            return None

        repository.consume_and_register = consumed
        with pytest.raises(PairingCodeRejectedError):
            await service.join(
                minted.code, name="x", public_key=HOST_KEY, address="1.2.3.4", facts={}
            )
        assert len(connector.revoked) == 1


class TestOperatorActions:
    async def test_tags_and_name_change_any_time(self):
        host = make_host(allocation_id=uuid4())
        service, repository, _, _ = _service(host)
        updated = await service.update(ADMIN, host.id, tags=["b", "a"], name="renamed")
        assert (updated.tags, updated.name) == (("a", "b"), "renamed")
        assert repository.hosts[host.id].allocation_id == host.allocation_id

    async def test_slots_change_only_out_of_service_and_within_capacity(self):
        held = make_host(allocation_id=uuid4())
        free = make_host(name="free", host_public_key=other_host_key(5))
        service, *_ = _service(held, free)
        with pytest.raises(ComputeHostStateError, match="Drain"):
            await service.update(ADMIN, held.id, slots=2)
        with pytest.raises(ComputeHostError, match="do not fit"):
            await service.update(ADMIN, free.id, slots=8)  # 7 fit
        assert (await service.update(ADMIN, free.id, slots=7)).slots == 7

    async def test_update_refusals(self):
        host = make_host()
        service, *_ = _service(host)
        with pytest.raises(ComputeHostAccessError):
            await service.update(USER, host.id, tags=["a"])
        with pytest.raises(ComputeHostError):
            await service.update(ADMIN, host.id, tags=[""])
        with pytest.raises(ComputeHostError):
            await service.update(ADMIN, host.id, name="no spaces")
        with pytest.raises(LookupError):
            await service.update(ADMIN, uuid4(), tags=[])
        assert await service.update(ADMIN, host.id) == host

    async def test_drain_an_unheld_host_takes_it_out_at_once(self):
        host = make_host()
        service, repository, _, _ = _service(host)
        drained = await service.drain(ADMIN, host.id, remove=False)
        assert drained.service is HostService.OUT
        resumed = await service.resume(ADMIN, host.id)
        assert (resumed.service, resumed.health) == (HostService.ACTIVE, HostHealth.HEALTHY)

    async def test_drain_a_held_host_waits_for_its_allocation(self):
        host = make_host(allocation_id=uuid4())
        service, repository, _, _ = _service(host)
        assert (await service.drain(ADMIN, host.id, remove=False)).service is HostService.DRAINING
        assert (await service.resume(ADMIN, host.id)).service is HostService.ACTIVE
        assert (await service.drain(ADMIN, host.id, remove=True)).service is HostService.REMOVING
        assert (await service.drain(ADMIN, host.id, remove=False)).service is HostService.REMOVING
        with pytest.raises(ComputeHostStateError):
            await service.resume(ADMIN, host.id)

    async def test_drain_and_remove_an_unheld_host_removes_it(self):
        host = make_host()
        service, repository, connector, inspector = _service(host)
        await service.drain(ADMIN, host.id, remove=True)
        assert host.id not in repository.hosts
        assert inspector.left == [host.id]
        assert connector.revoked == [host.id]

    async def test_remove_refuses_while_sessions_run(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        ledger = SlotLedger({allocation: [_binding(allocation)]})
        service, *_ = _service(host, ledger=ledger)
        with pytest.raises(ComputeHostStateError, match="Sessions still run"):
            await service.remove(ADMIN, host.id)

    async def test_remove_an_idle_held_host_ends_the_hold(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        ledger = SlotLedger({allocation: [_binding(allocation, state=SlotState.RELEASED)]})
        service, repository, _, _ = _service(host, ledger=ledger)
        assert await service.remove(ADMIN, host.id) is True
        assert repository.hosts == {}

    async def test_remove_an_unreachable_host_says_to_run_leave(self):
        host = make_host()
        inspector = Inspector()
        inspector.leave_error = RuntimeError("ssh 255")
        service, repository, _, _ = _service(host, inspector=inspector)
        with patch("volundr.domain.services.compute_hosts.get_observability") as observe:
            assert await service.remove(ADMIN, host.id) is False
        observe.return_value.count.assert_called_once()
        assert repository.hosts == {}

    async def test_reprepare_ends_the_idle_hold_then_checks(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation, health=HostHealth.QUARANTINED)
        ledger = SlotLedger({allocation: [_binding(allocation, state=SlotState.RELEASED)]})
        pool = HostPool()
        service, repository, _, inspector = _service(host, ledger=ledger, pool=pool)
        reprepared = await service.reprepare(ADMIN, host.id)
        assert pool.disposed == [allocation]
        assert reprepared.allocation_id is None
        assert (reprepared.service, reprepared.health) == (HostService.ACTIVE, HostHealth.HEALTHY)
        assert inspector.audits == [(host.id, (), False)]

    async def test_reprepare_an_unheld_host_only_checks_it(self):
        host = make_host(service=HostService.OUT)
        pool = HostPool()
        service, _, _, inspector = _service(host, pool=pool)
        reprepared = await service.reprepare(ADMIN, host.id)
        assert pool.disposed == []
        assert reprepared.service is HostService.OUT
        assert len(inspector.audits) == 1

    async def test_reprepare_refuses_while_sessions_run(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        ledger = SlotLedger({allocation: [_binding(allocation)]})
        pool = HostPool()
        service, *_ = _service(host, ledger=ledger, pool=pool)
        with pytest.raises(ComputeHostStateError, match="drain it"):
            await service.reprepare(ADMIN, host.id)
        assert pool.disposed == []

    async def test_reprepare_refuses_a_host_being_removed(self):
        host = make_host(allocation_id=uuid4(), service=HostService.REMOVING)
        service, *_ = _service(host)
        with pytest.raises(ComputeHostStateError, match="being removed"):
            await service.reprepare(ADMIN, host.id)

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (ValueError("Stop the session"), "placed on this host meanwhile"),
            (RuntimeError("ssh 255"), "could not end the host's current preparation: Runtime"),
        ],
    )
    async def test_reprepare_reports_a_disposal_that_fails(self, error, reason):
        host = make_host(allocation_id=uuid4())
        pool = HostPool()
        pool.error = error
        service, *_ = _service(host, pool=pool)
        with pytest.raises(ComputeHostStateError, match=reason):
            await service.reprepare(ADMIN, host.id)

    async def test_admin_only(self):
        host = make_host()
        service, *_ = _service(host)
        for call in (
            service.drain(USER, host.id, remove=False),
            service.resume(USER, host.id),
            service.reprepare(USER, host.id),
            service.remove(USER, host.id),
            service.check_now(USER, host.id),
        ):
            with pytest.raises(ComputeHostAccessError):
                await call


class TestChecks:
    async def test_clean_check_is_healthy_and_records_facts(self):
        host = make_host(health=HostHealth.PENDING)
        service, repository, _, inspector = _service(host)
        checked = await service.check_now(ADMIN, host.id)
        assert checked.health is HostHealth.HEALTHY
        assert checked.findings == ()
        assert checked.last_checked_at is not None
        assert inspector.audits == [(host.id, (), False)]

    async def test_live_slots_are_audited_around(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        ledger = SlotLedger({allocation: [_binding(allocation, 1), _binding(allocation, 3)]})
        service, _, _, inspector = _service(host, ledger=ledger)
        await service.check(host)
        assert inspector.audits[0][1] == (1, 3)

    async def test_findings_quarantine_loudly(self, caplog):
        host = make_host()
        service, *_ = _service(
            host, inspector=Inspector(findings=["user niuu-slot-4 still exists"])
        )
        with patch("volundr.domain.services.compute_hosts.get_observability") as observe:
            checked = await service.check(host)
        assert checked.health is HostHealth.QUARANTINED
        assert checked.findings == ("user niuu-slot-4 still exists",)
        observe.return_value.count.assert_called_once_with(
            "volundr.compute.host.quarantined", attributes={"pool": POOL}
        )
        assert "quarantined" in caplog.text

    async def test_too_many_slots_for_the_measured_host_is_a_finding(self):
        host = make_host(slots=8)  # 7 fit
        service, *_ = _service(host)
        checked = await service.check(host)
        assert checked.health is HostHealth.QUARANTINED
        assert "8 slots do not fit this host" in checked.findings[0]

    async def test_unreachable_host(self):
        host = make_host()
        service, *_ = _service(host, inspector=Inspector(error=OSError("refused")))
        with patch("volundr.domain.services.compute_hosts.get_observability") as observe:
            checked = await service.check(host)
        assert checked.health is HostHealth.UNREACHABLE
        assert checked.findings == ("Forge could not reach the host: OSError: refused",)
        assert checked.facts == host.facts
        observe.return_value.count.assert_called_once_with(
            "volundr.compute.host.unreachable", attributes={"pool": POOL}
        )

    async def test_a_repeated_state_is_not_counted_again(self):
        host = make_host(health=HostHealth.UNREACHABLE)
        service, *_ = _service(host, inspector=Inspector(error=RuntimeError()))
        with patch("volundr.domain.services.compute_hosts.get_observability") as observe:
            checked = await service.check(host)
        assert checked.findings == ("Forge could not reach the host: RuntimeError",)
        observe.return_value.count.assert_not_called()

    async def test_maintain_checks_due_hosts_and_finishes_removals(self):
        now = datetime.now(UTC)
        pending = make_host(
            name="pending", health=HostHealth.PENDING, host_public_key=other_host_key(1)
        )
        fresh = make_host(name="fresh", last_checked_at=now, host_public_key=other_host_key(2))
        stale = make_host(
            name="stale",
            last_checked_at=now - timedelta(hours=1),
            host_public_key=other_host_key(3),
        )
        removing = make_host(
            name="removing", service=HostService.REMOVING, host_public_key=other_host_key(4)
        )
        service, repository, connector, inspector = _service(pending, fresh, stale, removing)

        await service.maintain()

        assert sorted(audit[0] for audit in inspector.audits) == sorted([pending.id, stale.id])
        assert removing.id not in repository.hosts
        assert connector.revoked == [removing.id]

    async def test_run_records_and_counts_a_failed_pass(self):
        service, repository, *_ = _service()

        async def broken(_pool):
            raise ValueError("database down")

        repository.list = broken
        sleeps = 0

        async def stop_after_one(_seconds):
            nonlocal sleeps
            sleeps += 1
            raise SystemExit

        with (
            patch("volundr.domain.services.compute_hosts.asyncio.sleep", stop_after_one),
            patch("volundr.domain.services.compute_hosts.get_observability") as observe,
            pytest.raises(SystemExit),
        ):
            await service.run()
        assert service.last_error == "Host checks failed; see the Forge log"
        observe.return_value.count.assert_called_once()

    async def test_suggestion_for_a_host(self):
        host = make_host(facts={"cpus": 20, "memory_mib": 40960})
        service, *_ = _service(host)
        assert service.suggestion(host)[0] == 2  # memory (40960 − 8192) // 16384
        assert service.suggestion(make_host(execution="gone@1")) is None

    async def test_service_needs_an_execution(self):
        with pytest.raises(ValueError):
            _service(executions={})

    async def test_unknown_connector_on_removal_is_loud(self):
        host = make_host(connector="gone")
        service, *_ = _service(host)
        with pytest.raises(ComputeHostError, match="no longer configured"):
            await service.remove(ADMIN, host.id)
