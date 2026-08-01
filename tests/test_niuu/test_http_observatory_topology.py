"""Tests for the HTTP topology fragment client."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from niuu.adapters.outbound.http_observatory_topology import (
    HttpObservatoryTopologyClient,
    _fragment_url,
)
from niuu.domain.models import InstanceKind, InstanceVisibility, RegisteredInstance

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _instance(base_url: str) -> RegisteredInstance:
    return RegisteredInstance(
        id="obs-a",
        kind=InstanceKind.OBSERVATORY,
        slug="obs-a",
        name="Observatory A",
        base_url=base_url,
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id=None,
        enabled=True,
        is_default=False,
        config={},
        tags=[],
        created_at=NOW,
        updated_at=NOW,
    )


class TestFragmentUrl:
    @pytest.mark.parametrize(
        "base_url",
        [
            "https://obs.example.test",
            "https://obs.example.test/",
            "https://obs.example.test/api/v1/observatory",
            "https://obs.example.test/api/v1/observatory/fragment",
        ],
    )
    def test_resolves_to_one_endpoint_however_the_instance_was_registered(
        self, base_url: str
    ) -> None:
        assert _fragment_url(base_url) == "https://obs.example.test/api/v1/observatory/fragment"

    def test_rejects_a_non_http_scheme(self) -> None:
        with pytest.raises(ValueError):
            _fragment_url("embedded://local-forge")

    def test_rejects_embedded_credentials(self) -> None:
        """A base URL is stored in the registry and logged; it must not carry
        a secret."""
        with pytest.raises(ValueError):
            _fragment_url("https://user:pass@obs.example.test")


class TestFetchFragment:
    @pytest.mark.asyncio
    async def test_parses_a_fragment_and_forwards_auth_headers(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(
                200,
                json={
                    "nodes": [{"id": "n-1", "typeId": "mimir", "label": "mímir", "pages": 203}],
                    "edges": [],
                    "meta": {"sourceId": "obs-a"},
                },
            )

        client = HttpObservatoryTopologyClient(
            timeout_seconds=1.0,
            transport=httpx.MockTransport(handler),
        )

        fragment = await client.fetch_fragment(
            _instance("https://obs.example.test"),
            headers={"authorization": "Bearer token-a"},
        )

        assert seen["authorization"] == "Bearer token-a"
        assert fragment.meta is not None
        assert fragment.meta.source_id == "obs-a"
        # Kind-specific detail must survive the trip.
        assert fragment.nodes[0].model_dump()["pages"] == 203

    @pytest.mark.asyncio
    async def test_an_error_response_raises_so_the_source_is_reported_failed(self) -> None:
        client = HttpObservatoryTopologyClient(
            timeout_seconds=1.0,
            transport=httpx.MockTransport(lambda _request: httpx.Response(503)),
        )

        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_fragment(_instance("https://obs.example.test"), headers={})
