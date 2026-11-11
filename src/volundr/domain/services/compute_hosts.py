"""Registered hosts: pairing, join, checks and operator actions.

An admin mints a single-use pairing code that fixes everything the host will be (its
runtime, connector, tags, slots). The host runs one command, which sends its own public key
and facts with the code. Forge then reaches the host through its connector, pinned to that
key, and audits it. Placement happens through the compute pool and the registered-host
provider; this service never moves an allocation's hold except to remove a host.

See ``docs/design/bm-compute-hosts.md``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import math
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import JsonValue

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import COMPUTE_HOST_JOIN_SCOPE, VALKYRIE_BUILD_TOKEN_USE
from niuu.observability import get_observability
from niuu.ports.workload_identity import WorkloadTokenIssuer, WorkloadTokenVerificationError
from volundr.domain.compute import (
    LIVE_SLOT_STATES,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    ComputeSlot,
)
from volundr.domain.compute_hosts import (
    EndpointKey,
    HostConflictError,
    HostConnector,
    HostHealth,
    HostInspector,
    HostPairingCode,
    HostPlatform,
    HostService,
    RegisteredHost,
    RegisteredHostRepository,
    normalize_tags,
)
from volundr.domain.services.compute_pool import ComputePoolService
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SlotLimits

logger = logging.getLogger(__name__)

#: What a joining host may call itself: a hostname label, as ``hostname -s`` prints it.
_HOST_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
#: The comment that marks Forge's key in a host's authorized_keys, so removal finds it.
FORGE_KEY_COMMENT = "niuu-forge"
#: The file the "download and read first" commands save the join script to.
JOIN_SCRIPT_FILENAME = "niuu-join.sh"
#: How the host list and refusals name a platform.
PLATFORM_LABELS = {HostPlatform.LINUX: "Linux", HostPlatform.DARWIN: "macOS"}
#: Why a Mac takes one session at a time: there is nothing to keep two apart.
MACOS_ONE_SLOT = (
    "macOS hosts run one session at a time: macOS has no network namespaces or cgroups, so "
    "there is no boundary between two sessions on one Mac"
)


class ComputeHostError(Exception):
    """A request about registered hosts that cannot be done as asked (HTTP 422)."""


class ComputeHostAccessError(ComputeHostError):
    """The principal may not do this (HTTP 403)."""


class ComputeHostStateError(ComputeHostError):
    """The host's state does not allow this now (HTTP 409)."""


class PairingCodeRejectedError(ComputeHostError):
    """The presented code is not a usable host pairing code (HTTP 401)."""


@dataclass(frozen=True)
class HostExecution:
    """One catalog execution a host can run, as the Add host form offers it."""

    execution: str
    profile: str
    runtime: SessionRuntimeKind | None
    label: str
    supports_slots: bool
    requires_root_daemon: bool
    slot_limits: SlotLimits | None
    forge_public_key: str
    ssh_user: str
    platforms: frozenset[HostPlatform]

    @property
    def refusal(self) -> str | None:
        if self.requires_root_daemon:
            return "Needs a root daemon (root Docker), so not on registered hosts"
        return None

    @property
    def exclusive_only(self) -> bool:
        return not self.supports_slots


@dataclass(frozen=True)
class MintedHostCode:
    code: str
    pairing: HostPairingCode
    command: str
    #: None when the join is pinned: wget cannot check a pin.
    command_wget: str | None
    command_download: str


@dataclass(frozen=True)
class JoinedHost:
    host: RegisteredHost
    forge_public_key: str
    ssh_user: str
    authorized_key_options: str
    setup_files: tuple[Mapping[str, str], ...]
    setup_commands: tuple[tuple[str, ...], ...]


def code_hash(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode()).hexdigest()


def key_fingerprint(public_key: str) -> str:
    """OpenSSH's SHA256 fingerprint of an ``ssh-ed25519 AAAA…`` key."""
    blob = base64.b64decode(public_key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def validate_host_public_key(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ComputeHostError("The host key must be an OpenSSH ssh-ed25519 public key")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ComputeHostError("The host key is not valid base64") from exc
    # An ssh-ed25519 blob is the length-prefixed type name and the 32-byte key.
    if len(blob) != 4 + len("ssh-ed25519") + 4 + 32:
        raise ComputeHostError("The host key is not an Ed25519 key")
    return f"{parts[0]} {parts[1]}"


def suggest_slots(
    facts: Mapping[str, JsonValue],
    limits: SlotLimits | None,
    *,
    reserved_cpu: float,
    reserved_memory_mib: int,
) -> tuple[int, tuple[str, ...]] | None:
    """Slots that fit the host's measured size, with the arithmetic an operator reads."""
    if limits is None:
        return None
    cpus, memory = facts.get("cpus"), facts.get("memory_mib")
    lines: list[str] = []
    fits: list[int] = []
    if limits.cpu is not None and isinstance(cpus, int):
        fit = math.floor((cpus - reserved_cpu) / limits.cpu)
        lines.append(f"CPU ({cpus} − {reserved_cpu:g}) ÷ {limits.cpu:g} = {fit}")
        fits.append(fit)
    if limits.memory_mib is not None and isinstance(memory, int):
        fit = (memory - reserved_memory_mib) // limits.memory_mib
        lines.append(
            f"memory ({memory} − {reserved_memory_mib}) MiB ÷ {limits.memory_mib} MiB = {fit}"
        )
        fits.append(fit)
    if not fits:
        return None
    return max(0, min(fits)), tuple(lines)


class ComputeHostService:
    def __init__(
        self,
        *,
        pool_id: str,
        repository: RegisteredHostRepository,
        compute_repository: ComputeLeaseRepository,
        compute_pool: ComputePoolService,
        connectors: Mapping[str, HostConnector],
        inspector: HostInspector,
        executions: Mapping[str, HostExecution],
        token_issuer: WorkloadTokenIssuer,
        join_url: str,
        join_pin: str | None,
        pairing_code_ttl_seconds: float,
        check_interval_seconds: float,
        pending_check_interval_seconds: float,
        reserved_cpu: float,
        reserved_memory_mib: int,
    ) -> None:
        if not executions:
            raise ValueError("Registered hosts need at least one catalog execution")
        self._pool_id = pool_id
        self._repository = repository
        self._compute = compute_repository
        self._pool = compute_pool
        self._connectors = connectors
        self._inspector = inspector
        self._executions = dict(executions)
        self._issuer = token_issuer
        self._join_url = join_url.rstrip("/")
        self._join_pin = join_pin
        self._ttl = pairing_code_ttl_seconds
        self._check_interval = check_interval_seconds
        self._pending_interval = pending_check_interval_seconds
        self._reserved_cpu = reserved_cpu
        self._reserved_memory = reserved_memory_mib
        self.last_error: str | None = None

    @property
    def pool_id(self) -> str:
        return self._pool_id

    @property
    def connectors(self) -> Mapping[str, HostConnector]:
        return self._connectors

    @property
    def executions(self) -> Mapping[str, HostExecution]:
        return self._executions

    @property
    def join_base_url(self) -> str:
        return self._join_url

    @property
    def join_pin(self) -> str | None:
        """The ``curl --pinnedpubkey`` pin of Forge's join listener, when it serves the join."""
        return self._join_pin

    def join_script_url(self) -> str:
        return f"{self._join_url}/api/v1/forge/compute/hosts/join.sh"

    # ── pairing ──────────────────────────────────────────────────────────────

    async def mint(
        self,
        principal: Principal,
        *,
        execution: str,
        connector: str,
        tags: list[str],
        slots: int | None,
    ) -> MintedHostCode:
        if not self.can_manage(principal):
            raise ComputeHostAccessError("Only admins may add hosts")
        chosen = self._execution(execution)
        if chosen.refusal is not None:
            raise ComputeHostError(f"{execution} cannot run on registered hosts: {chosen.refusal}")
        if connector not in self._connectors:
            raise ComputeHostError(
                f"Unknown connector {connector!r}; configured: {sorted(self._connectors)}"
            )
        self._require_slot_count(chosen, slots)
        try:
            normalized = normalize_tags(tags)
        except ValueError as exc:
            raise ComputeHostError(str(exc)) from exc
        issued = self._issuer.issue_token(
            # A code proves "this is a valid code", never "act as this admin".
            principal=Principal(
                user_id=principal.user_id,
                email=principal.email,
                tenant_id=principal.tenant_id,
                roles=[],
            ),
            workload_subject=principal.user_id,
            workload_name="forge-host-pairing",
            audiences=["volundr-api"],
            token_use=VALKYRIE_BUILD_TOKEN_USE,
            claims={"scopes": [COMPUTE_HOST_JOIN_SCOPE]},
        )
        expires_at = min(
            datetime.fromtimestamp(issued.expires_at, UTC),
            datetime.now(UTC) + timedelta(seconds=self._ttl),
        )
        pairing = HostPairingCode(
            id=uuid4(),
            code_hash=code_hash(issued.token),
            pool_id=self._pool_id,
            tenant_id=principal.tenant_id or "default",
            created_by=principal.user_id,
            execution=chosen.execution,
            profile=chosen.profile,
            connector=connector,
            tags=normalized,
            slots=slots,
            expires_at=expires_at,
        )
        await self._repository.create_pairing_code(pairing)
        header = shlex.quote(f"Authorization: Bearer {issued.token}")
        url = shlex.quote(self.join_script_url())
        if self._join_pin is not None:
            # The listener's certificate is self-signed: -k skips the CA, the pin is the
            # trust, and curl refuses any other key before it sends the code.
            pin = shlex.quote(self._join_pin)
            return MintedHostCode(
                code=issued.token,
                pairing=pairing,
                command=f"curl -fsSL -k --pinnedpubkey {pin} -H {header} {url} | sudo sh",
                command_wget=None,
                command_download=(
                    f"curl -fsSL -k --pinnedpubkey {pin} -o {JOIN_SCRIPT_FILENAME} "
                    f"-H {header} {url}"
                ),
            )
        return MintedHostCode(
            code=issued.token,
            pairing=pairing,
            command=f"curl -fsSL -H {header} {url} | sudo sh",
            command_wget=f"wget -qO- --header={header} {url} | sudo sh",
            command_download=f"curl -fsSL -o {JOIN_SCRIPT_FILENAME} -H {header} {url}",
        )

    async def pairing_codes(self) -> list[HostPairingCode]:
        return await self._repository.pairing_codes(self._pool_id)

    async def revoke(self, principal: Principal, code_id: UUID) -> None:
        self._require_admin(principal)
        if not await self._repository.revoke_pairing_code(self._pool_id, code_id):
            raise ComputeHostStateError("The pairing code is already used, revoked or expired")

    async def usable_code(self, raw_code: str) -> HostPairingCode:
        # Verified here too, not only at the edge: a mini-mode Forge has no Envoy to do it.
        try:
            self._issuer.verify_token(raw_code)
        except WorkloadTokenVerificationError as exc:
            raise PairingCodeRejectedError(
                "Pairing code is invalid, expired, revoked or used"
            ) from exc
        pairing = await self._repository.pairing_code(code_hash(raw_code))
        if pairing is None or pairing.pool_id != self._pool_id:
            raise PairingCodeRejectedError("Pairing code is invalid, expired, revoked or used")
        return pairing

    async def join(
        self,
        raw_code: str,
        *,
        name: str,
        public_key: str,
        address: str,
        facts: Mapping[str, JsonValue],
    ) -> JoinedHost:
        """Register the host the code was minted for. Nothing the host sends widens it."""
        if not _HOST_NAME.match(name):
            raise ComputeHostError("The host name must be a hostname label (letters, digits, -)")
        key = validate_host_public_key(public_key)
        pairing = await self.usable_code(raw_code)
        execution = self._execution(pairing.execution)
        connector = self._connector(pairing.connector)
        platform = _joining_platform(facts)
        label = PLATFORM_LABELS[platform]
        if platform not in execution.platforms:
            raise ComputeHostError(
                f"{execution.execution} does not run sessions on {label} hosts; mint a code "
                "for an execution whose runtime does"
            )
        if platform not in connector.platforms:
            raise ComputeHostError(
                f"Connector {pairing.connector!r} cannot set up {label} hosts; mint a code "
                "with another connector"
            )
        if platform is HostPlatform.DARWIN and pairing.slots not in (None, 1):
            raise ComputeHostError(
                f"{MACOS_ONE_SLOT}. This code was minted for {pairing.slots} slots: mint one "
                "for this Mac with slots 1, or with slots left open"
            )
        slots = _platform_slots(platform, pairing.slots)
        host = RegisteredHost(
            id=uuid4(),
            pool_id=self._pool_id,
            tenant_id=pairing.tenant_id,
            name=name,
            tags=pairing.tags,
            profile=pairing.profile,
            execution=pairing.execution,
            connector=pairing.connector,
            host_public_key=key,
            slots=slots,
            facts=_known_facts(facts),
            registered_by=pairing.created_by,
        )
        try:
            connection = await connector.admit(host, {"address": address})
        except ValueError as exc:
            raise ComputeHostError(str(exc)) from exc
        host = host.model_copy(update={"connection": connection})
        try:
            registered = await self._repository.consume_and_register(pairing.code_hash, host)
        except HostConflictError as exc:
            await connector.revoke(host)
            raise ComputeHostStateError(str(exc)) from exc
        if registered is None:
            await connector.revoke(host)
            raise PairingCodeRejectedError("Pairing code is invalid, expired, revoked or used")
        setup = connector.join_setup(registered)
        logger.info(
            "Host %s (%s) joined compute pool %s with %s",
            registered.name,
            key_fingerprint(key),
            self._pool_id,
            registered.execution,
        )
        return JoinedHost(
            host=registered,
            forge_public_key=execution.forge_public_key,
            ssh_user=execution.ssh_user,
            authorized_key_options=setup.authorized_key_options,
            setup_files=setup.files,
            setup_commands=setup.commands,
        )

    async def endpoint_key(self, *, user: str, key_type: str, key: str) -> EndpointKey | None:
        """The line a connect endpoint admits this key with, asked on every connection.

        Only a registered host of this pool whose connector admits it at its endpoint gets
        one; removing the host, or its connector revoking it, takes effect at once.
        """
        try:
            public_key = validate_host_public_key(f"{key_type} {key}")
        except ComputeHostError:
            return None
        host = await self._repository.with_public_key(public_key)
        if host is None or host.pool_id != self._pool_id:
            return None
        options = self._connector(host.connector).endpoint_key_options(host, user)
        if options is None:
            return None
        return EndpointKey(host_id=host.id, line=f"{options} {public_key}")

    # ── reads ────────────────────────────────────────────────────────────────

    async def hosts(self) -> list[RegisteredHost]:
        return await self._repository.list(self._pool_id)

    async def get(self, host_id: UUID) -> RegisteredHost:
        host = await self._repository.get(host_id)
        if host is None or host.pool_id != self._pool_id:
            raise LookupError("Registered host not found in this pool")
        return host

    async def live_sessions(self, host: RegisteredHost) -> list[ComputeSlot]:
        if host.allocation_id is None:
            return []
        return [
            binding
            for binding in await self._compute.slots(host.allocation_id)
            if binding.state in LIVE_SLOT_STATES
        ]

    def suggestion(self, host: RegisteredHost) -> tuple[int, tuple[str, ...]] | None:
        execution = self._executions.get(host.execution)
        return suggest_slots(
            host.facts,
            execution.slot_limits if execution is not None else None,
            reserved_cpu=self._reserved_cpu,
            reserved_memory_mib=self._reserved_memory,
        )

    # ── operator actions ─────────────────────────────────────────────────────

    async def update(
        self,
        principal: Principal,
        host_id: UUID,
        *,
        tags: list[str] | None = None,
        slots: int | None = None,
        name: str | None = None,
    ) -> RegisteredHost:
        self._require_admin(principal)
        host = await self.get(host_id)
        changes: dict = {}
        if tags is not None:
            try:
                changes["tags"] = normalize_tags(tags)
            except ValueError as exc:
                raise ComputeHostError(str(exc)) from exc
        if name is not None:
            if not _HOST_NAME.match(name):
                raise ComputeHostError("The host name must be a hostname label")
            changes["name"] = name
        if slots is not None and slots != host.slots:
            if host.allocation_id is not None:
                raise ComputeHostStateError(
                    "Drain the host before changing its slots: sessions are placed by them"
                )
            self._require_slot_count(self._execution(host.execution), slots)
            _platform_slots(host.platform, slots)
            self._require_capacity(host, slots)
            changes["slots"] = slots
        if not changes:
            return host
        updated = host.model_copy(update=changes)
        try:
            await self._repository.save(updated)
        except HostConflictError as exc:
            raise ComputeHostStateError(str(exc)) from exc
        return updated

    async def drain(self, principal: Principal, host_id: UUID, *, remove: bool) -> RegisteredHost:
        """No new sessions. A held host leaves service once its allocation empties and is
        audited; an unheld one leaves at once."""
        self._require_admin(principal)
        host = await self.get(host_id)
        if host.service is HostService.REMOVING:
            return host
        if host.allocation_id is None:
            if remove:
                await self._remove(host)
                return host.model_copy(update={"service": HostService.REMOVING})
            updated = host.model_copy(update={"service": HostService.OUT})
        else:
            service = HostService.REMOVING if remove else HostService.DRAINING
            updated = host.model_copy(update={"service": service})
        await self._repository.save(updated)
        return updated

    async def resume(self, principal: Principal, host_id: UUID) -> RegisteredHost:
        self._require_admin(principal)
        host = await self.get(host_id)
        if host.service is HostService.DRAINING:
            updated = host.model_copy(update={"service": HostService.ACTIVE})
            await self._repository.save(updated)
            return updated
        if host.service is not HostService.OUT:
            raise ComputeHostStateError("Only a draining or out-of-service host can resume")
        # Audited before anything is placed: a clean check makes it claimable again.
        updated = host.model_copy(update={"service": HostService.ACTIVE})
        await self._repository.save(updated)
        return await self.check(updated)

    async def reprepare(self, principal: Principal, host_id: UUID) -> RegisteredHost:
        """Run the current recipe again when the pool next claims this empty host.

        A host's preparation record is the allocation that holds it: the allocation pins
        the execution plan, carries the preparation result, and keys the checkpoint on the
        host. Disposing of that allocation through the pool invalidates all three, as the
        pool already does for an idle host whose plan changed. The next claim pins the
        current plan and ``ensure``s every recipe stage from a fresh checkpoint. The host
        is then checked, so a finding shows now rather than at the claim.
        """
        self._require_admin(principal)
        host = await self.get(host_id)
        if host.service is HostService.REMOVING:
            raise ComputeHostStateError("This host is being removed")
        if await self.live_sessions(host):
            raise ComputeHostStateError(
                "Sessions run on this host; drain it, and re-prepare once it is empty"
            )
        if host.allocation_id is not None:
            try:
                await self._pool.dispose(host.allocation_id)
            except (ValueError, ComputeLeaseBusyError) as exc:
                # A session was placed on the host since the check above.
                raise ComputeHostStateError(
                    "A session was placed on this host meanwhile; drain it, and re-prepare "
                    "once it is empty"
                ) from exc
            except (RuntimeError, OSError) as exc:
                # The allocation stays draining; the pool retries ending it, loudly.
                raise ComputeHostStateError(
                    f"Forge could not end the host's current preparation: {_reason(exc)}. "
                    "The pool retries; Check now shows what the host reports"
                ) from exc
            host = await self.get(host_id)
        logger.info("Host %s in compute pool %s will be re-prepared", host.name, self._pool_id)
        return await self.check(host)

    async def remove(self, principal: Principal, host_id: UUID) -> bool:
        """Remove a host no session runs on. True when Forge also removed its key from the
        host; False when the host could not be reached and must run niuu-host-leave."""
        self._require_admin(principal)
        host = await self.get(host_id)
        if await self.live_sessions(host):
            raise ComputeHostStateError(
                "Sessions still run on this host; drain it with remove, or stop them first"
            )
        return await self._remove(host)

    async def _remove(self, host: RegisteredHost) -> bool:
        if host.allocation_id is not None:
            # The allocation holds no session; ending its hold lets the pool release it, and
            # its next deletion finds no host and completes.
            await self._repository.release(
                host.allocation_id, host.model_copy(update={"service": HostService.REMOVING})
            )
        cleaned = await self._remove_forge_key(host)
        await self._connector(host.connector).revoke(host)
        await self._repository.delete(host.id)
        logger.info("Host %s removed from compute pool %s", host.name, self._pool_id)
        return cleaned

    async def _remove_forge_key(self, host: RegisteredHost) -> bool:
        try:
            await self._inspector.leave(host)
        except (RuntimeError, OSError) as exc:
            # Reported to the operator, who runs niuu-host-leave on the host; the host is
            # removed from Forge either way, and its key is no longer admitted anywhere.
            logger.error(
                "Could not remove Forge's key from host %s (%s): %s; run niuu-host-leave there",
                host.name,
                host.id,
                type(exc).__name__,
            )
            get_observability().count(
                "volundr.compute.host.leave_failures", attributes={"pool": self._pool_id}
            )
            return False
        return True

    # ── checks ───────────────────────────────────────────────────────────────

    async def _follow(self, host: RegisteredHost) -> RegisteredHost:
        """Move a host onto the revision that replaced the one it joined with.

        A host runs one execution revision. The built-in catalog's revision follows its
        content, so a Forge release that changes the execution retires the revision every
        host joined with and offers its successor under the same id. The host follows it:
        its allocation still pins the old plan, and the pool re-prepares an idle host whose
        plan changed. With no successor, or more than one, the host stays as it is and its
        check quarantines it.
        """
        if host.execution in self._executions:
            return host
        execution_id = host.execution.partition("@")[0]
        successors = [name for name in self._executions if name.partition("@")[0] == execution_id]
        if len(successors) != 1:
            return host
        [successor] = successors
        profile = self._executions[successor].profile
        await self._repository.follow_execution(host.id, execution=successor, profile=profile)
        logger.warning(
            "Host %s (%s) now runs %s: its execution %s is no longer offered",
            host.name,
            host.id,
            successor,
            host.execution,
        )
        get_observability().count(
            "volundr.compute.host.execution_followed", attributes={"pool": self._pool_id}
        )
        return host.model_copy(update={"execution": successor, "profile": profile})

    async def check(self, host: RegisteredHost) -> RegisteredHost:
        """Reach the host, read its facts and audit it; the result is its health."""
        now = datetime.now(UTC)
        if host.execution not in self._executions:
            # Forge has no way to reach or audit it: the access is the execution's.
            return await self._record(
                host,
                host.facts,
                (
                    f"its execution {host.execution} is no longer offered "
                    f"({', '.join(sorted(self._executions))}); remove the host and join "
                    "it again",
                ),
                HostHealth.QUARANTINED,
                now,
            )
        live = await self.live_sessions(host)
        try:
            platform = host.platform
        except ValueError as exc:
            # Forge cannot tell which checks the host needs; nothing is placed on it.
            return await self._record(host, host.facts, (str(exc),), HostHealth.QUARANTINED, now)
        try:
            facts = await self._inspector.facts(host)
            # Before the audit, whose disk floor then sees what the prune freed. Reached
            # already (facts were read): a prune that fails is a finding, not unreachable.
            try:
                await self._inspector.prune(host)
                pruned: tuple[str, ...] = ()
            except (RuntimeError, OSError) as exc:
                pruned = (f"Forge could not prune the host's image cache: {_reason(exc)}",)
            findings = await self._inspector.audit(
                host, live_slots=[binding.slot for binding in live], clean=False
            )
            findings = (*pruned, *findings)
            observed = facts.get("platform")
            if observed != platform.value:
                findings = (
                    *findings,
                    f"the host now reports platform {observed!r}, but it joined as "
                    f"{platform.value}; remove it and join it again",
                )
            # The platform stays what the host joined as; it picks the checks.
            facts = {**facts, "platform": platform.value}
            findings = (*findings, *self._capacity_findings(host, facts))
            health = HostHealth.QUARANTINED if findings else HostHealth.HEALTHY
        except (RuntimeError, OSError) as exc:
            # Unreachable, a key that does not match, or a check that could not run: the
            # failure is the check's result, recorded on the host and counted below.
            facts = host.facts
            findings = (f"Forge could not reach the host: {_reason(exc)}",)
            health = HostHealth.UNREACHABLE
        return await self._record(host, facts, findings, health, now)

    async def _record(
        self,
        host: RegisteredHost,
        facts: Mapping[str, JsonValue],
        findings: tuple[str, ...],
        health: HostHealth,
        now: datetime,
    ) -> RegisteredHost:
        if health is not HostHealth.HEALTHY and health != host.health:
            get_observability().count(
                "volundr.compute.host.quarantined"
                if health is HostHealth.QUARANTINED
                else "volundr.compute.host.unreachable",
                attributes={"pool": self._pool_id},
            )
            logger.error(
                "Registered host %s (%s) is %s: %s",
                host.name,
                host.id,
                health.value,
                "; ".join(findings),
            )
        return await self._repository.record_check(
            host.id, facts=dict(facts), findings=findings, health=health, checked_at=now
        )

    async def check_now(self, principal: Principal, host_id: UUID) -> RegisteredHost:
        self._require_admin(principal)
        return await self.check(await self.get(host_id))

    async def maintain(self) -> None:
        """One pass: reach pending and due hosts, and finish removals that are now empty."""
        now = datetime.now(UTC)
        due: list[RegisteredHost] = []
        hosts = [await self._follow(host) for host in await self._repository.list(self._pool_id)]
        # A connector that knows its connection (a tunnel) reports a drop here, every tick;
        # the host is checked at once and goes unreachable instead of waiting for its audit.
        statuses = await asyncio.gather(
            *(self._connector(host.connector).status(host) for host in hosts)
        )
        for host, status in zip(hosts, statuses, strict=True):
            if host.service is HostService.REMOVING and host.allocation_id is None:
                await self._remove(host)
                continue
            interval = (
                self._pending_interval
                if host.health in {HostHealth.PENDING, HostHealth.UNREACHABLE}
                else self._check_interval
            )
            dropped = not status.connected and host.health in {
                HostHealth.HEALTHY,
                HostHealth.QUARANTINED,
            }
            if (
                dropped
                or host.last_checked_at is None
                or (now - host.last_checked_at).total_seconds() >= interval
            ):
                due.append(host)
        # Each check bounds itself (command_timeout_seconds); one slow host never holds
        # back the others.
        await asyncio.gather(*(self.check(host) for host in due))

    async def run(self) -> None:
        while True:
            try:
                await self.maintain()
                self.last_error = None
            except Exception:
                get_observability().count(
                    "volundr.compute.hosts.maintain_errors", attributes={"pool": self._pool_id}
                )
                self.last_error = "Host checks failed; see the Forge log"
                logger.exception("Registered-host maintenance failed for %s", self._pool_id)
            await asyncio.sleep(self._pending_interval)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _execution(self, execution: str) -> HostExecution:
        chosen = self._executions.get(execution)
        if chosen is None:
            raise ComputeHostError(
                f"Unknown execution {execution!r}; offered: {sorted(self._executions)}"
            )
        return chosen

    def _connector(self, binding_id: str) -> HostConnector:
        connector = self._connectors.get(binding_id)
        if connector is None:
            raise ComputeHostError(
                f"Connector {binding_id!r} is no longer configured; restore it in "
                "compute.hosts.connectors"
            )
        return connector

    @staticmethod
    def can_manage(principal: Principal) -> bool:
        """Whether the principal may change hosts: admins only. Reading the list is open."""
        return "volundr:admin" in principal.roles

    @classmethod
    def _require_admin(cls, principal: Principal) -> None:
        if not cls.can_manage(principal):
            raise ComputeHostAccessError("Only admins may manage hosts")

    @staticmethod
    def _require_slot_count(execution: HostExecution, slots: int | None) -> None:
        if slots is None:
            return
        if slots < 1:
            raise ComputeHostError("A host runs at least one session at once")
        if slots > 1 and execution.exclusive_only:
            raise ComputeHostError(
                f"{execution.execution} does not separate slots, so its hosts are exclusive: "
                "set slots to 1"
            )

    def _require_capacity(self, host: RegisteredHost, slots: int) -> None:
        findings = self._capacity_findings(host.model_copy(update={"slots": slots}), host.facts)
        if findings:
            raise ComputeHostError("; ".join(findings))

    def _capacity_findings(
        self, host: RegisteredHost, facts: Mapping[str, JsonValue]
    ) -> tuple[str, ...]:
        """A slot count the measured host cannot hold is a finding, with the arithmetic."""
        if host.slots is None:
            return ()
        if host.platform is HostPlatform.DARWIN and host.slots != 1:
            return (f"{MACOS_ONE_SLOT}; this host is set to {host.slots} slots: set it to 1",)
        execution = self._executions.get(host.execution)
        suggestion = suggest_slots(
            facts,
            execution.slot_limits if execution is not None else None,
            reserved_cpu=self._reserved_cpu,
            reserved_memory_mib=self._reserved_memory,
        )
        if suggestion is None or host.slots <= suggestion[0]:
            return ()
        return (
            f"{host.slots} slots do not fit this host: {'; '.join(suggestion[1])}; lower the "
            f"slots to {suggestion[0]} or the per-slot limits",
        )


def _known_facts(facts: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The facts the host list shows; anything else a host sends is dropped, not stored."""
    known = (
        "platform",
        "cpus",
        "memory_mib",
        "disk_free_mib",
        "kernel",
        "arch",
        "cgroup_v2",
        "gpus",
        "os",
    )
    return {key: facts[key] for key in known if key in facts}


def _joining_platform(facts: Mapping[str, JsonValue]) -> HostPlatform:
    value = facts.get("platform")
    try:
        return HostPlatform(value)
    except ValueError:
        raise ComputeHostError(
            f"The host reported platform {value!r}; only Linux and macOS hosts can join, with "
            "the join.sh this Forge serves"
        ) from None


def _platform_slots(platform: HostPlatform, slots: int | None) -> int | None:
    """The slots a host on ``platform`` runs: a Mac runs exactly one, never more."""
    if platform is not HostPlatform.DARWIN:
        return slots
    if slots is not None and slots != 1:
        raise ComputeHostError(f"{MACOS_ONE_SLOT}; set slots to 1, not {slots}")
    return 1


def _reason(exc: Exception) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
