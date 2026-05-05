"""Tests for PATValidator — revocation enforcement with in-memory cache."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest

from niuu.domain.services.pat_validator import PATValidator

SIGNING_KEY = "test-signing-key-for-validator-at-least-32"


def _make_pat_jwt(
    sub: str = "user-1",
    name: str = "test-token",
    jti: str = "jti-abc",
    ttl_days: int = 365,
) -> str:
    """Create a PAT JWT for testing."""
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "type": "pat",
        "jti": jti,
        "name": name,
        "iat": now,
        "exp": now + timedelta(days=ttl_days),
    }
    return jwt.encode(payload, SIGNING_KEY, algorithm="HS256")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.exists_by_hash = AsyncMock(return_value=True)
    repo.touch_last_used = AsyncMock()
    return repo


@pytest.fixture
def validator(mock_repo: AsyncMock) -> PATValidator:
    return PATValidator(
        repo=mock_repo,
        cache_ttl=300,
        revoked_cache_ttl=60,
    )


class TestIsValid:
    async def test_valid_pat_returns_true(self, validator: PATValidator, mock_repo: AsyncMock):
        token = _make_pat_jwt()
        result = await validator.is_valid(token)
        assert result is True
        mock_repo.exists_by_hash.assert_called_once_with(_hash(token))

    async def test_revoked_pat_returns_false(self, validator: PATValidator, mock_repo: AsyncMock):
        mock_repo.exists_by_hash.return_value = False
        token = _make_pat_jwt()
        result = await validator.is_valid(token)
        assert result is False

    async def test_non_pat_jwt_passes_through(self, validator: PATValidator, mock_repo: AsyncMock):
        """JWTs without type=pat are not checked."""
        payload = {"sub": "user-1", "type": "session"}
        token = jwt.encode(payload, SIGNING_KEY, algorithm="HS256")
        result = await validator.is_valid(token)
        assert result is True
        mock_repo.exists_by_hash.assert_not_called()

    async def test_invalid_jwt_passes_through(self, validator: PATValidator, mock_repo: AsyncMock):
        """Garbage tokens pass through (Envoy will reject them)."""
        result = await validator.is_valid("not-a-jwt")
        assert result is True
        mock_repo.exists_by_hash.assert_not_called()

    async def test_jwt_without_type_passes_through(
        self,
        validator: PATValidator,
        mock_repo: AsyncMock,
    ):
        """JWTs without a type claim pass through."""
        payload = {"sub": "user-1"}
        token = jwt.encode(payload, SIGNING_KEY, algorithm="HS256")
        result = await validator.is_valid(token)
        assert result is True
        mock_repo.exists_by_hash.assert_not_called()


class TestCaching:
    async def test_caches_valid_result(self, validator: PATValidator, mock_repo: AsyncMock):
        token = _make_pat_jwt()
        await validator.is_valid(token)
        await validator.is_valid(token)  # second call
        mock_repo.exists_by_hash.assert_called_once()  # only one DB call

    async def test_caches_revoked_result(self, validator: PATValidator, mock_repo: AsyncMock):
        mock_repo.exists_by_hash.return_value = False
        token = _make_pat_jwt()
        await validator.is_valid(token)
        await validator.is_valid(token)  # second call
        mock_repo.exists_by_hash.assert_called_once()

    async def test_cache_expires(self, mock_repo: AsyncMock):
        validator = PATValidator(
            repo=mock_repo,
            cache_ttl=0.01,
            revoked_cache_ttl=0.01,
        )
        token = _make_pat_jwt()

        await validator.is_valid(token)
        assert mock_repo.exists_by_hash.call_count == 1

        time.sleep(0.02)  # wait for cache to expire
        await validator.is_valid(token)
        assert mock_repo.exists_by_hash.call_count == 2

    async def test_invalidate_clears_specific_token(
        self,
        validator: PATValidator,
        mock_repo: AsyncMock,
    ):
        token = _make_pat_jwt()
        await validator.is_valid(token)
        assert mock_repo.exists_by_hash.call_count == 1

        validator.invalidate(token)
        await validator.is_valid(token)
        assert mock_repo.exists_by_hash.call_count == 2

    async def test_clear_cache_resets_all(
        self,
        validator: PATValidator,
        mock_repo: AsyncMock,
    ):
        t1 = _make_pat_jwt(jti="jti-1")
        t2 = _make_pat_jwt(jti="jti-2")
        await validator.is_valid(t1)
        await validator.is_valid(t2)
        assert mock_repo.exists_by_hash.call_count == 2

        validator.clear_cache()
        await validator.is_valid(t1)
        await validator.is_valid(t2)
        assert mock_repo.exists_by_hash.call_count == 4


class TestDifferentTokenTypes:
    async def test_different_pats_cached_independently(
        self,
        validator: PATValidator,
        mock_repo: AsyncMock,
    ):
        t1 = _make_pat_jwt(jti="jti-1")
        t2 = _make_pat_jwt(jti="jti-2")

        await validator.is_valid(t1)
        await validator.is_valid(t2)

        assert mock_repo.exists_by_hash.call_count == 2
        calls = [c[0][0] for c in mock_repo.exists_by_hash.call_args_list]
        assert _hash(t1) in calls
        assert _hash(t2) in calls


class TestInvalidateByHash:
    async def test_invalidate_by_hash_clears_cached_entry(
        self,
        validator: PATValidator,
        mock_repo: AsyncMock,
    ):
        """After invalidate_by_hash, the next is_valid call re-queries the DB."""
        token = _make_pat_jwt()
        await validator.is_valid(token)
        assert mock_repo.exists_by_hash.call_count == 1

        validator.invalidate_by_hash(_hash(token))
        await validator.is_valid(token)
        assert mock_repo.exists_by_hash.call_count == 2

    async def test_invalidate_by_hash_accepts_string(
        self,
        validator: PATValidator,
        mock_repo: AsyncMock,
    ):
        """invalidate_by_hash accepts a plain string hash."""
        token = _make_pat_jwt()
        await validator.is_valid(token)

        # Should not raise
        validator.invalidate_by_hash(_hash(token))

    async def test_invalidate_by_hash_noop_for_unknown(
        self,
        validator: PATValidator,
    ):
        """invalidate_by_hash does not raise for unknown hashes."""
        validator.invalidate_by_hash("nonexistent-hash")
