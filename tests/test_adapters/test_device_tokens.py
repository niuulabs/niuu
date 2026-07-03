"""Tests for the PostgreSQL device-token repository adapter."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_device_tokens import PostgresDeviceTokenRepository
from volundr.domain.models import DevicePlatform, DeviceToken


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock()
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


@pytest.fixture
def device_row(device: DeviceToken) -> dict:
    return {
        "id": device.id,
        "owner_id": device.owner_id,
        "platform": device.platform.value,
        "token": device.token,
        "app_bundle_id": device.app_bundle_id,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
    }


async def test_upsert_uses_on_conflict(repository, mock_pool, device):
    await repository.upsert(device)

    sql = mock_pool.execute.call_args[0][0]
    assert "INSERT INTO device_tokens" in sql
    assert "ON CONFLICT (owner_id, token) DO UPDATE" in sql
    assert mock_pool.execute.call_args[0][2] == "user-1"
    assert mock_pool.execute.call_args[0][3] == "ios"


async def test_list_for_owner_returns_devices(repository, mock_pool, device_row):
    mock_pool.fetch.return_value = [device_row]

    result = await repository.list_for_owner("user-1")

    assert len(result) == 1
    assert result[0].platform is DevicePlatform.IOS
    assert result[0].token == "apns-token-abc"
    assert "WHERE owner_id = $1" in mock_pool.fetch.call_args[0][0]


async def test_delete_returns_true_when_removed(repository, mock_pool):
    mock_pool.execute.return_value = "DELETE 1"
    assert await repository.delete("user-1", "tok") is True


async def test_delete_returns_false_when_absent(repository, mock_pool):
    mock_pool.execute.return_value = "DELETE 0"
    assert await repository.delete("user-1", "tok") is False


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
