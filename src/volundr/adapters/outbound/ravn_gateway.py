"""Ravn resident sessions through Ravn's OpenClaw-protocol shim.

A Ravn resident pod runs ``ravn.adapters.channels.gateway_openclaw``: an OpenClaw
Gateway that executes on Ravn, where every session key is its own Ravn
conversation with its own transcript. So a Ravn resident's sessions are driven by
the OpenClaw controller itself; this adapter supplies only what differs:

* the route: the shim's own port in the resident's workload, which the owning
  backend resolves (``ResidentOpenClawShimHost``: the resident's Service on Flux,
  an OpenShell TCP forward on OpenShell), not the backend's skuld proxy target;
* the token: each resident's shim checks its own token, generated per resident and
  kept in Volundr's credential store like the Hermes residents' API keys
  (``CredentialStoreOpenClawTokens``). The backend that deploys the resident hands
  it to that one resident (a Secret for its pod on Flux, its process env on
  OpenShell), adopting the token a running resident already has before ever minting
  one; there is no cluster-wide master;
* the machine identity: the shim trusts a device on first use behind that token, so
  the device key is derived from the resident's token instead of being minted and
  stored per resident;
* the participant: the resident itself, not NemoClaw;
* the notification outbox: the shim also serves the resident's notification
  outbox (``notifications.*``), which Forge's ``ResidentNotificationRelay`` drains
  over this same authenticated connection (``ResidentNotificationSource``).

The resident's default room (its ``skuld-v1`` chat endpoint, session id = resident
id) is a different endpoint and is unaffected.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from contextlib import suppress
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.domain.models import SecretType
from niuu.domain.resident_notifications import ResidentNotificationEntry
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.openclaw_gateway import (
    _RETRY_HISTORY_POLL_INTERVAL_SECONDS,
    _RETRY_HISTORY_TIMEOUT_SECONDS,
    OpenClawGatewayError,
    OpenClawResidentSessionController,
    _GatewayConnection,
    _new_machine_credential,
    _session_uuid,
)
from volundr.domain.models import ResidentDeploymentProfile, ResidentEngine, ResidentRuntime
from volundr.domain.ports import (
    ResidentNotificationSource,
    ResidentNotificationStream,
    ResidentNotificationsUnavailableError,
    ResidentOpenClawShimHost,
    ResidentOpenClawTokens,
)

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_PORT = 18790
#: A Ravn resident's own shim token in the credential store: ("resident", id, name).
RAVN_OPENCLAW_CREDENTIAL_NAME = "ravn-openclaw"
_TOKEN_FIELD = "token"
_DEVICE_SEED_CONTEXT = b"niuu-ravn-resident-openclaw-device:"
#: The shim's answer to an RPC it does not serve (an older Ravn, or no outbox).
_UNSUPPORTED = "UNSUPPORTED"
_NOTIFICATIONS_CHANGED_EVENT = "notifications.changed"


class CredentialStoreOpenClawTokens(ResidentOpenClawTokens):
    """Each Ravn resident's own shim token, kept per resident in the credential store.

    The same pattern as ``hermes_gateway.ensure_hermes_api_key``: generated once,
    kept under ``("resident", <id>, "ravn-openclaw")``, removed with the resident.
    """

    def __init__(self, store: CredentialStorePort) -> None:
        self._store = store

    async def stored(self, runtime: ResidentRuntime) -> str | None:
        existing = await self._store.get_value(
            "resident", str(runtime.id), RAVN_OPENCLAW_CREDENTIAL_NAME
        )
        if not existing:
            return None
        token = existing.get(_TOKEN_FIELD)
        if not token:
            raise RuntimeError("Ravn resident shim credential is incomplete")
        return token

    async def ensure(self, runtime: ResidentRuntime, *, adopt: str | None = None) -> str:
        token = await self.stored(runtime)
        if token:
            return token
        token = adopt or secrets.token_urlsafe(32)
        await self._store.store(
            "resident",
            str(runtime.id),
            RAVN_OPENCLAW_CREDENTIAL_NAME,
            SecretType.API_KEY,
            {_TOKEN_FIELD: token},
            metadata={"purpose": "ravn-openclaw-shim", "adopted": str(bool(adopt)).lower()},
        )
        if adopt:
            logger.info("Adopted resident %s's running Ravn shim token", runtime.id)
        return token

    async def forget(self, runtime: ResidentRuntime) -> None:
        await self._store.delete("resident", str(runtime.id), RAVN_OPENCLAW_CREDENTIAL_NAME)


class _ShimNotificationStream(ResidentNotificationStream):
    """One resident's notification outbox, read over its OpenClaw shim connection."""

    def __init__(self, connection: _GatewayConnection) -> None:
        self._connection = connection

    async def pending(self, *, limit: int) -> list[ResidentNotificationEntry]:
        payload = await self._connection.request("notifications.pull", {"limit": limit})
        rows = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise OpenClawGatewayError("notifications.pull answered without entries")
        return [ResidentNotificationEntry.model_validate(row) for row in rows]

    async def acknowledge(self, *, through_seq: int) -> None:
        await self._connection.request("notifications.ack", {"throughSeq": through_seq})

    async def wait_for_more(self, *, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                frame = await asyncio.wait_for(self._connection.events.get(), remaining)
            except TimeoutError:
                return
            event = str(frame.get("event") or "")
            if event == "connection.closed":
                raise OpenClawGatewayError("Ravn OpenClaw shim connection closed")
            if event == _NOTIFICATIONS_CHANGED_EVENT:
                # Coalesce a burst: one pull reads everything that is pending.
                while not self._connection.events.empty():
                    queued = self._connection.events.get_nowait()
                    if str(queued.get("event") or "") == "connection.closed":
                        raise OpenClawGatewayError("Ravn OpenClaw shim connection closed")
                return

    def resident_session_id(self, channel: str | None) -> UUID | None:
        # Forge's own session keys name a resident session; any other key (the shim's
        # seeded main conversation, or no channel at all) is the resident's main room.
        return _session_uuid(channel) if channel else None

    async def close(self) -> None:
        with suppress(Exception):
            await self._connection.close()


class RavnResidentSessionController(OpenClawResidentSessionController, ResidentNotificationSource):
    """Ravn implementation of resident sessions, over the Ravn OpenClaw shim."""

    def __init__(
        self,
        runtime_controller: Any,
        credential_store: CredentialStorePort,
        *,
        service_port: int = DEFAULT_SERVICE_PORT,
        retry_history_timeout_seconds: float = _RETRY_HISTORY_TIMEOUT_SECONDS,
        retry_history_poll_interval_seconds: float = _RETRY_HISTORY_POLL_INTERVAL_SECONDS,
        gateway_token: str = "",
    ) -> None:
        if gateway_token:
            logger.warning(
                "RavnResidentSessionController ignores gateway_token: there is no Ravn "
                "sessions master any more; each resident's shim token is its own, kept in "
                "the credential store. Remove gateway_token (secretKwargs) from "
                "residentRuntimeSessionControllers."
            )
        if not isinstance(runtime_controller, ResidentOpenClawShimHost):
            raise ValueError(
                "RavnResidentSessionController needs a backend that hosts the Ravn OpenClaw "
                f"shim ({getattr(runtime_controller, 'backend', runtime_controller)!r} does "
                "not implement ResidentOpenClawShimHost)"
            )
        super().__init__(
            runtime_controller,
            credential_store,
            retry_history_timeout_seconds=retry_history_timeout_seconds,
            retry_history_poll_interval_seconds=retry_history_poll_interval_seconds,
        )
        self._shim_host: ResidentOpenClawShimHost = runtime_controller
        self._service_port = int(service_port)
        # The backend that deploys the resident hands each resident its own token.
        runtime_controller.set_resident_openclaw_tokens(
            CredentialStoreOpenClawTokens(credential_store)
        )

    @property
    def engine(self) -> ResidentEngine:
        return ResidentEngine.RAVN

    def provides_sessions(self, profile: ResidentDeploymentProfile) -> bool:
        """A profile that switches the shim off (resident.openclaw.enabled) has none."""
        values = profile.deployment.get("values")
        resident = values.get("resident") if isinstance(values, dict) else None
        shim = resident.get("openclaw") if isinstance(resident, dict) else None
        return not (isinstance(shim, dict) and shim.get("enabled") is False)

    def _proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        return self._shim_host.resident_openclaw_target(runtime, self._service_port)

    async def _machine_credentials(self, runtime: ResidentRuntime) -> dict[str, str]:
        token = await self._shim_host.resident_openclaw_token(runtime)
        seed = hmac.new(
            token.encode("utf-8"),
            _DEVICE_SEED_CONTEXT + str(runtime.id).encode("ascii"),
            hashlib.sha256,
        ).digest()
        credentials = _new_machine_credential(Ed25519PrivateKey.from_private_bytes(seed))
        return {**credentials, "gateway_token": token, "device_token": ""}

    async def _remember_device_token(
        self,
        runtime: ResidentRuntime,
        credentials: dict[str, str],
    ) -> None:
        """The shim's device token is derived from the gateway token; nothing to keep."""

    def _participant(self, runtime: ResidentRuntime) -> dict[str, Any]:
        persona = runtime.persona_name or runtime.name
        return {
            "peer_id": runtime.flock_peer_id or f"flock-{persona}",
            "persona": persona,
            "display_name": runtime.name,
            "participant_type": "resident",
            "status": "idle",
        }

    async def _prepare_chat(self, connection: _GatewayConnection, session_key: str) -> None:
        # The shim relays a session's chat events only to connections subscribed to it.
        await connection.request("sessions.subscribe", {"sessionKey": session_key})

    async def open_notifications(self, runtime: ResidentRuntime) -> ResidentNotificationStream:
        """Subscribe to the resident's notification outbox over its own shim token."""
        connection = await self._connect(runtime)
        try:
            await connection.request("notifications.subscribe", {})
        except OpenClawGatewayError as exc:
            await connection.close()
            if exc.details.get("code") == _UNSUPPORTED:
                raise ResidentNotificationsUnavailableError(
                    f"Resident {runtime.name} serves no notification outbox (its Ravn predates "
                    "it, or gateway.channels.openclaw.notifications_enabled is false)"
                ) from exc
            raise
        except BaseException:
            await connection.close()
            raise
        return _ShimNotificationStream(connection)
