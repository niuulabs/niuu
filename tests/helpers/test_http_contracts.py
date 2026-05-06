"""Tests for reusable HTTP contract comparison helpers."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.helpers.http_contracts import RouteCallSpec, assert_route_equivalence


class TestAssertRouteEquivalence:
    def test_matches_equivalent_routes(self) -> None:
        app = FastAPI()

        @app.get("/legacy")
        async def legacy() -> dict[str, object]:
            return {"id": "u1", "features": ["b", "a"]}

        @app.get("/canonical")
        async def canonical() -> dict[str, object]:
            return {"id": "u1", "features": ["a", "b"]}

        def normalize(payload: dict[str, object]) -> dict[str, object]:
            normalized = dict(payload)
            normalized["features"] = sorted(normalized["features"])  # type: ignore[index]
            return normalized

        with TestClient(app) as client:
            legacy_response, canonical_response = assert_route_equivalence(
                client,
                RouteCallSpec("/legacy"),
                RouteCallSpec("/canonical"),
                normalizer=normalize,
            )

        assert legacy_response.status_code == 200
        assert canonical_response.status_code == 200

    def test_passes_method_params_and_body(self) -> None:
        app = FastAPI()

        @app.post("/legacy")
        async def legacy(limit: int) -> dict[str, object]:
            return {"limit": limit, "name": "same"}

        @app.post("/canonical")
        async def canonical(limit: int) -> dict[str, object]:
            return {"limit": limit, "name": "same"}

        with TestClient(app) as client:
            assert_route_equivalence(
                client,
                RouteCallSpec("/legacy", method="POST", params={"limit": 2}, json_body={"x": 1}),
                RouteCallSpec(
                    "/canonical",
                    method="POST",
                    params={"limit": 2},
                    json_body={"x": 1},
                ),
            )
