"""The push-device registry: integration connections, the credential store, gateway state.

Niuu keeps integrations and their secrets in one place: the ``integration_connections``
table holds the connection, and its credential lives in the pluggable credential
store (OpenBao on the clusters). A push device follows that model:

* **The device** is the owner's ``MESSAGING`` connection with slug
  :data:`PUSH_DEVICE_SLUG`, one per app install, with the platform, APNs topic,
  environment and install id in its (non-secret) ``config``. People see their
  devices under their integrations and can disable or remove one there; every
  token of a disabled device is skipped, and removing it removes its tokens.
* **Every token** of the install is a credential-store entry: the alert token under
  the connection's own ``credential_name`` (as before), the others under names
  derived from it and their kind (and, for a Live Activity, its activity).
* **Gateway state** is a ``device_tokens`` row per token that names its credential
  and connection and keeps what the push gateway queries and locks: the kind, the
  environment, the activity and the throttle columns. It never holds the token,
  only its SHA-256, so a token that moves to another owner is found.

An install is named by the ``install_id`` the app sends. An alert token registered
without one is its own install (the connection is found by a name derived from the
owner and the token), which is how every device registered before install ids
existed keeps working. Any other kind must name its install.

Lookups go by name, never by a derived id: the HTTP integration repository (a Forge
using another cluster's shared store) lets the server assign connection ids.

Push devices are delivery targets, never session integrations: session start leaves
them out (``is_push_device``) and Ting's channel factory skips them.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from niuu.domain.models import (
    PUSH_DEVICE_SLUG,
    IntegrationConnection,
    IntegrationType,
    SecretType,
    is_push_device,
)
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository
from volundr.adapters.outbound.push_device_state import DeviceStateRow, DeviceStateStore
from volundr.domain.models import DevicePlatform, DeviceToken
from volundr.domain.ports import PushTokenRegistry
from volundr.domain.push import (
    PushEnvironment,
    PushTokenKind,
    UnsupportedTokenKindError,
    token_fingerprint,
)

logger = logging.getLogger(__name__)

#: Credentials of integration connections are stored per user.
_CREDENTIAL_OWNER_TYPE = "user"
#: Namespace for the deterministic per-device connection id. Never change it.
_DEVICE_CONNECTION_NAMESPACE = UUID("8f0e6c1a-2b4d-4e7f-9a3c-5d1b7e2f6a40")
#: The key of a token inside its credential-store entry.
TOKEN_SECRET_KEY = "device_token"
#: Hex digits of the keys credential names are derived from (sha256 prefixes).
_NAME_KEY_HEX = 24
_ACTIVITY_KEY_HEX = 16
#: The config key of a push-device connection that names its app install.
INSTALL_ID_CONFIG_KEY = "install_id"

__all__ = [
    "IntegrationDeviceRegistry",
    "PUSH_DEVICE_SLUG",
    "is_push_device",
    "token_sha256",
]


def token_sha256(token: str) -> str:
    """The SHA-256 a ``device_tokens`` row keeps instead of the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _key(*parts: str, hex_digits: int = _NAME_KEY_HEX) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:hex_digits]


def legacy_credential_name(owner_id: str, token: str) -> str:
    """The credential of an install known only by its alert token (no install id)."""
    return f"push-device-{_key(owner_id, token)}"


def install_credential_name(owner_id: str, install_id: str) -> str:
    """The credential of an install's alert token, for an install the app named."""
    return f"push-device-{_key(owner_id, 'install', install_id)}"


def token_credential_name(
    connection: IntegrationConnection, kind: PushTokenKind, activity_id: str | None
) -> str:
    """Where a token of ``kind`` of this install is kept in the credential store."""
    if kind is PushTokenKind.ALERT:
        return connection.credential_name
    name = f"{connection.credential_name}-{kind.value.replace('_', '-')}"
    if kind is PushTokenKind.LIVE_ACTIVITY and activity_id:
        return f"{name}-{_key(activity_id, hex_digits=_ACTIVITY_KEY_HEX)}"
    return name


def _config(connection: IntegrationConnection) -> dict:
    return connection.config if isinstance(connection.config, dict) else {}


class IntegrationDeviceRegistry(PushTokenRegistry):
    """Push devices over the integration repository, the credential store and the
    gateway's state rows.

    ``token_kinds`` are the kinds this node takes. Only the node that runs the push
    gateway takes more than alert tokens: no other node pushes to them, and a token
    registered there would sit in a table the gateway never reads.
    """

    def __init__(
        self,
        integration_repository: IntegrationRepository,
        credential_store: CredentialStorePort,
        state: DeviceStateStore,
        *,
        token_kinds: Iterable[PushTokenKind] = (PushTokenKind.ALERT,),
    ) -> None:
        self._integrations = integration_repository
        self._credentials = credential_store
        self._state = state
        self._token_kinds = frozenset(token_kinds)
        if PushTokenKind.ALERT not in self._token_kinds:
            raise ValueError("A push-device registry always takes alert tokens")
        # Non-alert tokens by credential name: (sha256, token). Only this node writes
        # them, and the row's sha256 says when the cached one is out of date.
        self._token_cache: dict[str, tuple[str, str]] = {}
        #: Devices or tokens whose credential was missing (see ``list_for_owner``).
        self.missing_credentials = 0

    @property
    def token_kinds(self) -> frozenset[PushTokenKind]:
        return self._token_kinds

    # -- Registration ----------------------------------------------------------

    async def upsert(self, device: DeviceToken) -> DeviceToken:
        """Register a token on its install's device, the token in the credential store.

        The credential is written before the row and the connection: neither ever
        names a missing secret. Registering the same token again (every app launch)
        writes nothing to the credential store. A device the owner turned off stays
        off.
        """
        if device.kind not in self._token_kinds:
            raise UnsupportedTokenKindError(
                f"This Forge does not run the push gateway, the only user of "
                f"{device.kind.value} tokens: register them on the Guild (it sends them to "
                "the gateway node) or on the gateway node itself"
            )
        if device.kind is not PushTokenKind.ALERT and not device.install_id:
            raise UnsupportedTokenKindError(
                f"A {device.kind.value} token must name its app install (install_id): "
                "its device is the install, which the owner sees and switches off"
            )
        connection = await self._install_connection(device)
        name = token_credential_name(connection, device.kind, device.activity_id)
        sha = token_sha256(device.token)
        existing = await self._state.get(name)
        if existing is None or existing.token_sha256 != sha or existing.owner_id != device.owner_id:
            await self._store_token(device.owner_id, name, device)
        row, removed = await self._state.register(
            DeviceStateRow(
                id=existing.id if existing is not None else device.id,
                owner_id=device.owner_id,
                platform=device.platform.value,
                app_bundle_id=device.app_bundle_id,
                kind=device.kind.value,
                environment=(device.environment or PushEnvironment.PRODUCTION).value,
                activity_id=device.activity_id,
                connection_id=connection.id,
                credential_name=name,
                token_sha256=sha,
                created_at=existing.created_at if existing is not None else device.created_at,
                updated_at=datetime.now(UTC),
            )
        )
        if device.kind is not PushTokenKind.ALERT:
            self._token_cache[name] = (sha, device.token)
        await self._forget(removed)
        return self._device(row, device.token, connection)

    async def _install_connection(self, device: DeviceToken) -> IntegrationConnection:
        """The install's device connection, created (or brought up to date) as needed."""
        connections = await self._push_devices(device.owner_id)
        legacy_name = legacy_credential_name(device.owner_id, device.token)
        matches: list[IntegrationConnection] = []
        if device.install_id:
            matches = [
                c for c in connections if _config(c).get(INSTALL_ID_CONFIG_KEY) == device.install_id
            ]
        if not matches and device.kind is PushTokenKind.ALERT:
            # The install's alert token was registered before the app named its
            # install: that device becomes the install's, switch and history kept.
            matches = [c for c in connections if c.credential_name == legacy_name]
        matches.sort(key=lambda connection: connection.created_at)
        kept = matches[0] if matches else None
        for duplicate in matches[1:]:
            await self._remove_connection(duplicate)
        config: dict[str, str] = dict(_config(kept)) if kept is not None else {}
        config["platform"] = device.platform.value
        if device.app_bundle_id:
            config["app_bundle_id"] = device.app_bundle_id
        if device.environment:
            config["environment"] = device.environment.value
        if device.install_id:
            config[INSTALL_ID_CONFIG_KEY] = device.install_id
        if kept is not None and len(matches) == 1 and config == _config(kept):
            return kept
        if kept is not None:
            credential_name = kept.credential_name
        elif device.install_id:
            credential_name = install_credential_name(device.owner_id, device.install_id)
        else:
            credential_name = legacy_name
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id=kept.id if kept is not None else self._connection_id(device, credential_name),
            owner_id=device.owner_id,
            integration_type=IntegrationType.MESSAGING,
            adapter="",
            credential_name=credential_name,
            config=config,
            enabled=all(c.enabled for c in matches),
            created_at=kept.created_at if kept is not None else now,
            updated_at=now,
            slug=PUSH_DEVICE_SLUG,
        )
        return await self._integrations.save_connection(connection)

    @staticmethod
    def _connection_id(device: DeviceToken, credential_name: str) -> str:
        return str(uuid5(_DEVICE_CONNECTION_NAMESPACE, f"{device.owner_id}\0{credential_name}"))

    async def _store_token(self, owner_id: str, name: str, device: DeviceToken) -> None:
        await self._credentials.store(
            _CREDENTIAL_OWNER_TYPE,
            owner_id,
            name,
            SecretType.GENERIC,
            {TOKEN_SECRET_KEY: device.token},
            metadata={
                "kind": PUSH_DEVICE_SLUG,
                "token_kind": device.kind.value,
                "platform": device.platform.value,
            },
        )

    # -- Reading -----------------------------------------------------------------

    async def list_for_owner(
        self, owner_id: str, *, kinds: Sequence[PushTokenKind] | None = None
    ) -> list[DeviceToken]:
        """The owner's tokens on enabled devices, newest first.

        The alert token is read from the connection's own credential, so a device
        registered on another node of the same integration store is found too. The
        tokens of a removed device are removed with it. A device whose credential is
        gone is reported at error level and counted (``missing_credentials``).
        """
        wanted = frozenset(kinds) if kinds is not None else frozenset(PushTokenKind)
        connections = await self._push_devices(owner_id)
        by_id = {connection.id: connection for connection in connections}
        rows: dict[str, DeviceStateRow] = {}
        for row in await self._state.owner_rows(owner_id):
            if row.connection_id in by_id:
                rows[row.credential_name] = row
                continue
            # The owner removed the device: its tokens go with it.
            await self._forget(await self._state.delete(row.id), with_alert_credential=True)
        devices: list[DeviceToken] = []
        for connection in connections:
            if not connection.enabled:
                continue
            if PushTokenKind.ALERT in wanted:
                alert = await self._alert_device(connection, rows.get(connection.credential_name))
                if alert is not None:
                    devices.append(alert)
            for row in rows.values():
                kind = PushTokenKind(row.kind)
                if row.connection_id != connection.id or kind is PushTokenKind.ALERT:
                    continue
                if kind not in wanted:
                    continue
                token = await self._cached_token(row)
                if token is not None:
                    devices.append(self._device(row, token, connection))
        return sorted(devices, key=lambda device: device.updated_at, reverse=True)

    async def _alert_device(
        self, connection: IntegrationConnection, row: DeviceStateRow | None
    ) -> DeviceToken | None:
        """The install's alert token, its row created the first time it is seen here."""
        token = await self._read_token(connection.owner_id, connection.credential_name)
        if token is None:
            if _config(connection).get(INSTALL_ID_CONFIG_KEY) and row is None:
                return None  # The install has not registered its alert token yet.
            self.missing_credentials += 1
            logger.error(
                "Push device %s of owner %s has no token in credential %s: it receives "
                "nothing until the app registers again, or remove the device",
                connection.id,
                connection.owner_id,
                connection.credential_name,
            )
            return None
        sha = token_sha256(token)
        if row is None:
            row = await self._ensure_alert_row(connection, sha)
            if row is None:
                return None
        elif row.token_sha256 != sha:
            # Registered again on another node writing the same store.
            await self._state.set_token_sha(row.id, sha)
        return self._device(row, token, connection)

    async def _ensure_alert_row(
        self, connection: IntegrationConnection, sha: str
    ) -> DeviceStateRow | None:
        """The row of an alert token registered on another node, or before rows existed.

        ``None`` when another device's row holds the token (one phone signed in to two
        accounts, both registered before rows): the holder keeps it until the app
        registers again, which moves it to the account signed in then.
        """
        holder = await self._state.holder(
            sha, PushTokenKind.ALERT.value, connection.credential_name
        )
        if holder is not None:
            logger.warning(
                "Push device %s of owner %s is not pushed to: its alert token is registered "
                "on device %s of owner %s, until the app registers it again",
                connection.id,
                connection.owner_id,
                holder.connection_id,
                holder.owner_id,
            )
            return None
        config = _config(connection)
        row, removed = await self._state.register(
            DeviceStateRow(
                id=uuid4(),
                owner_id=connection.owner_id,
                platform=str(config.get("platform") or DevicePlatform.IOS.value),
                app_bundle_id=config.get("app_bundle_id") or None,
                kind=PushTokenKind.ALERT.value,
                environment=str(config.get("environment") or PushEnvironment.PRODUCTION.value),
                activity_id=None,
                connection_id=connection.id,
                credential_name=connection.credential_name,
                token_sha256=sha,
                created_at=connection.created_at,
                updated_at=connection.updated_at,
            )
        )
        await self._forget(removed)
        return row

    async def _read_token(self, owner_id: str, name: str) -> str | None:
        secret = await self._credentials.get_value(_CREDENTIAL_OWNER_TYPE, owner_id, name)
        token = str((secret or {}).get(TOKEN_SECRET_KEY, "")).strip()
        return token or None

    async def _cached_token(self, row: DeviceStateRow) -> str | None:
        cached = self._token_cache.get(row.credential_name)
        if cached is not None and cached[0] == row.token_sha256:
            return cached[1]
        token = await self._read_token(row.owner_id, row.credential_name)
        if token is None:
            self.missing_credentials += 1
            logger.error(
                "The %s token of push device %s (owner %s) is missing from credential %s: "
                "its registration is dropped and the app registers it again",
                row.kind,
                row.connection_id,
                row.owner_id,
                row.credential_name,
            )
            await self._state.delete(row.id)
            return None
        sha = token_sha256(token)
        if sha != row.token_sha256:
            await self._state.set_token_sha(row.id, sha)
        self._token_cache[row.credential_name] = (sha, token)
        return token

    async def _connection(self, owner_id: str, connection_id: str) -> IntegrationConnection | None:
        """One of the owner's devices, looked up through the owner's own listing: the
        HTTP integration repository answers only owner-scoped reads."""
        return next((c for c in await self._push_devices(owner_id) if c.id == connection_id), None)

    async def _push_devices(self, owner_id: str) -> list[IntegrationConnection]:
        connections = await self._integrations.list_connections(
            owner_id, integration_type=IntegrationType.MESSAGING
        )
        return [connection for connection in connections if is_push_device(connection)]

    # -- Removal -------------------------------------------------------------

    async def delete(self, owner_id: str, token: str, *, kind: PushTokenKind | None = None) -> bool:
        """Remove the owner's registration(s) of a token.

        Removing an install's alert token removes the device: its connection and
        every token of it. True when anything was removed.
        """
        rows = [
            row
            for row in await self._state.owner_rows_with_token(owner_id, token_sha256(token))
            if kind is None or row.kind == kind.value
        ]
        removed = False
        if kind in (None, PushTokenKind.ALERT):
            # A device registered on another node may have no row here yet.
            legacy_name = legacy_credential_name(owner_id, token)
            alert_devices = {
                row.connection_id for row in rows if row.kind == PushTokenKind.ALERT.value
            }
            for connection in await self._push_devices(owner_id):
                if connection.credential_name == legacy_name or connection.id in alert_devices:
                    await self._remove_connection(connection)
                    removed = True
        for row in rows:
            dropped = await self._state.delete(row.id)
            await self._forget(dropped)
            removed = removed or bool(dropped)
        return removed

    async def delete_registration(self, device_id: UUID) -> bool:
        """Remove a token APNs reported as dead; a dead alert token removes the device.

        An alert token is checked against the store first: when the install
        registered a new one since (on any node), only the row is brought up to date.
        """
        row = await self._state.get_by_id(device_id)
        if row is None:
            return False
        if row.kind != PushTokenKind.ALERT.value:
            await self._forget(await self._state.delete(device_id))
            return True
        current = await self._read_token(row.owner_id, row.credential_name)
        if current is not None and token_sha256(current) != row.token_sha256:
            await self._state.set_token_sha(device_id, token_sha256(current))
            return False
        connection = await self._connection(row.owner_id, row.connection_id)
        if connection is None:
            await self._forget(await self._state.delete(device_id), with_alert_credential=True)
            return True
        await self._remove_connection(connection)
        return True

    async def _remove_connection(self, connection: IntegrationConnection) -> None:
        """Remove a device: its connection, every row of it, and their credentials."""
        rows = await self._state.delete_connection(connection.id)
        await self._integrations.delete_connection(connection.id)
        names = {connection.credential_name, *(row.credential_name for row in rows)}
        for name in sorted(names):
            self._token_cache.pop(name, None)
            await self._credentials.delete(_CREDENTIAL_OWNER_TYPE, connection.owner_id, name)

    async def _forget(
        self, rows: Sequence[DeviceStateRow], *, with_alert_credential: bool = False
    ) -> None:
        """Delete the credentials of removed rows.

        An alert credential is the device's own and goes with the device, unless the
        device is already gone. A removed alert row whose token another account or
        install took over ends the device it named.
        """
        for row in rows:
            alert = row.kind == PushTokenKind.ALERT.value
            if not alert or with_alert_credential:
                self._token_cache.pop(row.credential_name, None)
                await self._credentials.delete(
                    _CREDENTIAL_OWNER_TYPE, row.owner_id, row.credential_name
                )
            if not alert or with_alert_credential:
                continue
            connection = await self._connection(row.owner_id, row.connection_id)
            if connection is not None and connection.credential_name == row.credential_name:
                logger.info(
                    "Push device %s of owner %s ended: its alert token is now registered "
                    "by another account or install",
                    connection.id,
                    connection.owner_id,
                )
                await self._remove_connection(connection)

    # -- Push throttles --------------------------------------------------------

    async def claim_push(self, device_id: UUID, *, min_interval_seconds: float) -> bool:
        return await self._state.claim_push(device_id, min_interval_seconds=min_interval_seconds)

    async def release_push(self, device_id: UUID) -> None:
        await self._state.release_push(device_id)

    async def claim_owed_pushes(
        self, kind: PushTokenKind, *, min_interval_seconds: float
    ) -> list[DeviceToken]:
        """Claim the owed pushes of ``kind``; a token of a disabled device is not sent."""
        rows = await self._state.claim_owed_pushes(
            kind.value, min_interval_seconds=min_interval_seconds
        )
        devices: list[DeviceToken] = []
        for row in rows:
            connection = await self._connection(row.owner_id, row.connection_id)
            if connection is None:
                await self._forget(await self._state.delete(row.id), with_alert_credential=True)
                continue
            if not connection.enabled:
                continue
            if kind is PushTokenKind.ALERT:
                token = await self._read_token(row.owner_id, row.credential_name)
            else:
                token = await self._cached_token(row)
            if token is not None:
                devices.append(self._device(row, token, connection))
        return devices

    async def count_by_environment(self) -> dict[str, dict[str, int]]:
        return await self._state.count_by_environment()

    # -- Tokens written before they left Postgres ------------------------------

    async def move_stored_tokens(self) -> int:
        """Move every token ``device_tokens`` still holds into the credential store.

        Each alert token becomes a device (migration 000101 refuses to run while
        another kind is stored). Raises on the first one the credential store or the
        integration repository refuses, so a start never runs with tokens left behind.
        """
        moved = 0
        for stored in await self._state.stored_tokens():
            kind = PushTokenKind(stored.kind)
            if kind is not PushTokenKind.ALERT:
                raise RuntimeError(
                    f"device_tokens row {stored.id} holds a {kind.value} token, which names no "
                    "app install: delete it (DELETE FROM device_tokens WHERE token IS NOT NULL "
                    "AND kind <> 'alert'); the app registers it again"
                )
            device = await self.upsert(
                DeviceToken(
                    owner_id=stored.owner_id,
                    platform=DevicePlatform(stored.platform),
                    token=stored.token,
                    app_bundle_id=stored.app_bundle_id,
                    kind=kind,
                    environment=PushEnvironment(stored.environment),
                    created_at=stored.created_at,
                )
            )
            logger.info(
                "Moved the alert token %s of owner %s into the credential store (device %s)",
                token_fingerprint(device.token),
                device.owner_id,
                device.connection_id,
            )
            moved += 1
        return moved

    @staticmethod
    def _device(row: DeviceStateRow, token: str, connection: IntegrationConnection) -> DeviceToken:
        return DeviceToken(
            id=row.id,
            owner_id=row.owner_id,
            platform=DevicePlatform(row.platform),
            token=token,
            app_bundle_id=row.app_bundle_id,
            kind=PushTokenKind(row.kind),
            environment=PushEnvironment(row.environment),
            activity_id=row.activity_id,
            last_push_at=row.last_push_at,
            install_id=_config(connection).get(INSTALL_ID_CONFIG_KEY) or None,
            connection_id=connection.id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
