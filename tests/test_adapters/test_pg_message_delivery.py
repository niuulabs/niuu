"""State monotonicity and claimant ownership in the PostgreSQL adapter."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.pg_message_delivery import PostgresMessageDelivery
from volundr.domain.message_delivery import MessageDeliveryConflictError


@pytest.fixture
def delivery_store():
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.fetchrow = AsyncMock()
    token = uuid4()
    row = {
        "request_id": "r",
        "payload_hash": "a" * 64,
        "claim_token": token,
        "status": "pending",
        "error": None,
    }
    conn.fetchrow.return_value = row
    return PostgresMessageDelivery(pool), conn, pool, row, token


@pytest.mark.asyncio
async def test_claim_retry_same_token_is_owned_but_restart_token_is_not(delivery_store):
    store, _, _, row, token = delivery_store
    sid = uuid4()
    assert (await store.claim(sid, "r", "a" * 64, token)).claimed
    assert not (await store.claim(sid, "r", "a" * 64, uuid4())).claimed
    row["status"] = "delivered"
    assert not (await store.claim(sid, "r", "a" * 64, token)).claimed


@pytest.mark.asyncio
async def test_identity_cannot_be_reused_for_new_payload(delivery_store):
    store, _, _, _, token = delivery_store
    with pytest.raises(MessageDeliveryConflictError, match="different content"):
        await store.claim(uuid4(), "r", "b" * 64, token)


@pytest.mark.asyncio
async def test_settlement_requires_original_claim_and_never_reopens_terminal(delivery_store):
    store, conn, _, row, token = delivery_store
    sid = uuid4()
    with pytest.raises(MessageDeliveryConflictError, match="original dispatcher"):
        await store.settle(sid, "r", uuid4(), "delivered")
    row["status"] = "delivered"
    with pytest.raises(MessageDeliveryConflictError, match="terminal"):
        await store.settle(sid, "r", token, "failed")
    assert (await store.settle(sid, "r", token, "delivered")).status == "delivered"
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_delivery_scrubs_database_unsupported_text(delivery_store):
    store, conn, _, _, token = delivery_store
    result = await store.settle(uuid4(), "r", token, "failed", "bad" + chr(0) + "text")
    assert result.status == "failed"
    assert chr(0) not in result.error
    assert conn.execute.await_args.args[-1] == result.error


@pytest.mark.asyncio
async def test_missing_claim_and_invalid_settlement_are_explicit(delivery_store):
    store, conn, _, _, token = delivery_store
    conn.fetchrow.return_value = None
    with pytest.raises(LookupError):
        await store.settle(uuid4(), "r", token, "failed")
    with pytest.raises(ValueError, match="terminal"):
        await store.settle(uuid4(), "r", token, "pending")


@pytest.mark.asyncio
async def test_lookup_reports_absent_or_recorded_without_exposing_token(delivery_store):
    store, _, pool, row, _ = delivery_store
    pool.fetchrow.return_value = None
    assert await store.get(uuid4(), "r") is None
    pool.fetchrow.return_value = row
    result = await store.get(uuid4(), "r")
    assert result.status == "pending"
    assert not hasattr(result, "claim_token")
