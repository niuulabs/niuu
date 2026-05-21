"""Linear GraphQL client adapter.

Concrete implementation of GraphQLClientPort for the Linear API.
Used by both Volundr's LinearAdapter and Ting's LinearTrackerAdapter.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from niuu.domain.models import LINEAR_API_URL, CacheEntry
from niuu.ports.graphql import GraphQLClientPort

logger = logging.getLogger(__name__)


class GraphQLError(Exception):
    """Raised when a GraphQL response contains errors."""


class LinearGraphQLClient(GraphQLClientPort):
    """Linear GraphQL client with TTL caching and rate-limit retry."""

    def __init__(
        self,
        api_key: str,
        api_url: str = LINEAR_API_URL,
        cache_ttl: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._api_url = api_url
        self._client = httpx.AsyncClient(
            base_url=api_url,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        self._cache: dict[str, CacheEntry] = {}
        self._cache_ttl = cache_ttl
        self._max_retries = max_retries

    async def query(
        self,
        query: str,
        variables: dict | None = None,
    ) -> dict:
        """Execute a GraphQL query against the Linear API.

        Retries on 429 (rate limited) up to ``max_retries`` times,
        respecting the ``Retry-After`` header.
        """
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post("", json=payload)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    if attempt < self._max_retries:
                        logger.warning(
                            "Linear rate limited, retrying in %.1fs (attempt %d/%d)",
                            retry_after,
                            attempt + 1,
                            self._max_retries,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    response.raise_for_status()

                if response.status_code >= 400:
                    logger.error(
                        "Linear API error %d: %s",
                        response.status_code,
                        response.text[:500],
                    )
                response.raise_for_status()
                body = response.json()

                if "errors" in body:
                    errors = body["errors"]
                    first = errors[0]
                    msg = first.get("message", str(errors))
                    extensions = first.get("extensions", {})
                    if extensions:
                        msg += f" (extensions={extensions})"
                    raise GraphQLError(msg)

                return body.get("data", {})

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < self._max_retries:
                    retry_after = float(exc.response.headers.get("Retry-After", "1"))
                    logger.warning(
                        "Linear rate limited, retrying in %.1fs (attempt %d/%d)",
                        retry_after,
                        attempt + 1,
                        self._max_retries,
                    )
                    await asyncio.sleep(retry_after)
                    last_exc = exc
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Exhausted retries without response")  # pragma: no cover

    # -- Cache helpers --

    def get_cached(self, key: str) -> object | None:
        """Return cached value if present and not expired."""
        entry = self._cache.get(key)
        if entry is None or entry.expired:
            return None
        return entry.value

    def set_cached(
        self,
        key: str,
        value: object,
        ttl: float | None = None,
    ) -> None:
        """Store a value in the cache with optional custom TTL."""
        self._cache[key] = CacheEntry(value, ttl or self._cache_ttl)

    def invalidate_cache(self, prefix: str) -> None:
        """Remove all cache entries whose keys start with ``prefix``."""
        self._cache = {k: v for k, v in self._cache.items() if not k.startswith(prefix)}

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
