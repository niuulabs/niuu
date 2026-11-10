"""Tests for LinearGraphQLClient and CacheEntry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from niuu.adapters.linear import GraphQLError, LinearGraphQLClient
from niuu.domain.models import CacheEntry

# ---------------------------------------------------------------------------
# CacheEntry tests
# ---------------------------------------------------------------------------


class TestCacheEntry:
    def test_not_expired(self):
        entry = CacheEntry("hello", ttl=60.0)
        assert not entry.expired
        assert entry.value == "hello"

    def test_expired(self):
        entry = CacheEntry("gone", ttl=60.0)
        entry.expires_at = 0.0
        assert entry.expired


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> LinearGraphQLClient:
    return LinearGraphQLClient(
        api_key="test-key",
        api_url="https://test.linear.app/graphql",
        cache_ttl=30.0,
        max_retries=3,
    )


def _mock_response(json_data: dict, status_code: int = 200, headers: dict | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.headers = headers or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Query tests
# ---------------------------------------------------------------------------


class TestQuery:
    async def test_successful_query(self):
        client = _make_client()
        client._client = AsyncMock()
        client._client.post.return_value = _mock_response({"data": {"viewer": {"id": "1"}}})

        result = await client.query("query { viewer { id } }")
        assert result == {"viewer": {"id": "1"}}
        client._client.post.assert_called_once()

    async def test_query_with_variables(self):
        client = _make_client()
        client._client = AsyncMock()
        client._client.post.return_value = _mock_response({"data": {"issue": {"id": "abc"}}})

        result = await client.query(
            "query($id: String!) { issue(id: $id) { id } }",
            variables={"id": "abc"},
        )
        assert result == {"issue": {"id": "abc"}}

        call_kwargs = client._client.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["variables"] == {"id": "abc"}

    async def test_graphql_error_handling(self):
        client = _make_client()
        client._client = AsyncMock()
        client._client.post.return_value = _mock_response(
            {"errors": [{"message": "Field not found"}]}
        )

        with pytest.raises(GraphQLError, match="Field not found"):
            await client.query("query { bad }")

    async def test_http_error_handling(self):
        client = _make_client()
        client._client = AsyncMock()
        client._client.post.return_value = _mock_response({}, status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            await client.query("query { fail }")

    async def test_429_rate_limit_retry(self):
        """First call returns 429, second returns 200."""
        client = LinearGraphQLClient(
            api_key="test-key",
            api_url="https://test.linear.app/graphql",
            cache_ttl=30.0,
            max_retries=3,
        )
        client._client = AsyncMock()

        rate_limited = _mock_response({}, status_code=429, headers={"Retry-After": "0"})
        # 429 response: raise_for_status raises, but we handle it before that
        # The code checks status_code == 429 before raise_for_status
        rate_limited.raise_for_status = MagicMock()  # won't be called on 429 with retries left

        success = _mock_response({"data": {"ok": True}})

        client._client.post.side_effect = [rate_limited, success]

        result = await client.query("query { ok }")
        assert result == {"ok": True}
        assert client._client.post.call_count == 2

    async def test_max_retries_exhausted(self):
        """All retries return 429 — should raise HTTPStatusError."""
        client = LinearGraphQLClient(
            api_key="test-key",
            api_url="https://test.linear.app/graphql",
            cache_ttl=30.0,
            max_retries=2,
        )
        client._client = AsyncMock()

        def make_429():
            resp = _mock_response({}, status_code=429, headers={"Retry-After": "0"})
            return resp

        # max_retries=2 means 3 total attempts (0, 1, 2)
        # On last attempt with 429, it calls raise_for_status which raises
        client._client.post.side_effect = [make_429(), make_429(), make_429()]

        with pytest.raises(httpx.HTTPStatusError):
            await client.query("query { rate_limited }")


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestCache:
    def test_get_cached_miss(self):
        client = _make_client()
        assert client.get_cached("nonexistent") is None

    def test_get_cached_hit(self):
        client = _make_client()
        client.set_cached("key", {"data": 42})
        assert client.get_cached("key") == {"data": 42}

    def test_get_cached_expired(self):
        client = _make_client()
        client.set_cached("key", "value")
        client._cache["key"].expires_at = 0.0
        assert client.get_cached("key") is None

    def test_set_cached_custom_ttl(self):
        client = _make_client()
        client.set_cached("key", "val", ttl=120.0)
        entry = client._cache["key"]
        assert entry.value == "val"
        assert not entry.expired

    def test_invalidate_cache_by_prefix(self):
        client = _make_client()
        client.set_cached("projects:all", [1, 2])
        client.set_cached("projects:detail:1", {"id": 1})
        client.set_cached("issues:all", [3, 4])

        client.invalidate_cache("projects")

        assert client.get_cached("projects:all") is None
        assert client.get_cached("projects:detail:1") is None
        assert client.get_cached("issues:all") == [3, 4]


# ---------------------------------------------------------------------------
# Close test
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close(self):
        client = _make_client()
        client._client = AsyncMock()

        await client.close()

        client._client.aclose.assert_called_once()


class TestCredentialHeader:
    def test_api_key_is_sent_raw(self):
        client = LinearGraphQLClient(api_key="lin_api_key")
        assert client._client.headers["Authorization"] == "lin_api_key"

    def test_access_token_is_sent_as_bearer(self):
        client = LinearGraphQLClient(access_token="oauth-token")
        assert client._client.headers["Authorization"] == "Bearer oauth-token"

    def test_a_token_prefix_is_never_sniffed(self):
        client = LinearGraphQLClient(api_key="lin_oauth_looks_like_a_token")
        assert client._client.headers["Authorization"] == "lin_oauth_looks_like_a_token"

    def test_both_credentials_is_an_error(self):
        with pytest.raises(ValueError, match="not both"):
            LinearGraphQLClient(api_key="k", access_token="t")

    def test_neither_credential_is_an_error(self):
        with pytest.raises(ValueError, match="needs a credential"):
            LinearGraphQLClient()
