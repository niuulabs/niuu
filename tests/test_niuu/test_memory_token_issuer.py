"""Tests for MemoryTokenIssuer — in-memory JWT issuer for dev/test."""

from __future__ import annotations

import time

import jwt
import pytest

from niuu.adapters.memory_token_issuer import MemoryTokenIssuer

TEST_SIGNING_KEY = "test-signing-key-at-least-32-bytes-long"
TEST_SUBJECT_KEY = "subject-signing-key-at-least-32-bytes"


@pytest.fixture
def issuer() -> MemoryTokenIssuer:
    return MemoryTokenIssuer(signing_key=TEST_SIGNING_KEY)


@pytest.mark.asyncio
async def test_issue_token_returns_issued_token(issuer: MemoryTokenIssuer):
    result = await issuer.issue_token(subject_token="some-opaque-token", name="my-pat")
    assert result.raw_token
    assert result.token_id
    assert result.expires_at > int(time.time())


@pytest.mark.asyncio
async def test_issue_token_with_jwt_subject(issuer: MemoryTokenIssuer):
    """When subject_token is a valid JWT, sub is extracted from it."""
    subject_jwt = jwt.encode({"sub": "user-abc"}, TEST_SUBJECT_KEY, algorithm="HS256")
    result = await issuer.issue_token(subject_token=subject_jwt, name="pat-1")
    # Decode issued token and verify sub propagated correctly
    claims = jwt.decode(result.raw_token, TEST_SIGNING_KEY, algorithms=["HS256"])
    assert claims["sub"] == "user-abc"


@pytest.mark.asyncio
async def test_issue_token_with_opaque_subject_uses_hash(issuer: MemoryTokenIssuer):
    """When subject_token is not a JWT, its hash is used as sub."""
    result = await issuer.issue_token(subject_token="opaque-session-token", name="pat-2")
    claims = jwt.decode(result.raw_token, TEST_SIGNING_KEY, algorithms=["HS256"])
    # sub should be a 16-char hex hash
    assert len(claims["sub"]) == 16


@pytest.mark.asyncio
async def test_issue_token_with_empty_subject_uses_dev(issuer: MemoryTokenIssuer):
    """Empty subject_token falls back to 'dev' as sub."""
    result = await issuer.issue_token(subject_token="", name="dev-pat")
    claims = jwt.decode(result.raw_token, TEST_SIGNING_KEY, algorithms=["HS256"])
    assert claims["sub"] == "dev"


@pytest.mark.asyncio
async def test_issue_token_type_is_pat(issuer: MemoryTokenIssuer):
    result = await issuer.issue_token(subject_token="x", name="my-pat")
    claims = jwt.decode(result.raw_token, TEST_SIGNING_KEY, algorithms=["HS256"])
    assert claims["type"] == "pat"
    assert claims["name"] == "my-pat"


@pytest.mark.asyncio
async def test_issue_token_ttl_days(issuer: MemoryTokenIssuer):
    result = await issuer.issue_token(subject_token="x", name="short-lived", ttl_days=7)
    claims = jwt.decode(result.raw_token, TEST_SIGNING_KEY, algorithms=["HS256"])
    expected_exp = claims["iat"] + 7 * 86400
    assert claims["exp"] == expected_exp


@pytest.mark.asyncio
async def test_close_is_noop(issuer: MemoryTokenIssuer):
    await issuer.close()  # Should not raise


def test_requires_signing_key():
    with pytest.raises(ValueError, match="signing_key"):
        MemoryTokenIssuer(signing_key="")


def test_token_id_unique():
    import asyncio

    issuer = MemoryTokenIssuer(signing_key=TEST_SIGNING_KEY)

    async def _gather():
        return await asyncio.gather(
            issuer.issue_token(subject_token="x", name="a"),
            issuer.issue_token(subject_token="x", name="b"),
        )

    tokens = asyncio.run(_gather())
    assert tokens[0].token_id != tokens[1].token_id
