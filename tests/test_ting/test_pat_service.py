"""Tests for PATService with mocked repository and MemoryTokenIssuer."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import jwt
import pytest

from niuu.adapters.memory_token_issuer import MemoryTokenIssuer
from niuu.domain.models import PersonalAccessToken
from niuu.domain.services.pat import PATService
from niuu.domain.services.pat_validator import PATValidator
from niuu.ports.pat_repository import PATRepository

SIGNING_KEY = "test-secret-key-for-pats-minimum-32-bytes!"


class FakePATRepository(PATRepository):
    """In-memory fake for testing."""

    def __init__(self) -> None:
        self.store: dict[str, PersonalAccessToken] = {}
        self.hashes: dict[str, str] = {}
        self.last_used_touches: list[str] = []

    async def create(self, owner_id: str, name: str, token_hash: str) -> PersonalAccessToken:
        pat = PersonalAccessToken(
            id=uuid4(),
            owner_id=owner_id,
            name=name,
            created_at=datetime.now(UTC),
        )
        self.store[str(pat.id)] = pat
        self.hashes[str(pat.id)] = token_hash
        return pat

    async def list(self, owner_id: str) -> list[PersonalAccessToken]:
        return [p for p in self.store.values() if p.owner_id == owner_id]

    async def get(self, pat_id, owner_id: str) -> PersonalAccessToken | None:
        pat = self.store.get(str(pat_id))
        if pat and pat.owner_id == owner_id:
            return pat
        return None

    async def delete(self, pat_id, owner_id: str) -> str | None:
        key = str(pat_id)
        pat = self.store.get(key)
        if pat and pat.owner_id == owner_id:
            token_hash = self.hashes.pop(key, None)
            del self.store[key]
            return token_hash
        return None

    async def exists_by_hash(self, token_hash: str) -> bool:
        return token_hash in self.hashes.values()

    async def touch_last_used(self, token_hash: str) -> None:
        self.last_used_touches.append(token_hash)


@pytest.fixture
def fake_repo() -> FakePATRepository:
    return FakePATRepository()


@pytest.fixture
def issuer() -> MemoryTokenIssuer:
    return MemoryTokenIssuer(signing_key=SIGNING_KEY)


@pytest.fixture
def service(fake_repo: FakePATRepository, issuer: MemoryTokenIssuer) -> PATService:
    return PATService(repo=fake_repo, token_issuer=issuer, ttl_days=30)


class TestCreate:
    @pytest.mark.asyncio
    async def test_returns_pat_and_raw_jwt(self, service: PATService):
        pat, raw_jwt = await service.create("user-1", "my-token", subject_token="fake")

        assert isinstance(pat, PersonalAccessToken)
        assert pat.owner_id == "user-1"
        assert pat.name == "my-token"
        assert isinstance(raw_jwt, str)
        assert len(raw_jwt) > 0

    @pytest.mark.asyncio
    async def test_jwt_payload_contains_required_fields(self, service: PATService):
        _, raw_jwt = await service.create("user-1", "ci-token", subject_token="fake")

        payload = jwt.decode(raw_jwt, SIGNING_KEY, algorithms=["HS256"])

        assert payload["type"] == "pat"
        assert payload["name"] == "ci-token"
        assert "jti" in payload
        assert "iat" in payload
        assert "exp" in payload

    @pytest.mark.asyncio
    async def test_jwt_expiry_uses_ttl_days(self, service: PATService):
        _, raw_jwt = await service.create("user-1", "tok", subject_token="fake")

        payload = jwt.decode(raw_jwt, SIGNING_KEY, algorithms=["HS256"])
        ttl_seconds = payload["exp"] - payload["iat"]

        assert ttl_seconds == 30 * 86400

    @pytest.mark.asyncio
    async def test_stores_sha256_hash(self, service: PATService, fake_repo: FakePATRepository):
        pat, raw_jwt = await service.create("user-1", "tok", subject_token="fake")
        expected_hash = hashlib.sha256(raw_jwt.encode()).hexdigest()

        assert fake_repo.hashes[str(pat.id)] == expected_hash

    @pytest.mark.asyncio
    async def test_each_token_has_unique_jti(self, service: PATService):
        _, jwt1 = await service.create("user-1", "tok-1", subject_token="fake")
        _, jwt2 = await service.create("user-1", "tok-2", subject_token="fake")

        payload1 = jwt.decode(jwt1, SIGNING_KEY, algorithms=["HS256"])
        payload2 = jwt.decode(jwt2, SIGNING_KEY, algorithms=["HS256"])

        assert payload1["jti"] != payload2["jti"]

    @pytest.mark.asyncio
    async def test_persists_to_repo(self, service: PATService, fake_repo: FakePATRepository):
        await service.create("user-1", "tok", subject_token="fake")

        assert len(fake_repo.store) == 1


class TestList:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self, service: PATService):
        result = await service.list("user-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_pats_for_owner(self, service: PATService):
        await service.create("user-1", "tok-a", subject_token="fake")
        await service.create("user-1", "tok-b", subject_token="fake")
        await service.create("user-2", "tok-c", subject_token="fake")

        result = await service.list("user-1")

        assert len(result) == 2
        names = {p.name for p in result}
        assert names == {"tok-a", "tok-b"}

    @pytest.mark.asyncio
    async def test_isolates_by_owner(self, service: PATService):
        await service.create("user-1", "tok", subject_token="fake")

        result = await service.list("user-2")

        assert result == []


class TestRevoke:
    @pytest.mark.asyncio
    async def test_returns_true_when_deleted(self, service: PATService):
        pat, _ = await service.create("user-1", "tok", subject_token="fake")

        result = await service.revoke(pat.id, "user-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self, service: PATService):
        result = await service.revoke(uuid4(), "user-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_removes_from_repo(self, service: PATService, fake_repo: FakePATRepository):
        pat, _ = await service.create("user-1", "tok", subject_token="fake")
        await service.revoke(pat.id, "user-1")

        assert len(fake_repo.store) == 0

    @pytest.mark.asyncio
    async def test_cannot_revoke_other_owners_token(self, service: PATService):
        pat, _ = await service.create("user-1", "tok", subject_token="fake")

        result = await service.revoke(pat.id, "user-2")

        assert result is False

    @pytest.mark.asyncio
    async def test_logs_on_successful_revoke(self, service: PATService):
        pat, _ = await service.create("user-1", "tok", subject_token="fake")

        with patch("niuu.domain.services.pat.logger") as mock_logger:
            await service.revoke(pat.id, "user-1")
            mock_logger.info.assert_called_once()
            assert "revoked" in mock_logger.info.call_args[0][0].lower()


class TestDefaultTtl:
    @pytest.mark.asyncio
    async def test_default_ttl_is_365_days(
        self,
        fake_repo: FakePATRepository,
        issuer: MemoryTokenIssuer,
    ):
        service = PATService(repo=fake_repo, token_issuer=issuer)
        _, raw_jwt_str = await service.create("user-1", "tok", subject_token="fake")

        payload = jwt.decode(raw_jwt_str, SIGNING_KEY, algorithms=["HS256"])
        ttl_seconds = payload["exp"] - payload["iat"]

        assert ttl_seconds == 365 * 86400


class TestValidatorIntegration:
    @pytest.mark.asyncio
    async def test_revoke_calls_validator_invalidate_by_hash(
        self,
        fake_repo: FakePATRepository,
        issuer: MemoryTokenIssuer,
    ):
        mock_validator = MagicMock(spec=PATValidator)
        service = PATService(
            repo=fake_repo,
            token_issuer=issuer,
            validator=mock_validator,
        )

        pat, _ = await service.create("user-1", "tok", subject_token="fake")
        token_hash = fake_repo.hashes[str(pat.id)]

        await service.revoke(pat.id, "user-1")

        mock_validator.invalidate_by_hash.assert_called_once_with(token_hash)

    @pytest.mark.asyncio
    async def test_revoke_without_validator_does_not_fail(
        self,
        fake_repo: FakePATRepository,
        issuer: MemoryTokenIssuer,
    ):
        service = PATService(repo=fake_repo, token_issuer=issuer, validator=None)
        pat, _ = await service.create("user-1", "tok", subject_token="fake")

        result = await service.revoke(pat.id, "user-1")

        assert result is True
