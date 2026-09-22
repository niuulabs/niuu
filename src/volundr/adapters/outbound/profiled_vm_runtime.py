"""Route VM guest lifecycle operations by machine profile.

The infrastructure provider owns machine profiles.  This adapter lets those
profiles select different guest runtimes while preserving one durable compute
pool and one Forge API/UI lifecycle.
"""

from __future__ import annotations

from collections.abc import Mapping

from niuu.ports.credentials import CredentialStorePort
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import ComputeLease, MachineBootstrap
from volundr.domain.models import Session, SessionSpec
from volundr.domain.vm_runtime import CredentialAwareVmRuntime, VmRuntime


class ProfiledVmRuntime(VmRuntime):
    def __init__(
        self,
        *,
        default: VmRuntime,
        profiles: Mapping[str, VmRuntime] | None = None,
        default_profile: str | None = None,
    ) -> None:
        if default_profile is not None and not default_profile.strip():
            raise ValueError("Profiled VM runtime default_profile must not be blank")
        if not isinstance(default, VmRuntime):
            raise TypeError("Profiled VM runtime default must implement VmRuntime")
        configured_profiles = dict(profiles or {})
        if any(not profile.strip() for profile in configured_profiles):
            raise ValueError("Profiled VM runtime profile names must not be blank")
        if any(not isinstance(runtime, VmRuntime) for runtime in configured_profiles.values()):
            raise TypeError("Profiled VM runtime profiles must implement VmRuntime")
        self._default = default
        self._profiles = configured_profiles
        self._default_profile = default_profile

    def _for_profile(self, profile: str) -> VmRuntime:
        return self._profiles.get(profile, self._default)

    def _for_session(self, session: Session) -> VmRuntime:
        profile = str(session.workload_config.get("compute_profile") or "").strip()
        if not profile and self._default_profile is not None:
            profile = self._default_profile
        return self._for_profile(profile)

    def configure_credentials(self, credential_store: CredentialStorePort) -> None:
        for runtime in (self._default, *self._profiles.values()):
            if isinstance(runtime, CredentialAwareVmRuntime):
                runtime.configure_credentials(credential_store)

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        runtime = (
            self._for_profile(self._default_profile)
            if self._default_profile is not None
            else self._default
        )
        return runtime.machine_bootstrap(defaults)

    def machine_bootstrap_for(self, profile: str, defaults: MachineBootstrap) -> MachineBootstrap:
        return self._for_profile(profile).machine_bootstrap(defaults)

    def session_bootstrap(
        self, session: Session, spec: SessionSpec, machine: MachineBootstrap
    ) -> MachineBootstrap:
        return self._for_session(session).session_bootstrap(session, spec, machine)

    def session_bootstrap_for(
        self,
        profile: str,
        session: Session,
        spec: SessionSpec,
        machine: MachineBootstrap,
    ) -> MachineBootstrap:
        return self._for_profile(profile).session_bootstrap(session, spec, machine)

    def bootstrap(
        self, session: Session, spec: SessionSpec, defaults: MachineBootstrap
    ) -> MachineBootstrap:
        return self._for_session(session).bootstrap(session, spec, defaults)

    def bootstrap_for(
        self,
        profile: str,
        session: Session,
        spec: SessionSpec,
        defaults: MachineBootstrap,
    ) -> MachineBootstrap:
        return self._for_profile(profile).bootstrap(session, spec, defaults)

    async def warm(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await self._for_profile(lease.profile).warm(lease, bootstrap)

    async def prepare(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await self._for_profile(lease.profile).prepare(lease, bootstrap)

    async def start(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await self._for_profile(lease.profile).start(lease, bootstrap)

    async def target(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> SessionProxyTarget:
        return await self._for_profile(lease.profile).target(lease, bootstrap)

    async def ready(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> bool:
        return await self._for_profile(lease.profile).ready(lease, bootstrap)

    async def stop(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await self._for_profile(lease.profile).stop(lease, bootstrap)

    async def close(self) -> None:
        seen: set[int] = set()
        for runtime in (self._default, *self._profiles.values()):
            if id(runtime) in seen:
                continue
            seen.add(id(runtime))
            await runtime.close()
