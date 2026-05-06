"""Tests for legacy route compatibility helpers."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response

from niuu.http_compat import (
    LegacyRouteNotice,
    apply_deprecation_headers,
    collect_legacy_route_hits,
    record_legacy_route_use,
    reset_legacy_route_hits,
    warn_on_legacy_route,
)


class TestApplyDeprecationHeaders:
    def test_sets_standard_headers(self) -> None:
        response = Response()
        notice = LegacyRouteNotice(
            legacy_path="/api/v1/volundr/me",
            canonical_path="/api/v1/identity/me",
            sunset="Wed, 31 Dec 2026 23:59:59 GMT",
        )

        apply_deprecation_headers(response, notice)

        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Legacy-Route"] == "/api/v1/volundr/me"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/identity/me"
        assert response.headers["Link"] == '</api/v1/identity/me>; rel="successor-version"'
        assert response.headers["Sunset"] == "Wed, 31 Dec 2026 23:59:59 GMT"


class TestRecordLegacyRouteUse:
    def test_counts_hits_per_route_and_method(self) -> None:
        app = FastAPI()
        app.state.legacy_route_hits = {}
        notice = LegacyRouteNotice(
            legacy_path="/api/v1/volundr/me",
            canonical_path="/api/v1/identity/me",
        )

        count1 = record_legacy_route_use(app, notice, method="get")
        count2 = record_legacy_route_use(app, notice, method="GET")

        assert count1 == 1
        assert count2 == 2
        assert (
            app.state.legacy_route_hits[("/api/v1/volundr/me", "/api/v1/identity/me", "GET")] == 2
        )


class TestWarnOnLegacyRoute:
    def test_applies_headers_records_hit_and_logs(self, caplog) -> None:
        app = FastAPI()
        app.state.legacy_route_hits = {}
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/volundr/me",
                "headers": [],
                "app": app,
            }
        )
        response = Response()
        notice = LegacyRouteNotice(
            legacy_path="/api/v1/volundr/me",
            canonical_path="/api/v1/identity/me",
        )

        with caplog.at_level(logging.WARNING):
            count = warn_on_legacy_route(request, response, notice)

        assert count == 1
        assert response.headers["Deprecation"] == "true"
        assert "Legacy route hit" in caplog.text


class TestCollectLegacyRouteHits:
    def test_returns_sorted_snapshot(self) -> None:
        app = FastAPI()
        app.state.legacy_route_hits = {
            ("/api/v1/volundr/me", "/api/v1/identity/me", "GET"): 2,
            ("/api/v1/volundr/users", "/api/v1/volundr/admin/users", "GET"): 1,
        }

        snapshot = collect_legacy_route_hits(app)

        assert snapshot[0].legacy_path == "/api/v1/volundr/me"
        assert snapshot[0].hits == 2
        assert snapshot[1].legacy_path == "/api/v1/volundr/users"

    def test_handles_empty_state(self) -> None:
        app = FastAPI()
        app.state.legacy_route_hits = {}

        assert collect_legacy_route_hits(app) == ()


class TestResetLegacyRouteHits:
    def test_returns_snapshot_and_clears_state(self) -> None:
        app = FastAPI()
        app.state.legacy_route_hits = {
            ("/api/v1/volundr/me", "/api/v1/identity/me", "GET"): 2,
        }

        snapshot = reset_legacy_route_hits(app)

        assert snapshot[0].legacy_path == "/api/v1/volundr/me"
        assert app.state.legacy_route_hits == {}
