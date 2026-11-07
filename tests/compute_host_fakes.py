"""Explicit in-memory registered-host ports used only by tests."""

from __future__ import annotations

import base64
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.ports.workload_identity import (
    IssuedWorkloadToken,
    WorkloadTokenIssuer,
    WorkloadTokenVerificationError,
)
from volundr.domain.compute import Machine, MachineState
from volundr.domain.compute_hosts import (
    HostConflictError,
    HostConnectionStatus,
    HostConnector,
    HostHealth,
    HostInspector,
    HostJoinSetup,
    HostPairingCode,
    HostPlatform,
    RegisteredHost,
    RegisteredHostRepository,
    SshDial,
)
from volundr.domain.services.compute_hosts import HostExecution
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SlotLimits

POOL = "hosts"

#: A syntactically valid OpenSSH Ed25519 public key: type name + 32 key bytes.
_BLOB = (
    len(b"ssh-ed25519").to_bytes(4, "big")
    + b"ssh-ed25519"
    + (32).to_bytes(4, "big")
    + bytes(range(32))
)
HOST_KEY = "ssh-ed25519 " + base64.b64encode(_BLOB).decode()


def other_host_key(seed: int) -> str:
    blob = (
        len(b"ssh-ed25519").to_bytes(4, "big")
        + b"ssh-ed25519"
        + (32).to_bytes(4, "big")
        + bytes((seed + i) % 256 for i in range(32))
    )
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


def make_host(**changes) -> RegisteredHost:
    values = {
        "id": uuid4(),
        "pool_id": POOL,
        "tenant_id": "tenant",
        "name": "hrimfaxi",
        "profile": "bm",
        "execution": "host@1",
        "connector": "direct-ssh",
        "connection": {"address": "192.168.90.31", "port": 22},
        "host_public_key": HOST_KEY,
        "slots": 4,
        # (32 − 4 reserved) ÷ 4 CPUs and (131072 − 8192) ÷ 16384 MiB: 7 slots fit.
        "facts": {"platform": "linux", "cpus": 32, "memory_mib": 131072},
        "health": HostHealth.HEALTHY,
        "registered_by": "admin",
    }
    values.update(changes)
    return RegisteredHost(**values)


def make_execution(**changes) -> HostExecution:
    values = {
        "execution": "host@1",
        "profile": "bm",
        "runtime": SessionRuntimeKind.HOST,
        "label": "Host · Nix + rootless Podman",
        "supports_slots": True,
        "requires_root_daemon": False,
        "slot_limits": SlotLimits(uid_base=61000, cpu=4, memory_mib=16384, pids=4096),
        "forge_public_key": "ssh-ed25519 FORGEKEY forge",
        "ssh_user": "root",
        "platforms": frozenset({HostPlatform.LINUX}),
    }
    values.update(changes)
    return HostExecution(**values)


class HostRepository(RegisteredHostRepository):
    """In-memory inventory with the Postgres adapter's write separation."""

    def __init__(self, *hosts: RegisteredHost):
        self.hosts = {host.id: host for host in hosts}
        self.codes: dict[str, HostPairingCode] = {}

    async def create_pairing_code(self, code):
        self.codes[code.code_hash] = code

    def _usable(self, code):
        return (
            code.consumed_at is None
            and code.revoked_at is None
            and code.expires_at > datetime.now(UTC)
        )

    async def pairing_code(self, code_hash):
        code = self.codes.get(code_hash)
        return code if code is not None and self._usable(code) else None

    async def pairing_codes(self, pool_id):
        return [c for c in self.codes.values() if c.pool_id == pool_id and self._usable(c)]

    async def revoke_pairing_code(self, pool_id, code_id):
        for key, code in self.codes.items():
            if code.id == code_id and code.pool_id == pool_id and self._usable(code):
                self.codes[key] = code.model_copy(update={"revoked_at": datetime.now(UTC)})
                return True
        return False

    async def consume_and_register(self, code_hash, host):
        code = await self.pairing_code(code_hash)
        if code is None:
            return None
        for existing in self.hosts.values():
            taken_name = existing.pool_id == host.pool_id and existing.name == host.name
            if taken_name or existing.host_public_key == host.host_public_key:
                raise HostConflictError("taken")
        self.codes[code_hash] = code.model_copy(
            update={"consumed_at": datetime.now(UTC), "consumed_by_host_id": host.id}
        )
        self.hosts[host.id] = host
        return host

    async def get(self, host_id):
        return self.hosts.get(host_id)

    async def held_by(self, allocation_id):
        return next((h for h in self.hosts.values() if h.allocation_id == allocation_id), None)

    async def list(self, pool_id):
        return sorted(
            (h for h in self.hosts.values() if h.pool_id == pool_id), key=lambda h: h.name
        )

    async def save(self, host):
        if host.id not in self.hosts:
            raise LookupError(host.id)
        current = self.hosts[host.id]
        self.hosts[host.id] = current.model_copy(
            update={
                "name": host.name,
                "tags": host.tags,
                "slots": host.slots,
                "service": host.service,
            }
        )

    async def record_check(self, host_id, *, facts, findings, health, checked_at):
        updated = self.hosts[host_id].model_copy(
            update={
                "facts": facts,
                "findings": tuple(findings),
                "health": health,
                "last_checked_at": checked_at,
            }
        )
        self.hosts[host_id] = updated
        return updated

    async def claim(self, pool_id, allocation_id, profile, selector):
        held = await self.held_by(allocation_id)
        if held is not None:
            return held
        candidates = sorted(
            (
                h
                for h in self.hosts.values()
                if h.pool_id == pool_id
                and h.claimable
                and h.profile == profile
                and selector.accepts(h.tags)
            ),
            key=lambda h: (-(h.slots or 0), h.name),
        )
        if not candidates:
            return None
        claimed = candidates[0].model_copy(update={"allocation_id": allocation_id})
        self.hosts[claimed.id] = claimed
        return claimed

    async def release(self, allocation_id, update):
        held = await self.held_by(allocation_id)
        if held is not None:
            self.hosts[held.id] = update.model_copy(update={"allocation_id": None})

    async def delete(self, host_id):
        host = self.hosts.get(host_id)
        if host is None or host.allocation_id is not None:
            return False
        del self.hosts[host_id]
        return True


class Connector(HostConnector):
    def __init__(self, *, connected: bool = True, platforms=None):
        self.connected = connected
        self._platforms = platforms
        self.revoked: list[UUID] = []
        self.admitted: list[dict] = []
        self.closed = False

    @property
    def kind(self):
        return "fake"

    @property
    def label(self):
        return "Fake connector"

    def join_setup(self, host):
        return HostJoinSetup(kind="fake", authorized_key_options="restrict,port-forwarding")

    @property
    def platforms(self):
        return super().platforms if self._platforms is None else self._platforms

    async def admit(self, host, offered):
        if not offered.get("address"):
            raise ValueError("no address")
        self.admitted.append(dict(offered))
        return {"address": offered["address"], "port": 22}

    def dial(self, host):
        return SshDial(address=str(host.connection.get("address", "10.0.0.1")))

    async def status(self, host):
        return HostConnectionStatus(connected=self.connected, target="10.0.0.1:22")

    async def revoke(self, host):
        self.revoked.append(host.id)

    async def close(self):
        self.closed = True


class Inspector(HostInspector):
    def __init__(self, *, facts=None, findings=(), error: Exception | None = None):
        self.facts_result = (
            facts if facts is not None else {"platform": "linux", "cpus": 32, "memory_mib": 131072}
        )
        self.findings = tuple(findings)
        self.error = error
        self.leave_error: Exception | None = None
        self.audits: list[tuple[UUID, tuple[int, ...], bool]] = []
        self.left: list[UUID] = []

    async def facts(self, host):
        if self.error is not None:
            raise self.error
        return dict(self.facts_result)

    async def audit(self, host, *, live_slots, clean):
        if self.error is not None:
            raise self.error
        self.audits.append((host.id, tuple(live_slots), clean))
        return self.findings

    def machine(self, host, allocation_id=None):
        return Machine(
            allocation_id=allocation_id or host.id,
            resource_id=str(host.id),
            state=MachineState.RUNNING,
            addresses=("10.0.0.1",),
            host_public_key=host.host_public_key,
            slots=host.slots,
            tags=host.tags,
            accepting=host.accepting,
            phase=host.name,
        )

    async def leave(self, host):
        if self.leave_error is not None:
            raise self.leave_error
        self.left.append(host.id)


class Issuer(WorkloadTokenIssuer):
    def __init__(self, *, enabled: bool = True, ttl_seconds: int = 3600):
        self._enabled = enabled
        self._ttl = ttl_seconds
        self.issued: list[dict] = []
        self.tokens: set[str] = set()

    @property
    def enabled(self):
        return self._enabled

    def issue_token(self, **kwargs):
        self.issued.append(kwargs)
        token = f"code-{len(self.issued)}-{uuid4().hex}"
        self.tokens.add(token)
        return IssuedWorkloadToken(token=token, expires_at=int(time.time()) + self._ttl)

    def verify_token(self, token):
        if token not in self.tokens:
            raise WorkloadTokenVerificationError("not minted here")
        return {"scopes": ["compute_host_join"]}


class SlotLedger:
    """Only the ``slots`` read the host service needs of the lease ledger."""

    def __init__(self, bindings_by_allocation=None):
        self.bindings = bindings_by_allocation or {}

    async def slots(self, allocation_id):
        return list(self.bindings.get(allocation_id, []))


class HostPool:
    """Only the ``dispose`` the host service needs of the compute pool.

    Disposing of an idle allocation ends its hold, as the registered-host provider's clean
    does; ``error`` makes it fail as the pool would.
    """

    def __init__(self, repository=None):
        self.repository = repository
        self.disposed: list = []
        self.error: Exception | None = None

    async def dispose(self, allocation_id):
        self.disposed.append(allocation_id)
        if self.error is not None:
            raise self.error
        held = await self.repository.held_by(allocation_id)
        if held is not None:
            await self.repository.release(allocation_id, held)
