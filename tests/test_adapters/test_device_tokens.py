"""Tests for the PostgreSQL device-token repository adapter."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_device_tokens import PostgresDeviceTokenRepository
from volundr.domain.models import DevicePlatform, DeviceToken
from volundr.domain.push import PushEnvironment, PushTokenKind


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetchval = AsyncMock()
    return pool


@pytest.fixture
def repository(mock_pool) -> PostgresDeviceTokenRepository:
    return PostgresDeviceTokenRepository(mock_pool)


@pytest.fixture
def device() -> DeviceToken:
    return DeviceToken(
        owner_id="user-1",
        platform=DevicePlatform.IOS,
        token="apns-token-abc",
        app_bundle_id="com.niuu.forge",
    )


def row_for(device: DeviceToken, **overrides) -> dict:
    row = {
        "id": device.id,
        "owner_id": device.owner_id,
        "platform": device.platform.value,
        "token": device.token,
        "app_bundle_id": device.app_bundle_id,
        "kind": device.kind.value,
        "environment": (device.environment or PushEnvironment.PRODUCTION).value,
        "activity_id": device.activity_id,
        "last_push_at": None,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
    }
    row.update(overrides)
    return row


@pytest.fixture
def device_row(device: DeviceToken) -> dict:
    return row_for(device)


async def test_upsert_is_keyed_by_token_and_kind_and_moves_the_owner(
    repository, mock_pool, device, device_row
):
    mock_pool.fetchrow.return_value = device_row

    stored = await repository.upsert(device)

    sql, *args = mock_pool.fetchrow.call_args[0]
    assert "INSERT INTO device_tokens" in sql
    assert "ON CONFLICT (token, kind) DO UPDATE" in sql
    assert "owner_id = EXCLUDED.owner_id" in sql
    assert args[1:8] == [
        "user-1",
        "ios",
        "apns-token-abc",
        "com.niuu.forge",
        "alert",
        "production",
        None,
    ]
    assert stored.id == device.id and stored.kind is PushTokenKind.ALERT
    mock_pool.execute.assert_not_called()


async def test_upsert_returns_the_stored_row_of_an_existing_token(repository, mock_pool, device):
    existing_id = uuid4()
    mock_pool.fetchrow.return_value = row_for(device, id=existing_id, environment="sandbox")

    stored = await repository.upsert(device)

    assert stored.id == existing_id
    assert stored.environment is PushEnvironment.SANDBOX


async def test_a_live_activity_token_replaces_the_activitys_earlier_token(repository, mock_pool):
    device = DeviceToken(
        owner_id="user-1",
        platform=DevicePlatform.IOS,
        token="la-new",
        kind=PushTokenKind.LIVE_ACTIVITY,
        environment=PushEnvironment.SANDBOX,
        activity_id="act-1",
    )
    mock_pool.fetchrow.return_value = row_for(device)

    await repository.upsert(device)

    sql, *args = mock_pool.execute.call_args[0]
    assert sql.startswith("DELETE FROM device_tokens")
    assert "activity_id = $3 AND token <> $4" in sql
    assert args == ["user-1", "live_activity", "act-1", "la-new"]


async def test_a_live_activity_token_without_an_activity_replaces_nothing(repository, mock_pool):
    device = DeviceToken(
        owner_id="user-1",
        platform=DevicePlatform.IOS,
        token="la-new",
        kind=PushTokenKind.LIVE_ACTIVITY,
    )
    mock_pool.fetchrow.return_value = row_for(device)

    await repository.upsert(device)

    mock_pool.execute.assert_not_called()


async def test_list_for_owner_returns_devices(repository, mock_pool, device_row):
    mock_pool.fetch.return_value = [device_row]

    result = await repository.list_for_owner("user-1")

    assert len(result) == 1
    assert result[0].platform is DevicePlatform.IOS
    assert result[0].token == "apns-token-abc"
    assert result[0].kind is PushTokenKind.ALERT
    assert result[0].environment is PushEnvironment.PRODUCTION
    assert "WHERE owner_id = $1" in mock_pool.fetch.call_args[0][0]
    assert len(mock_pool.fetch.call_args[0]) == 2


async def test_list_for_owner_filters_by_kind(repository, mock_pool, device_row):
    mock_pool.fetch.return_value = [device_row]

    await repository.list_for_owner(
        "user-1", kinds=[PushTokenKind.LIVE_ACTIVITY, PushTokenKind.WIDGET]
    )

    sql, owner, kinds = mock_pool.fetch.call_args[0]
    assert "kind = ANY($2::text[])" in sql
    assert (owner, kinds) == ("user-1", ["live_activity", "widget"])


async def test_delete_returns_true_when_removed(repository, mock_pool):
    mock_pool.execute.return_value = "DELETE 1"
    deleted = await repository.delete("user-1", "tok")
    assert deleted is True
    assert mock_pool.execute.call_args[0][1:] == ("user-1", "tok")


async def test_delete_returns_true_when_several_kinds_were_removed(repository, mock_pool):
    mock_pool.execute.return_value = "DELETE 2"
    assert await repository.delete("user-1", "tok") is True


async def test_delete_returns_false_when_absent(repository, mock_pool):
    mock_pool.execute.return_value = "DELETE 0"
    deleted = await repository.delete("user-1", "tok")
    assert deleted is False


async def test_delete_one_kind(repository, mock_pool):
    mock_pool.execute.return_value = "DELETE 1"

    assert await repository.delete("user-1", "tok", kind=PushTokenKind.WIDGET) is True

    sql, *args = mock_pool.execute.call_args[0]
    assert "AND kind = $3" in sql
    assert args == ["user-1", "tok", "widget"]


async def test_delete_registration_by_id(repository, mock_pool, device):
    mock_pool.execute.side_effect = ["DELETE 1", "DELETE 0"]

    assert await repository.delete_registration(device.id) is True
    assert await repository.delete_registration(device.id) is False
    assert mock_pool.execute.call_args[0] == ("DELETE FROM device_tokens WHERE id = $1", device.id)


async def test_claim_push_when_the_interval_elapsed(repository, mock_pool, device):
    mock_pool.fetchval.return_value = device.id

    assert await repository.claim_push(device.id, min_interval_seconds=300) is True

    sql, device_id, interval = mock_pool.fetchval.call_args[0]
    assert "SET last_push_at = now(), push_due = FALSE" in sql
    assert "last_push_at IS NULL OR last_push_at <= now() - make_interval" in sql
    assert (device_id, interval) == (device.id, 300.0)
    mock_pool.execute.assert_not_called()


async def test_claim_push_inside_the_interval_marks_the_push_as_owed(repository, mock_pool, device):
    mock_pool.fetchval.return_value = None

    assert await repository.claim_push(device.id, min_interval_seconds=300) is False

    assert mock_pool.execute.call_args[0] == (
        "UPDATE device_tokens SET push_due = TRUE WHERE id = $1",
        device.id,
    )


async def test_release_push_makes_the_token_claimable_and_owed(repository, mock_pool, device):
    await repository.release_push(device.id)

    sql, device_id = mock_pool.execute.call_args[0]
    assert sql == "UPDATE device_tokens SET last_push_at = NULL, push_due = TRUE WHERE id = $1"
    assert device_id == device.id


async def test_claim_owed_pushes(repository, mock_pool, device):
    pushed = datetime(2026, 10, 2, 6, 40, tzinfo=UTC)
    mock_pool.fetch.return_value = [row_for(device, kind="widget", last_push_at=pushed)]

    claimed = await repository.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=300)

    sql, kind, interval = mock_pool.fetch.call_args[0]
    assert "WHERE kind = $1 AND push_due AND" in sql
    assert "RETURNING *" in sql
    assert (kind, interval) == ("widget", 300.0)
    assert claimed[0].kind is PushTokenKind.WIDGET
    assert claimed[0].last_push_at == pushed


async def test_row_to_device_naive_datetime(repository, device_row):
    device_row["created_at"] = datetime(2024, 1, 1, 12, 0, 0)
    device_row["updated_at"] = datetime(2024, 1, 1, 12, 0, 0)

    result = repository._row_to_device(device_row)

    assert result.created_at.tzinfo == UTC
    assert result.updated_at.tzinfo == UTC
    assert result.id == device_row["id"]


def test_device_token_id_defaults_unique():
    a = DeviceToken(owner_id="u", platform=DevicePlatform.WEB, token="t1")
    b = DeviceToken(owner_id="u", platform=DevicePlatform.WEB, token="t2")
    assert a.id != b.id
    assert isinstance(a.id, type(uuid4()))


def test_device_token_defaults_to_an_alert_token_of_no_stated_environment():
    token = DeviceToken(owner_id="u", platform=DevicePlatform.IOS, token="t")
    assert token.kind is PushTokenKind.ALERT
    assert token.environment is None
    assert token.activity_id is None and token.last_push_at is None


async def test_count_by_environment_groups_environment_and_kind(repository, mock_pool):
    mock_pool.fetch = AsyncMock(
        return_value=[
            {"environment": "production", "kind": "alert", "n": 1},
            {"environment": "production", "kind": "live_activity", "n": 3},
            {"environment": "sandbox", "kind": "alert", "n": 1},
        ]
    )

    counts = await repository.count_by_environment()

    assert counts == {"production": {"alert": 1, "live_activity": 3}, "sandbox": {"alert": 1}}
    assert "GROUP BY" in mock_pool.fetch.call_args[0][0]
