"""Deliver a VM session's ServiceAccount tokens and Forge session credential.

Selected per backend through ``compute.credential_delivery`` (dynamic adapter):

* ``SshPushCredentialDelivery`` — the controller issues the tokens and writes them
  over the runtime's pinned SSH channel, refreshing them before they expire, as a
  kubelet does for a projected volume. For guests that cannot reach Forge.
* ``MetadataCredentialDelivery`` — a guest agent pulls them from Forge's metadata
  endpoint, authenticated by the configured ``GuestIdentity``, writes the same
  files, and serves them on the guest's link-local metadata address.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.observability import get_observability
from volundr.domain.compute import MachineBootstrap, SlotLease
from volundr.domain.guest_credentials import (
    METADATA_ADDRESS,
    GuestContainers,
    GuestCredentialDelivery,
    GuestCredentialPlan,
    GuestFiles,
    GuestIdentity,
)
from volundr.domain.service_account_tokens import ServiceAccountTokenPort
from volundr.domain.vm_runtime import VmRuntimeStageError

logger = logging.getLogger(__name__)

#: Guest directory holding the agent's configuration (no credentials).
AGENT_CONFIG_DIRECTORY = "/etc/niuu/guest-agent"
AGENT_CONFIG_FILE = "config.json"


class SshPushCredentialDelivery(GuestCredentialDelivery):
    """Issue and write credentials from the controller over the pinned SSH channel."""

    def __init__(
        self,
        *,
        service_account_tokens: ServiceAccountTokenPort,
        refresh_fraction: float = 0.8,
        retry_seconds: float = 10,
        **_extra: object,
    ) -> None:
        if not isinstance(service_account_tokens, ServiceAccountTokenPort):
            raise ValueError(
                "SshPushCredentialDelivery needs compute.service_account_tokens configured"
            )
        if not 0 < refresh_fraction < 1 or retry_seconds <= 0:
            raise ValueError(
                "refresh_fraction must lie between 0 and 1 and retry_seconds must be positive"
            )
        self._tokens = service_account_tokens
        self._refresh_fraction = refresh_fraction
        self._retry = retry_seconds
        # Lease id -> the task keeping that guest's tokens fresh.
        self._refreshers: dict[str, asyncio.Task] = {}
        # Session id -> its current launch credential. Memory only: every start
        # mints a new one and revokes the previous, so a stored copy is stale.
        self._forge_sessions: dict[UUID, IssuedForgeSessionToken] = {}

    def stage_forge_session(self, session_id: UUID, credential: IssuedForgeSessionToken) -> None:
        self._forge_sessions[session_id] = credential

    def discard_forge_session(self, session_id: UUID) -> None:
        self._forge_sessions.pop(session_id, None)

    async def _deliver_tokens(
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        files: GuestFiles,
    ) -> datetime | None:
        """Issue every planned token, land it in the guest, return when to refresh."""
        items, refresh_at = [], None
        for volume in plan.token_volumes:
            for token in volume.tokens:
                issued_at = datetime.now(UTC)
                issued = await self._tokens.issue(
                    plan.service_account,
                    audience=token.audience,
                    expiration_seconds=token.expiration_seconds,
                )
                due = issued_at + (issued.expires_at - issued_at) * self._refresh_fraction
                refresh_at = due if refresh_at is None else min(refresh_at, due)
                items.append(
                    {"directory": volume.directory, "name": token.path, "content": issued.token}
                )
        if items:
            await files.write_files(lease, bootstrap, items)
        return refresh_at

    async def _ensure_refresh(
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        files: GuestFiles,
    ) -> None:
        key = str(lease.id)
        running = self._refreshers.get(key)
        if running is not None and not running.done():
            return
        refresh_at = await self._deliver_tokens(lease, bootstrap, plan, files)
        if refresh_at is None:
            return
        self._refreshers[key] = asyncio.create_task(
            self._refresh(lease, bootstrap, plan, files, refresh_at)
        )

    async def _refresh(
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        files: GuestFiles,
        refresh_at: datetime,
    ) -> None:
        """Replace the guest's tokens before they expire.

        A failed refresh is retried until it succeeds; every failure is an error
        and a counted event, because the session loses its identity at expiry.
        """
        while True:
            await asyncio.sleep(max(0.0, (refresh_at - datetime.now(UTC)).total_seconds()))
            try:
                refresh_at = await self._deliver_tokens(lease, bootstrap, plan, files)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                get_observability().count(
                    "volundr.compute.token_refresh.errors",
                    attributes={"service_account": plan.service_account},
                )
                logger.error(
                    "Refreshing ServiceAccount %s tokens for VM allocation %s failed (%s); "
                    "retrying in %gs",
                    plan.service_account,
                    lease.id,
                    type(exc).__name__,
                    self._retry,
                )
                refresh_at = datetime.now(UTC) + timedelta(seconds=self._retry)

    async def start(
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        launch: dict,
        files: GuestFiles,
    ) -> None:
        await self._ensure_refresh(lease, bootstrap, plan, files)
        if not plan.forge_session:
            return
        path = f"{plan.forge_session_directory}/{plan.forge_session_file}"
        credential = self._forge_sessions.get(lease.session_id)
        if credential is not None:
            await files.write_files(
                lease,
                bootstrap,
                [
                    {
                        "directory": plan.forge_session_directory,
                        "name": plan.forge_session_file,
                        "content": credential.token,
                    }
                ],
            )
            launch["forge_session_grants"] = [grant.value for grant in credential.grants]
            launch["relaunch"] = True
            return
        # A restarted controller replays a start it no longer holds the credential
        # for: the guest must already have it, or this launch cannot start.
        if not await files.file_exists(lease, bootstrap, path):
            raise VmRuntimeStageError(
                "forge-session",
                "the controller restarted before delivering this launch's Forge session "
                "credential; restart the session",
            )

    async def resume(
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        files: GuestFiles,
    ) -> None:
        # After a controller restart the first route to a running guest resumes
        # its refresh, delivering fresh tokens before it returns.
        await self._ensure_refresh(lease, bootstrap, plan, files)

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        task = self._refreshers.pop(str(lease.id), None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        # Guests keep their current tokens; the next controller resumes refreshing.
        # The injected token issuer belongs to the composition root.
        for key in list(self._refreshers):
            task = self._refreshers.pop(key)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class MetadataCredentialDelivery(GuestCredentialDelivery):
    """Run a guest agent that pulls credentials from Forge's metadata endpoint."""

    def __init__(
        self,
        *,
        metadata_url: str,
        guest_identity: GuestIdentity,
        agent_command: list[str] | tuple[str, ...] = ("python", "-m", "volundr.guest_agent"),
        refresh_fraction: float = 0.8,
        retry_seconds: float = 10,
        request_timeout_seconds: float = 30,
        ca_file: str = "",
        listen_port: int = 80,
        **_extra: object,
    ) -> None:
        if not metadata_url.startswith("https://"):
            raise ValueError(
                "MetadataCredentialDelivery needs an https metadata_url guests can reach"
            )
        if not isinstance(guest_identity, GuestIdentity):
            raise ValueError("MetadataCredentialDelivery needs compute.guest_identity configured")
        if not agent_command or any(not str(part) for part in agent_command):
            raise ValueError("agent_command must name the guest agent program")
        if not 0 < refresh_fraction < 1 or min(retry_seconds, request_timeout_seconds) <= 0:
            raise ValueError(
                "refresh_fraction must lie between 0 and 1; retry_seconds and "
                "request_timeout_seconds must be positive"
            )
        if not 0 < listen_port < 65536:
            raise ValueError("listen_port must be a TCP port")
        if ca_file and not ca_file.startswith("/"):
            raise ValueError("ca_file must be an absolute guest path")
        self._url = metadata_url.rstrip("/")
        self._identity = guest_identity
        self._command = [str(part) for part in agent_command]
        self._refresh_fraction = refresh_fraction
        self._retry = retry_seconds
        self._timeout = request_timeout_seconds
        self._ca_file = ca_file
        self._port = listen_port

    @property
    def identity(self) -> GuestIdentity:
        return self._identity

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        return self._identity.machine_bootstrap(defaults)

    def _mounts(self, plan: GuestCredentialPlan) -> list[dict]:
        mounts = [
            {"source": AGENT_CONFIG_DIRECTORY, "target": AGENT_CONFIG_DIRECTORY, "readonly": True},
            *(
                {"source": volume.directory, "target": volume.directory, "readonly": False}
                for volume in plan.token_volumes
            ),
            *(dict(mount) for mount in self._identity.agent_mounts()),
        ]
        if plan.forge_session:
            mounts.append(
                {
                    "source": plan.forge_session_directory,
                    "target": plan.forge_session_directory,
                    "readonly": False,
                }
            )
        if self._ca_file:
            mounts.append({"source": self._ca_file, "target": self._ca_file, "readonly": True})
        return mounts

    def session_containers(self, plan: GuestCredentialPlan, *, image: str) -> GuestContainers:
        if plan.empty:
            return GuestContainers()
        config = f"{AGENT_CONFIG_DIRECTORY}/{AGENT_CONFIG_FILE}"

        def agent(name: str, mode: str) -> dict:
            return {
                "name": name,
                "image": image,
                "command": [*self._command, mode, "--config", config],
                "args": [],
                "environment": {},
                # Root: it reads the guest identity key and binds the metadata address.
                "user": "0",
                "mounts": self._mounts(plan),
            }

        return GuestContainers(
            # Runs first, so OpenBao's agent finds its ServiceAccount token.
            init_containers=(agent("niuu-guest-agent-init", "once"),),
            sidecars=(agent("niuu-guest-agent", "serve"),),
            directories=tuple(
                [volume.directory for volume in plan.token_volumes]
                + ([plan.forge_session_directory] if plan.forge_session else [])
            ),
            launch={"metadata_address": METADATA_ADDRESS},
        )

    async def start(
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        launch: dict,
        files: GuestFiles,
    ) -> None:
        if plan.empty:
            return
        await self._identity.register(lease, bootstrap)
        config = {
            "metadata_url": self._url,
            "allocation_id": str(lease.id),
            "auth": self._identity.agent_auth(lease.id),
            "token_volumes": [
                {
                    "volume": volume.volume,
                    "directory": volume.directory,
                    "tokens": [{"path": token.path} for token in volume.tokens],
                }
                for volume in plan.token_volumes
            ],
            "forge_session": (
                {"directory": plan.forge_session_directory, "file": plan.forge_session_file}
                if plan.forge_session
                else None
            ),
            "listen": {"address": METADATA_ADDRESS, "port": self._port},
            "refresh_fraction": self._refresh_fraction,
            "retry_seconds": self._retry,
            "request_timeout_seconds": self._timeout,
            "ca_file": self._ca_file,
        }
        await files.write_files(
            lease,
            bootstrap,
            [
                {
                    "directory": AGENT_CONFIG_DIRECTORY,
                    "name": AGENT_CONFIG_FILE,
                    "content": json.dumps(config),
                }
            ],
        )
        if plan.forge_session:
            # Each start is a new launch: the init agent fetches its credential, and
            # a broker still running from the previous launch must re-read it.
            launch["relaunch"] = True

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        await self._identity.unregister(lease, bootstrap)
