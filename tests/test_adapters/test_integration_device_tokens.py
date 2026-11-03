"""The push-device registry: integration connections, tokens in the credential store,
gateway state without tokens."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.adapters.notifications.integrations import NotificationChannelFactory
from niuu.domain.models import PUSH_DEVICE_SLUG, IntegrationType
from tests.support.push_device_state import InMemoryDeviceStateStore
from volundr.adapters.outbound.integration_device_tokens import (
    IntegrationDeviceRegistry,
    legacy_credential_name,
    token_sha256,
)
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.adapters.outbound.push_device_state import StoredToken
from volundr.config import PushNotificationConfig
from volundr.domain.models import DevicePlatform, DeviceToken
from volundr.domain.push import PushEnvironment, PushTokenKind, UnsupportedTokenKindError

INSTALL = "install-a"
ALL_KINDS = frozenset(PushTokenKind)


def _device(
    token: str = "apns-token-1",
    owner: str = "user-1",
    kind: PushTokenKind = PushTokenKind.ALERT,
    **extra,
) -> DeviceToken:
    return DeviceToken(
        owner_id=owner,
        platform=DevicePlatform.IOS,
        token=token,
        app_bundle_id="com.niuu.lexi.chat",
        kind=kind,
        **extra,
    )


class CountingCredentialStore(MemoryCredentialStore):
    def __init__(self) -> None:
        super().__init__()
        self.writes = 0
        self.reads = 0

    async def store(self, *args, **kwargs):
        self.writes += 1
        return await super().store(*args, **kwargs)

    async def get_value(self, *args, **kwargs):
        self.reads += 1
        return await super().get_value(*args, **kwargs)


class ServerAssignedIdRepository(InMemoryIntegrationRepository):
    """Like the HTTP repository: a new connection gets an id the server picks."""

    async def save_connection(self, connection):
        if await self.get_connection(connection.id) is None:
            connection = replace(connection, id=str(uuid4()))
        return await super().save_connection(connection)


class Stores:
    def __init__(self, *, kinds=ALL_KINDS, integrations=None) -> None:
        self.integrations = integrations or InMemoryIntegrationRepository()
        self.credentials = CountingCredentialStore()
        self.state = InMemoryDeviceStateStore()
        self.registry = IntegrationDeviceRegistry(
            self.integrations, self.credentials, self.state, token_kinds=kinds
        )

    async def connections(self, owner: str = "user-1"):
        return await self.integrations.list_connections(owner)

    async def secret(self, name: str, owner: str = "user-1"):
        return await self.credentials.get_value("user", owner, name)


@pytest.fixture
def stores() -> Stores:
    return Stores()


async def _register_install(
    stores: Stores, owner: str = "user-1"
) -> dict[PushTokenKind, DeviceToken]:
    registry = stores.registry
    return {
        PushTokenKind.ALERT: await registry.upsert(_device("alert-a", owner, install_id=INSTALL)),
        PushTokenKind.LIVE_ACTIVITY: await registry.upsert(
            _device(
                "la-a", owner, PushTokenKind.LIVE_ACTIVITY, install_id=INSTALL, activity_id="act-1"
            )
        ),
        PushTokenKind.LIVE_ACTIVITY_START: await registry.upsert(
            _device("start-a", owner, PushTokenKind.LIVE_ACTIVITY_START, install_id=INSTALL)
        ),
        PushTokenKind.WIDGET: await registry.upsert(
            _device("widget-a", owner, PushTokenKind.WIDGET, install_id=INSTALL)
        ),
    }


class TestRegistration:
    async def test_an_alert_token_is_a_device_with_its_token_in_the_credential_store(self, stores):
        stored = await stores.registry.upsert(_device(environment="sandbox"))

        [connection] = await stores.connections()
        assert connection.integration_type == IntegrationType.MESSAGING
        assert connection.slug == PUSH_DEVICE_SLUG
        assert connection.enabled
        assert connection.config == {
            "platform": "ios",
            "app_bundle_id": "com.niuu.lexi.chat",
            "environment": "sandbox",
        }
        assert await stores.secret(connection.credential_name) == {"device_token": "apns-token-1"}
        # The gateway's row names the credential and the device, never the token.
        [row] = stores.state.rows.values()
        assert row.credential_name == connection.credential_name
        assert row.connection_id == connection.id
        assert row.token_sha256 == token_sha256("apns-token-1")
        assert "apns-token-1" not in repr(row) and "apns-token-1" not in repr(connection)
        assert stored.id == row.id
        assert stored.connection_id == connection.id
        assert stored.token == "apns-token-1"
        assert stored.environment == "sandbox"

    async def test_registering_the_same_token_again_writes_no_secret(self, stores):
        await stores.registry.upsert(_device())
        writes = stores.credentials.writes

        await stores.registry.upsert(_device())
        await stores.registry.upsert(_device())

        assert stores.credentials.writes == writes
        assert len(await stores.connections()) == 1

    async def test_registering_again_keeps_a_device_the_owner_turned_off(self, stores):
        await stores.registry.upsert(_device())
        [connection] = await stores.connections()
        await stores.integrations.save_connection(replace(connection, enabled=False))

        await stores.registry.upsert(_device(environment="production"))

        [connection] = await stores.connections()
        assert not connection.enabled
        assert connection.config["environment"] == "production"
        assert await stores.registry.list_for_owner("user-1") == []

    async def test_a_node_without_the_gateway_refuses_other_kinds(self):
        stores = Stores(kinds=(PushTokenKind.ALERT,))

        with pytest.raises(UnsupportedTokenKindError, match="does not run the push gateway"):
            await stores.registry.upsert(
                _device("w", kind=PushTokenKind.WIDGET, install_id=INSTALL)
            )
        assert await stores.connections() == []

    async def test_a_non_alert_token_must_name_its_install(self, stores):
        with pytest.raises(UnsupportedTokenKindError, match="install_id"):
            await stores.registry.upsert(_device("w", kind=PushTokenKind.WIDGET))
        assert await stores.connections() == []

    def test_a_registry_always_takes_alert_tokens(self):
        with pytest.raises(ValueError, match="alert"):
            IntegrationDeviceRegistry(
                InMemoryIntegrationRepository(),
                MemoryCredentialStore(),
                InMemoryDeviceStateStore(),
                token_kinds=(PushTokenKind.WIDGET,),
            )

    async def test_every_token_of_an_install_is_one_device(self, stores):
        registered = await _register_install(stores)

        [connection] = await stores.connections()
        assert connection.config["install_id"] == INSTALL
        names = {row.credential_name for row in stores.state.rows.values()}
        assert len(names) == 4 and connection.credential_name in names
        for kind, device in registered.items():
            assert device.connection_id == connection.id
            row = stores.state.rows[next(n for n in names if stores.state.rows[n].id == device.id)]
            assert await stores.secret(row.credential_name) == {"device_token": device.token}
            assert row.kind == kind.value
        listed = await stores.registry.list_for_owner("user-1")
        assert sorted(d.token for d in listed) == ["alert-a", "la-a", "start-a", "widget-a"]
        assert all(d.install_id == INSTALL for d in listed)

    async def test_a_device_turned_off_receives_no_token_of_any_kind(self, stores):
        await _register_install(stores)
        [connection] = await stores.connections()
        await stores.integrations.save_connection(replace(connection, enabled=False))

        assert await stores.registry.list_for_owner("user-1") == []

    async def test_kinds_narrow_the_listing(self, stores):
        await _register_install(stores)

        widgets = await stores.registry.list_for_owner("user-1", kinds=[PushTokenKind.WIDGET])
        alerts = await stores.registry.list_for_owner("user-1", kinds=[PushTokenKind.ALERT])

        assert [d.token for d in widgets] == ["widget-a"]
        assert [d.token for d in alerts] == ["alert-a"]

    async def test_a_device_known_by_its_alert_token_becomes_the_install(self, stores):
        legacy = await stores.registry.upsert(_device("alert-a"))
        [connection] = await stores.connections()
        await stores.integrations.save_connection(replace(connection, enabled=False))

        adopted = await stores.registry.upsert(_device("alert-a", install_id=INSTALL))
        widget = await stores.registry.upsert(
            _device("widget-a", kind=PushTokenKind.WIDGET, install_id=INSTALL)
        )

        [kept] = await stores.connections()
        assert kept.id == connection.id
        assert kept.credential_name == legacy_credential_name("user-1", "alert-a")
        assert kept.config["install_id"] == INSTALL
        assert not kept.enabled
        assert adopted.id == legacy.id
        assert widget.connection_id == kept.id

    async def test_a_new_live_activity_token_replaces_the_activitys_earlier_one(self, stores):
        first = await stores.registry.upsert(
            _device(
                "la-old", kind=PushTokenKind.LIVE_ACTIVITY, install_id=INSTALL, activity_id="a1"
            )
        )
        await stores.registry.upsert(
            _device(
                "la-other", kind=PushTokenKind.LIVE_ACTIVITY, install_id=INSTALL, activity_id="a2"
            )
        )

        again = await stores.registry.upsert(
            _device(
                "la-new", kind=PushTokenKind.LIVE_ACTIVITY, install_id=INSTALL, activity_id="a1"
            )
        )

        live = await stores.registry.list_for_owner("user-1", kinds=[PushTokenKind.LIVE_ACTIVITY])
        assert sorted(d.token for d in live) == ["la-new", "la-other"]
        assert again.id == first.id

    async def test_a_token_moves_to_the_account_signed_in_now(self, stores):
        await _register_install(stores, owner="user-1")
        [old] = await stores.connections("user-1")

        await stores.registry.upsert(_device("alert-a", "user-2", install_id="install-b"))

        # The first account's device of that phone ended, with every token of it.
        assert await stores.connections("user-1") == []
        assert await stores.registry.list_for_owner("user-1") == []
        assert await stores.secret(old.credential_name) is None
        assert [d.token for d in await stores.registry.list_for_owner("user-2")] == ["alert-a"]

    async def test_new_connections_keep_the_id_a_server_assigned(self):
        stores = Stores(integrations=ServerAssignedIdRepository())

        first = await stores.registry.upsert(_device())
        again = await stores.registry.upsert(_device(environment="sandbox"))

        [connection] = await stores.connections()
        assert again.connection_id == first.connection_id == connection.id
        assert connection.config["environment"] == "sandbox"

    async def test_registering_collapses_duplicate_connections_of_one_device(self, stores):
        await stores.registry.upsert(_device())
        [connection] = await stores.connections()
        oldest = replace(
            connection, id=str(uuid4()), created_at=connection.created_at - timedelta(days=2)
        )
        turned_off = replace(
            connection,
            id=str(uuid4()),
            enabled=False,
            created_at=connection.created_at - timedelta(days=1),
        )
        await stores.integrations.save_connection(oldest)
        await stores.integrations.save_connection(turned_off)

        again = await stores.registry.upsert(_device())

        [kept] = await stores.connections()
        assert kept.id == oldest.id
        assert again.connection_id == oldest.id
        assert not kept.enabled


class TestDevicesRegisteredElsewhere:
    """A device in the shared integration store that this node never registered."""

    async def _legacy_device(self, stores: Stores, token: str = "alert-z", owner: str = "user-1"):
        other_node = IntegrationDeviceRegistry(
            stores.integrations, stores.credentials, InMemoryDeviceStateStore()
        )
        return await other_node.upsert(_device(token, owner, environment="sandbox"))

    async def test_its_alert_token_is_pushed_and_gets_a_gateway_row(self, stores):
        await self._legacy_device(stores)
        assert stores.state.rows == {}

        [alert] = await stores.registry.list_for_owner("user-1")

        assert alert.token == "alert-z"
        assert alert.environment == "sandbox"
        [row] = stores.state.rows.values()
        assert alert.id == row.id
        assert await stores.registry.claim_push(alert.id, min_interval_seconds=60)

    async def test_a_token_registered_again_elsewhere_updates_the_row(self, stores):
        await stores.registry.upsert(_device("alert-a", install_id=INSTALL))
        [connection] = await stores.connections()
        await stores.credentials.store(
            "user", "user-1", connection.credential_name, "generic", {"device_token": "alert-b"}
        )

        [alert] = await stores.registry.list_for_owner("user-1")

        assert alert.token == "alert-b"
        [row] = stores.state.rows.values()
        assert row.token_sha256 == token_sha256("alert-b")

    async def test_a_token_another_device_holds_is_not_pushed_twice(self, stores, caplog):
        await stores.registry.upsert(_device("shared", "user-2"))
        await self._legacy_device(stores, token="shared", owner="user-1")

        with caplog.at_level(logging.WARNING):
            assert await stores.registry.list_for_owner("user-1") == []

        assert "registered on device" in caplog.text
        assert [d.token for d in await stores.registry.list_for_owner("user-2")] == ["shared"]


class TestRemoval:
    async def test_removing_the_alert_token_removes_the_device_and_every_token(self, stores):
        await _register_install(stores)
        names = {row.credential_name for row in stores.state.rows.values()}

        assert await stores.registry.delete("user-1", "alert-a")

        assert await stores.connections() == []
        assert stores.state.rows == {}
        for name in names:
            assert await stores.secret(name) is None
        assert not await stores.registry.delete("user-1", "alert-a")
        assert not await stores.registry.delete("user-2", "alert-a")

    async def test_removing_one_kind_keeps_the_device(self, stores):
        await _register_install(stores)

        assert await stores.registry.delete("user-1", "widget-a", kind=PushTokenKind.WIDGET)

        kinds = {d.kind for d in await stores.registry.list_for_owner("user-1")}
        assert PushTokenKind.WIDGET not in kinds and PushTokenKind.ALERT in kinds

    async def test_a_device_the_owner_removed_takes_its_tokens_with_it(self, stores):
        await _register_install(stores)
        [connection] = await stores.connections()
        names = {row.credential_name for row in stores.state.rows.values()}
        await stores.integrations.delete_connection(connection.id)

        assert await stores.registry.list_for_owner("user-1") == []

        assert stores.state.rows == {}
        for name in names:
            assert await stores.secret(name) is None

    async def test_a_dead_live_activity_token_goes_alone(self, stores):
        registered = await _register_install(stores)

        assert await stores.registry.delete_registration(registered[PushTokenKind.LIVE_ACTIVITY].id)

        kinds = {d.kind for d in await stores.registry.list_for_owner("user-1")}
        assert kinds == {
            PushTokenKind.ALERT,
            PushTokenKind.LIVE_ACTIVITY_START,
            PushTokenKind.WIDGET,
        }

    async def test_a_dead_alert_token_ends_the_device(self, stores):
        registered = await _register_install(stores)

        assert await stores.registry.delete_registration(registered[PushTokenKind.ALERT].id)

        assert await stores.connections() == []
        assert stores.state.rows == {}
        assert not await stores.registry.delete_registration(registered[PushTokenKind.ALERT].id)

    async def test_an_alert_token_replaced_since_is_not_removed(self, stores):
        stored = await stores.registry.upsert(_device("alert-a", install_id=INSTALL))
        [connection] = await stores.connections()
        await stores.credentials.store(
            "user", "user-1", connection.credential_name, "generic", {"device_token": "alert-b"}
        )

        assert not await stores.registry.delete_registration(stored.id)

        assert [d.token for d in await stores.registry.list_for_owner("user-1")] == ["alert-b"]


class TestMissingCredentials:
    async def test_a_device_without_its_alert_credential_is_reported_and_counted(
        self, stores, caplog
    ):
        await _register_install(stores)
        [connection] = await stores.connections()
        await stores.credentials.delete("user", "user-1", connection.credential_name)

        with caplog.at_level(logging.ERROR):
            listed = await stores.registry.list_for_owner("user-1")

        assert PushTokenKind.ALERT not in {d.kind for d in listed}
        assert stores.registry.missing_credentials == 1
        assert any(
            r.levelname == "ERROR" and connection.id in r.getMessage() for r in caplog.records
        )

    async def test_a_missing_token_of_another_kind_drops_its_registration(self, stores, caplog):
        registered = await _register_install(stores)
        widget_row = next(
            row
            for row in stores.state.rows.values()
            if row.id == registered[PushTokenKind.WIDGET].id
        )
        await stores.credentials.delete("user", "user-1", widget_row.credential_name)
        fresh = IntegrationDeviceRegistry(
            stores.integrations, stores.credentials, stores.state, token_kinds=ALL_KINDS
        )

        with caplog.at_level(logging.ERROR):
            listed = await fresh.list_for_owner("user-1")

        assert PushTokenKind.WIDGET not in {d.kind for d in listed}
        assert fresh.missing_credentials == 1
        assert widget_row.credential_name not in stores.state.rows

    async def test_an_install_without_its_alert_token_yet_is_no_error(self, stores, caplog):
        await stores.registry.upsert(_device("w", kind=PushTokenKind.WIDGET, install_id=INSTALL))

        with caplog.at_level(logging.ERROR):
            listed = await stores.registry.list_for_owner("user-1")

        assert [d.kind for d in listed] == [PushTokenKind.WIDGET]
        assert stores.registry.missing_credentials == 0
        assert not caplog.records


class TestThrottles:
    async def test_owed_pushes_skip_a_device_turned_off(self, stores):
        registered = await _register_install(stores)
        widget = registered[PushTokenKind.WIDGET]
        assert await stores.registry.claim_push(widget.id, min_interval_seconds=3600)
        assert not await stores.registry.claim_push(widget.id, min_interval_seconds=3600)
        [connection] = await stores.connections()
        await stores.integrations.save_connection(replace(connection, enabled=False))

        owed = await stores.registry.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=0)

        assert owed == []

    async def test_owed_pushes_carry_the_token(self, stores):
        registered = await _register_install(stores)
        widget = registered[PushTokenKind.WIDGET]
        await stores.registry.claim_push(widget.id, min_interval_seconds=3600)
        await stores.registry.claim_push(widget.id, min_interval_seconds=3600)

        [owed] = await stores.registry.claim_owed_pushes(
            PushTokenKind.WIDGET, min_interval_seconds=0
        )

        assert owed.token == "widget-a"

    async def test_non_alert_tokens_are_read_from_the_store_once(self, stores):
        await _register_install(stores)
        fresh = IntegrationDeviceRegistry(
            stores.integrations, stores.credentials, stores.state, token_kinds=ALL_KINDS
        )
        await fresh.list_for_owner("user-1")
        reads = stores.credentials.reads

        await fresh.list_for_owner("user-1")

        # Only the alert token is read again: another node may have changed it.
        assert stores.credentials.reads == reads + 1

    async def test_tokens_are_counted_per_environment_and_kind(self, stores):
        await _register_install(stores)

        counts = await stores.registry.count_by_environment()

        assert counts == {
            "production": {"alert": 1, "live_activity": 1, "live_activity_start": 1, "widget": 1}
        }


class TestTokensStoredBeforeTheMove:
    async def test_alert_tokens_move_into_the_credential_store(self, stores):
        stores.state.stored = [
            StoredToken(
                id=uuid4(),
                owner_id="user-1",
                platform="ios",
                app_bundle_id="com.niuu.lexi.chat",
                kind="alert",
                environment="sandbox",
                token="old-alert",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]

        assert await stores.registry.move_stored_tokens() == 1

        assert stores.state.stored == []
        [connection] = await stores.connections()
        assert await stores.secret(connection.credential_name) == {"device_token": "old-alert"}
        [alert] = await stores.registry.list_for_owner("user-1")
        assert alert.environment == PushEnvironment.SANDBOX
        assert await stores.registry.move_stored_tokens() == 0

    async def test_a_stored_token_of_another_kind_stops_the_start(self, stores):
        stores.state.stored = [
            StoredToken(
                id=uuid4(),
                owner_id="user-1",
                platform="ios",
                app_bundle_id=None,
                kind="widget",
                environment="production",
                token="old-widget",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]

        with pytest.raises(RuntimeError, match="DELETE FROM device_tokens"):
            await stores.registry.move_stored_tokens()


async def test_ting_does_not_build_push_devices_as_channels(stores):
    await stores.registry.upsert(_device())

    factory = NotificationChannelFactory(stores.integrations, stores.credentials)

    assert await factory.for_owner("user-1") == []


class TestConfiguration:
    def test_the_postgres_registry_is_refused_with_the_remedy(self):
        with pytest.raises(ValueError, match="Remove push.device_registry"):
            PushNotificationConfig(device_registry="postgres")

    @pytest.mark.parametrize("registry", ["auto", "integrations"])
    def test_auto_and_integrations_name_the_one_registry(self, registry):
        config = PushNotificationConfig(device_registry=registry)
        assert config.accepted_token_kinds() == {PushTokenKind.ALERT}

    def test_only_the_gateway_node_takes_every_kind(self, tmp_path):
        from tests.helpers.apns_keys import write_p8

        config = PushNotificationConfig(
            enabled=True,
            apns={
                "team_id": "ABCDE12345",
                "key_id": "KEY1234567",
                "bundle_id": "com.example.app",
                "private_key_path": str(write_p8(tmp_path)),
            },
            gateway={"enabled": True, "guild_url": "http://guild:8080"},
        )
        assert config.accepted_token_kinds() == ALL_KINDS
        assert not config.pushes_alerts_itself()

    def test_a_node_with_a_push_adapter_pushes_alerts_itself(self):
        config = PushNotificationConfig(
            enabled=True, adapter="volundr.adapters.outbound.push_channels.ApnsNotificationChannel"
        )
        assert config.pushes_alerts_itself()
        assert not PushNotificationConfig(enabled=True).pushes_alerts_itself()
