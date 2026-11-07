"""One host execution for every platform: route each session to its host's runtime.

A bring-your-own-machine Forge has one pool and one execution, and both Linux machines
and Macs join it. Each platform needs its own runtime (``SshHostVmRuntime`` on Linux,
``SshDarwinHostRuntime`` on a Mac), so this runtime holds one child per platform and
hands every call for a claimed host to the child of the platform the host recorded at
join (``Machine.platform``).

A session's bootstrap is built before Forge knows which host it gets, so it is built
for every platform at once: each child's launch is kept under its platform
(``encode_platform_bootstraps``) and the child sees only its own
(``platform_bootstrap``). A child that refuses the session (a Mac cannot run a
container image natively, say) leaves its platform without a launch; the session then
fails on a host of that platform with the child's reason, and runs on the others.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import (
    AllocationRef,
    ComputeLease,
    HostPlatform,
    MachineBootstrap,
    SlotLease,
    encode_platform_bootstraps,
    platform_bootstrap,
    refused_platforms,
)
from volundr.domain.guest_credentials import GuestCredentialDelivery
from volundr.domain.models import Session, SessionSpec
from volundr.domain.session_runtimes import SessionGrant, SessionRuntimeKind
from volundr.domain.vm_runtime import (
    CredentialAwareVmRuntime,
    CredentialDeliveryVmRuntime,
    ForgeSessionVmRuntime,
    SlotLimits,
    StartupReportingVmRuntime,
    StartupStepReport,
    VmRuntime,
)

logger = logging.getLogger(__name__)


class PlatformHostRuntime(VmRuntime):
    """Hold one runtime per host platform and use the claimed host's.

    ``platforms`` maps a platform (``linux``, ``darwin``) to its runtime; the composition
    root builds each from its nested adapter config with the execution's guest access.
    Every child must run sessions on exactly its own platform, and the children must
    agree on what a launch offers (``session_runtime``, ``session_grants``).
    """

    def __init__(self, *, platforms: Mapping[str, VmRuntime]) -> None:
        if not platforms:
            raise ValueError("PlatformHostRuntime needs a runtime for at least one platform")
        children: dict[HostPlatform, VmRuntime] = {}
        for name, runtime in platforms.items():
            try:
                platform = HostPlatform(name)
            except ValueError as exc:
                known = ", ".join(sorted(p.value for p in HostPlatform))
                raise ValueError(f"Unknown host platform {name!r}; use one of {known}") from exc
            if not isinstance(runtime, VmRuntime):
                raise TypeError(f"The {platform} runtime must implement VmRuntime")
            if runtime.host_platforms != frozenset({platform}):
                raise ValueError(
                    f"{type(runtime).__name__} runs sessions on "
                    f"{', '.join(sorted(runtime.host_platforms)) or 'no platform'}, so it "
                    f"cannot be the {platform} runtime"
                )
            children[platform] = runtime
        kinds = {runtime.session_runtime for runtime in children.values()}
        if len(kinds) != 1:
            raise ValueError("Every platform's runtime must run sessions the same way")
        grants = {runtime.session_grants for runtime in children.values()}
        if len(grants) != 1:
            raise ValueError("Every platform's runtime must offer the same session grants")
        separating = [runtime for runtime in children.values() if runtime.supports_slots]
        if len({runtime.slot_limits for runtime in separating}) > 1:
            raise ValueError(
                "The platforms whose runtimes separate slots must set the same slot limits"
            )
        self._children = children

    # ── which child ──────────────────────────────────────────────────────────

    def _all(self) -> tuple[VmRuntime, ...]:
        return tuple(self._children.values())

    def for_platform(self, platform: HostPlatform) -> VmRuntime:
        try:
            return self._children[platform]
        except KeyError as exc:
            raise ValueError(
                f"This execution runs no sessions on {platform} hosts; add a {platform} "
                "runtime to its platforms"
            ) from exc

    def _child(self, lease: AllocationRef) -> VmRuntime:
        machine = lease.machine
        if machine is None or machine.platform is None:
            raise ValueError(
                f"Allocation {lease.id} has no host platform recorded, so no runtime can be "
                "chosen for it; its host must join again"
            )
        return self.for_platform(machine.platform)

    def _routed(self, lease: AllocationRef, bootstrap: MachineBootstrap):
        child = self._child(lease)
        platform = lease.machine.platform  # type: ignore[union-attr]
        refused = refused_platforms(bootstrap).get(platform)
        if refused is not None:
            raise ValueError(f"This session cannot run on this {platform} host: {refused}")
        return child, platform_bootstrap(bootstrap, platform)

    # ── what the execution offers ────────────────────────────────────────────

    @property
    def host_platforms(self) -> frozenset[HostPlatform]:
        return frozenset(self._children)

    @property
    def requires_root_daemon(self) -> bool:
        return any(runtime.requires_root_daemon for runtime in self._all())

    @property
    def supports_slots(self) -> bool:
        # Per host: a platform whose runtime keeps one session per machine is held to one
        # slot by the registered-host checks (a Mac, say).
        return any(runtime.supports_slots for runtime in self._all())

    @property
    def supports_reuse(self) -> bool:
        return all(runtime.supports_reuse for runtime in self._all())

    @property
    def slot_limits(self) -> SlotLimits | None:
        for runtime in self._all():
            if runtime.supports_slots:
                return runtime.slot_limits
        return next(iter(self._children.values())).slot_limits

    @property
    def session_runtime(self) -> SessionRuntimeKind | None:
        return next(iter(self._children.values())).session_runtime

    @property
    def session_grants(self) -> tuple[SessionGrant, ...]:
        return next(iter(self._children.values())).session_grants

    @property
    def delivers_forge_session_token(self) -> bool:
        return all(
            isinstance(runtime, ForgeSessionVmRuntime) and runtime.delivers_forge_session_token
            for runtime in self._all()
        )

    # ── collaborators every child shares ─────────────────────────────────────

    def stage_forge_session(self, session_id: UUID, credential: IssuedForgeSessionToken) -> None:
        for runtime in self._all():
            if isinstance(runtime, ForgeSessionVmRuntime):
                runtime.stage_forge_session(session_id, credential)

    def discard_forge_session(self, session_id: UUID) -> None:
        for runtime in self._all():
            if isinstance(runtime, ForgeSessionVmRuntime):
                runtime.discard_forge_session(session_id)

    def report_startup_to(self, report: StartupStepReport) -> None:
        for runtime in self._all():
            if isinstance(runtime, StartupReportingVmRuntime):
                runtime.report_startup_to(report)

    def configure_credential_delivery(self, delivery: GuestCredentialDelivery) -> None:
        for runtime in self._all():
            if not isinstance(runtime, CredentialDeliveryVmRuntime):
                raise TypeError(
                    f"The {runtime.host_platforms} runtime {type(runtime).__name__} cannot "
                    "deliver guest credentials"
                )
            runtime.configure_credential_delivery(delivery)

    def configure_credentials(self, credential_store: CredentialStorePort) -> None:
        for runtime in self._all():
            if isinstance(runtime, CredentialAwareVmRuntime):
                runtime.configure_credentials(credential_store)

    # ── bootstraps: built before a host is claimed, so for every platform ────

    def _per_platform(self, build) -> MachineBootstrap:
        built: dict[HostPlatform, MachineBootstrap] = {}
        refusals: dict[HostPlatform, str] = {}
        for platform, runtime in self._children.items():
            try:
                built[platform] = build(runtime)
            except ValueError as exc:
                # The session cannot run on this platform; a host of another one may take it.
                refusals[platform] = str(exc)
        if not built:
            reasons = "; ".join(f"{platform}: {reason}" for platform, reason in refusals.items())
            raise ValueError(f"No host platform can run this session ({reasons})")
        for platform, reason in refusals.items():
            logger.info("Sessions of this launch will not run on %s hosts: %s", platform, reason)
        return encode_platform_bootstraps(built, refusals)

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        return self._per_platform(lambda runtime: runtime.machine_bootstrap(defaults))

    def session_bootstrap(
        self, session: Session, spec: SessionSpec, machine: MachineBootstrap
    ) -> MachineBootstrap:
        return self._per_platform(
            lambda runtime: runtime.session_bootstrap(
                session, spec, platform_bootstrap(machine, _only(runtime))
            )
        )

    def bootstrap(
        self, session: Session, spec: SessionSpec, defaults: MachineBootstrap
    ) -> MachineBootstrap:
        return self._per_platform(lambda runtime: runtime.bootstrap(session, spec, defaults))

    # ── a claimed host: its platform's runtime ───────────────────────────────

    async def warm(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        child, routed = self._routed(lease, bootstrap)
        await child.warm(lease, routed)

    async def prepare(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        child, routed = self._routed(lease, bootstrap)
        await child.prepare(lease, routed)

    async def start(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        child, routed = self._routed(lease, bootstrap)
        await child.start(lease, routed)

    async def target(self, lease: SlotLease, bootstrap: MachineBootstrap) -> SessionProxyTarget:
        child, routed = self._routed(lease, bootstrap)
        return await child.target(lease, routed)

    async def ready(self, lease: SlotLease, bootstrap: MachineBootstrap) -> bool:
        child, routed = self._routed(lease, bootstrap)
        return await child.ready(lease, routed)

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        # A refused platform never started anything; its stop is the child's no-op stop.
        child = self._child(lease)
        await child.stop(lease, platform_bootstrap(bootstrap, lease.machine.platform))  # type: ignore[union-attr]

    async def recycle(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        child = self._child(lease)
        await child.recycle(lease, platform_bootstrap(bootstrap, lease.machine.platform))  # type: ignore[union-attr]

    async def close(self) -> None:
        for runtime in self._all():
            await runtime.close()


def _only(runtime: VmRuntime) -> HostPlatform:
    (platform,) = runtime.host_platforms
    return platform
