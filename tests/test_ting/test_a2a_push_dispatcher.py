from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from a2a.types import AuthenticationInfo, TaskPushNotificationConfig
from cryptography.fernet import Fernet

from ting.adapters.a2a_push_dispatcher import A2APushDispatcher, callback_origin
from ting.adapters.postgres_a2a_push import PostgresA2APushConfigRepository
from ting.ports.a2a_push import A2APushDelivery


def _config() -> TaskPushNotificationConfig:
    return TaskPushNotificationConfig(
        task_id="task-1",
        id="callback-1",
        url="https://resident.example/a2a/push",
        authentication=AuthenticationInfo(scheme="Bearer"),
    )


def _delivery() -> A2APushDelivery:
    return A2APushDelivery(
        task_id="task-1",
        config_id="callback-1",
        owner_id="user-1",
        config=_config(),
        payload={"task": {"id": "task-1", "status": {"state": "TASK_STATE_FAILED"}}},
        delivery_version="version-1",
        attempt_count=0,
    )


def _dispatcher(repo: MagicMock, auth: MagicMock | None = None) -> A2APushDispatcher:
    auth = auth or MagicMock()
    auth.headers.return_value = {"Authorization": "Bearer workload-token"}
    auth.invalidate.return_value = True
    return A2APushDispatcher(
        repo=repo,
        auth=auth,
        allowed_callback_origins=["https://resident.example"],
        timeout_seconds=10.0,
        poll_seconds=1.0,
        retry_initial_seconds=2.0,
        retry_max_seconds=300.0,
        claim_limit=20,
        lease_seconds=30.0,
        max_url_chars=2048,
        max_credential_chars=8192,
        max_configs_page_size=100,
    )


def test_callback_origin_requires_allowlisted_https_shape() -> None:
    assert callback_origin("https://Resident.Example:443/a2a/push") == "https://resident.example"
    with pytest.raises(ValueError, match="HTTPS"):
        callback_origin("http://resident.example/a2a/push")
    with pytest.raises(ValueError, match="userinfo"):
        callback_origin("https://user:password@resident.example/a2a/push")
    with pytest.raises(ValueError, match="fragment"):
        callback_origin("https://resident.example/a2a/push#secret")


@pytest.mark.asyncio
async def test_push_delivery_uses_workload_bearer_and_marks_delivered() -> None:
    repo = MagicMock()
    repo.mark_delivered = AsyncMock()
    repo.retry_later = AsyncMock()
    dispatcher = _dispatcher(repo)
    with respx.mock:
        route = respx.post("https://resident.example/a2a/push").mock(
            return_value=httpx.Response(204)
        )
        await dispatcher._deliver(_delivery())

    assert route.called
    assert route.calls[0].request.headers["Authorization"] == "Bearer workload-token"
    assert "X-A2A-Notification-Token" not in route.calls[0].request.headers
    repo.mark_delivered.assert_awaited_once()
    repo.retry_later.assert_not_awaited()
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_push_delivery_refreshes_rejected_workload_identity_once() -> None:
    repo = MagicMock()
    repo.mark_delivered = AsyncMock()
    repo.retry_later = AsyncMock()
    auth = MagicMock()
    auth.headers.side_effect = [
        {"Authorization": "Bearer expired-token"},
        {"Authorization": "Bearer fresh-token"},
    ]
    auth.invalidate.return_value = True
    dispatcher = _dispatcher(repo, auth)
    with respx.mock:
        route = respx.post("https://resident.example/a2a/push").mock(
            side_effect=[httpx.Response(401), httpx.Response(204)]
        )
        await dispatcher._deliver(_delivery())

    assert route.call_count == 2
    assert route.calls[1].request.headers["Authorization"] == "Bearer fresh-token"
    auth.invalidate.assert_called_once_with()
    repo.mark_delivered.assert_awaited_once()
    repo.retry_later.assert_not_awaited()
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_push_delivery_retries_without_dropping_failed_event() -> None:
    repo = MagicMock()
    repo.mark_delivered = AsyncMock()
    repo.retry_later = AsyncMock()
    dispatcher = _dispatcher(repo)
    with respx.mock:
        respx.post("https://resident.example/a2a/push").mock(return_value=httpx.Response(503))
        await dispatcher._deliver(_delivery())

    repo.retry_later.assert_awaited_once()
    repo.mark_delivered.assert_not_awaited()
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_repository_encrypts_callback_config_before_storage() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    repo = PostgresA2APushConfigRepository(
        pool,
        Fernet.generate_key().decode(),
        max_error_chars=2000,
    )

    saved = await repo.save_config(
        task_id="task-1",
        owner_id="user-1",
        config=_config(),
    )

    encrypted = pool.execute.await_args.args[-1]
    assert isinstance(encrypted, bytes)
    assert b"resident.example" not in encrypted
    assert repo._decrypt(encrypted) == saved


def test_push_config_rejects_inline_credentials() -> None:
    repo = MagicMock()
    dispatcher = _dispatcher(repo)
    config = _config()
    config.token = "legacy-token"

    with pytest.raises(ValueError, match="inline credentials"):
        dispatcher.validate_config(config)
