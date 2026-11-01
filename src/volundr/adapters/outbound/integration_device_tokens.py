"""Push devices as integrations: each device is an owner's messaging integration connection.

Outside mini mode, Niuu keeps integrations and their secrets in one place: the
``integration_connections`` table holds the connection, and its credential lives in
the pluggable credential store (OpenBao on the clusters). A registered push device
follows that model instead of a table of its own:

* one ``MESSAGING`` connection per (owner, device), slug :data:`PUSH_DEVICE_SLUG`,
  with the platform, APNs topic and environment in its (non-secret) ``config``;
* the device token in the owner's credential store entry named by the connection.

The connection id and credential name are derived from (owner, token), so a device
registering again (every app launch) updates the same connection. People see their
devices under their integrations and can disable or remove one there; a disabled
connection receives no pushes.

Push devices are delivery targets, never session integrations: session start leaves
them out (``is_push_device``) and Ting's channel factory skips them.
"""

from __future__ import annotations

import hashlib
import logging
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
        Re-registering keeps the connection's ``enabled`` choice and creation time.
        """
        name = self.credential_name(device.owner_id, device.token)
        connection_id = self.connection_id(device.owner_id, device.token)
        await self._credentials.store(
            _CREDENTIAL_OWNER_TYPE,
            device.owner_id,
            name,
            SecretType.GENERIC,
            {"device_token": device.token},
            metadata={"kind": PUSH_DEVICE_SLUG, "platform": device.platform.value},
        )
        existing = await self._integrations.get_connection(connection_id)
        now = datetime.now(UTC)
        config: dict[str, str] = {"platform": device.platform.value}
        if device.app_bundle_id:
            config["app_bundle_id"] = device.app_bundle_id
        if device.environment:
            config["environment"] = device.environment
        connection = IntegrationConnection(
            id=connection_id,
            owner_id=device.owner_id,
            integration_type=IntegrationType.MESSAGING,
            adapter="",
            credential_name=name,
            config=config,
            enabled=existing.enabled if existing is not None else True,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            slug=PUSH_DEVICE_SLUG,
        )
        await self._integrations.save_connection(connection)
        return self._device(connection, device.token)

    async def list_for_owner(self, owner_id: str) -> list[DeviceToken]:
        """The owner's enabled push devices whose token is in the credential store."""
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

    async def delete(self, owner_id: str, token: str) -> bool:
        """Remove the device's connection, then its credential."""
        connection_id = self.connection_id(owner_id, token)
        connection = await self._integrations.get_connection(connection_id)
        if connection is None or connection.owner_id != owner_id or not is_push_device(connection):
            return False
        await self._integrations.delete_connection(connection_id)
        await self._credentials.delete(_CREDENTIAL_OWNER_TYPE, owner_id, connection.credential_name)
        return True

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
