"""Push devices as integrations: each device is an owner's messaging integration connection.

Outside mini mode, Niuu keeps integrations and their secrets in one place: the
``integration_connections`` table holds the connection, and its credential lives in
the pluggable credential store (OpenBao on the clusters). A registered push device
follows that model instead of a table of its own:

* one ``MESSAGING`` connection per (owner, device), slug :data:`PUSH_DEVICE_SLUG`,
  with the platform, APNs topic and environment in its (non-secret) ``config``;
* the device token in the owner's credential store entry named by the connection.

The credential name is derived from (owner, token), and a device registering again
(every app launch) finds its connection by that name and updates it. The lookup goes
by name, not id: the HTTP integration repository (a Forge using another cluster's
shared store) lets the server assign the id, so a derived id is not kept. People see their
devices under their integrations and can disable or remove one there; a disabled
connection receives no pushes.

Push devices are delivery targets, never session integrations: session start leaves
them out (``is_push_device``) and Ting's channel factory skips them.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid5

from niuu.domain.models import (
    PUSH_DEVICE_SLUG,
    IntegrationConnection,
    IntegrationType,
    SecretType,
    is_push_device,
)
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository
from volundr.domain.models import DevicePlatform, DeviceToken
from volundr.domain.ports import DeviceTokenRepository
from volundr.domain.push import PushTokenKind, UnsupportedTokenKindError

logger = logging.getLogger(__name__)

#: Credentials of integration connections are stored per user.
_CREDENTIAL_OWNER_TYPE = "user"
#: Namespace for the deterministic per-device connection id. Never change it.
_DEVICE_CONNECTION_NAMESPACE = UUID("8f0e6c1a-2b4d-4e7f-9a3c-5d1b7e2f6a40")

__all__ = ["IntegrationDeviceTokenRepository", "PUSH_DEVICE_SLUG", "is_push_device"]


def _device_key(owner_id: str, token: str) -> str:
    """A stable, non-reversible key for one owner's device (the token is the secret)."""
    return hashlib.sha256(f"{owner_id}\0{token}".encode()).hexdigest()[:24]


class IntegrationDeviceTokenRepository(DeviceTokenRepository):
    """``DeviceTokenRepository`` over the integration repository and the credential store."""

    def __init__(
        self,
        integration_repository: IntegrationRepository,
        credential_store: CredentialStorePort,
    ) -> None:
        self._integrations = integration_repository
        self._credentials = credential_store

    @staticmethod
    def credential_name(owner_id: str, token: str) -> str:
        return f"push-device-{_device_key(owner_id, token)}"

    @staticmethod
    def connection_id(owner_id: str, token: str) -> str:
        key = _device_key(owner_id, token)
        return str(uuid5(_DEVICE_CONNECTION_NAMESPACE, f"{owner_id}\0{key}"))

    async def upsert(self, device: DeviceToken) -> DeviceToken:
        """Store the token in the credential store, then the connection that names it.

        The credential is written first: a connection never points at a missing secret.
        Re-registering updates the device's connection and keeps its creation time; a
        device turned off (any of its connections disabled) stays off. Extra connections
        for the same device, left by registrations that each created one, are removed.
        Only alert tokens are kept: Live Activity, push-to-start and widget tokens
        need the push gateway's ``device_tokens`` table.
        """
        if device.kind is not PushTokenKind.ALERT:
            raise UnsupportedTokenKindError(
                f"This Forge keeps alert tokens only (push-device integrations); a "
                f"{device.kind.value} token needs push.device_registry: postgres"
            )
        name = self.credential_name(device.owner_id, device.token)
        await self._credentials.store(
            _CREDENTIAL_OWNER_TYPE,
            device.owner_id,
            name,
            SecretType.GENERIC,
            {"device_token": device.token},
            metadata={"kind": PUSH_DEVICE_SLUG, "platform": device.platform.value},
        )
        existing = await self._connections_for(device.owner_id, name)
        kept = existing[0] if existing else None
        for duplicate in existing[1:]:
            await self._integrations.delete_connection(duplicate.id)
        now = datetime.now(UTC)
        config: dict[str, str] = {"platform": device.platform.value}
        if device.app_bundle_id:
            config["app_bundle_id"] = device.app_bundle_id
        if device.environment:
            config["environment"] = device.environment.value
        connection = IntegrationConnection(
            id=kept.id if kept is not None else self.connection_id(device.owner_id, device.token),
            owner_id=device.owner_id,
            integration_type=IntegrationType.MESSAGING,
            adapter="",
            credential_name=name,
            config=config,
            enabled=all(c.enabled for c in existing),
            created_at=kept.created_at if kept is not None else now,
            updated_at=now,
            slug=PUSH_DEVICE_SLUG,
        )
        saved = await self._integrations.save_connection(connection)
        return self._device(saved, device.token)

    async def list_for_owner(
        self, owner_id: str, *, kinds: Sequence[PushTokenKind] | None = None
    ) -> list[DeviceToken]:
        """The owner's enabled push devices whose token is in the credential store.

        They are all alert tokens, so asking for other kinds only returns nothing.
        """
        if kinds is not None and PushTokenKind.ALERT not in kinds:
            return []
        connections = await self._integrations.list_connections(
            owner_id, integration_type=IntegrationType.MESSAGING
        )
        devices: list[DeviceToken] = []
        for connection in connections:
            if not is_push_device(connection) or not connection.enabled:
                continue
            secret = await self._credentials.get_value(
                _CREDENTIAL_OWNER_TYPE, owner_id, connection.credential_name
            )
            token = (secret or {}).get("device_token", "").strip()
            if not token:
                logger.warning(
                    "Push device connection %s has no token in credential %s; skipping it",
                    connection.id,
                    connection.credential_name,
                )
                continue
            devices.append(self._device(connection, token))
        return sorted(devices, key=lambda device: device.updated_at, reverse=True)

    async def delete(self, owner_id: str, token: str, *, kind: PushTokenKind | None = None) -> bool:
        """Remove the device's connections, then its credential (alert tokens only)."""
        if kind is not None and kind is not PushTokenKind.ALERT:
            return False
        name = self.credential_name(owner_id, token)
        connections = await self._connections_for(owner_id, name)
        if not connections:
            return False
        for connection in connections:
            await self._integrations.delete_connection(connection.id)
        await self._credentials.delete(_CREDENTIAL_OWNER_TYPE, owner_id, name)
        return True

    async def _connections_for(
        self, owner_id: str, credential_name: str
    ) -> list[IntegrationConnection]:
        """The owner's push-device connections naming this credential, oldest first."""
        connections = await self._integrations.list_connections(
            owner_id, integration_type=IntegrationType.MESSAGING
        )
        matches = [
            connection
            for connection in connections
            if is_push_device(connection) and connection.credential_name == credential_name
        ]
        return sorted(matches, key=lambda connection: connection.created_at)

    @staticmethod
    def _device(connection: IntegrationConnection, token: str) -> DeviceToken:
        config = connection.config if isinstance(connection.config, dict) else {}
        try:
            platform = DevicePlatform(config.get("platform", DevicePlatform.IOS.value))
        except ValueError:
            platform = DevicePlatform.IOS
        return DeviceToken(
            id=UUID(connection.id),
            owner_id=connection.owner_id,
            platform=platform,
            token=token,
            app_bundle_id=config.get("app_bundle_id") or None,
            environment=config.get("environment") or None,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )
