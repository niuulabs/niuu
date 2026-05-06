"""Tests for canonical and legacy feature catalog routes."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.http_contracts import RouteCallSpec, assert_route_equivalence
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_features import (
    create_feature_catalog_router,
    create_features_router,
)
from volundr.domain.models import Principal
from volundr.domain.services.feature import FeatureModule, UserFeaturePreference


def _make_admin_principal() -> Principal:
    return Principal(
        user_id="user-1",
        email="dev@example.com",
        tenant_id="tenant-1",
        roles=["volundr:admin"],
    )


def _make_feature_service() -> AsyncMock:
    service = AsyncMock()
    service.get_catalog.return_value = [
        FeatureModule(
            key="tokens",
            label="Tokens",
            icon="ShieldCheck",
            scope="user",
            enabled=True,
            default_enabled=True,
            admin_only=False,
            order=10,
        )
    ]
    service.get_user_preferences.return_value = [
        UserFeaturePreference(
            feature_key="tokens",
            visible=True,
            sort_order=0,
        )
    ]
    service.update_user_preferences.return_value = [
        UserFeaturePreference(
            feature_key="tokens",
            visible=True,
            sort_order=0,
        )
    ]
    service.set_feature_enabled.return_value = None
    return service


def _make_app(feature_service: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(create_feature_catalog_router(feature_service))
    app.include_router(create_features_router(feature_service))
    app.dependency_overrides[extract_principal] = _make_admin_principal
    return TestClient(app)


class TestFeatureCatalogRoutes:
    def test_legacy_catalog_matches_canonical_modules(self) -> None:
        client = _make_app(_make_feature_service())

        legacy = client.get("/api/v1/volundr/features")
        assert legacy.status_code == 200
        assert legacy.headers["Deprecation"] == "true"
        assert legacy.headers["Link"] == '</api/v1/features/modules>; rel="successor-version"'

        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(path="/api/v1/volundr/features"),
            canonical=RouteCallSpec(path="/api/v1/features/modules"),
        )

    def test_canonical_root_alias_matches_modules(self) -> None:
        client = _make_app(_make_feature_service())
        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(path="/api/v1/features"),
            canonical=RouteCallSpec(path="/api/v1/features/modules"),
        )

    def test_volundr_modules_alias_matches_canonical_modules(self) -> None:
        client = _make_app(_make_feature_service())

        legacy = client.get("/api/v1/volundr/features/modules")
        assert legacy.status_code == 200
        assert legacy.headers["Deprecation"] == "true"

        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(path="/api/v1/volundr/features/modules"),
            canonical=RouteCallSpec(path="/api/v1/features/modules"),
        )

    def test_legacy_toggle_matches_canonical_toggle(self) -> None:
        client = _make_app(_make_feature_service())

        legacy = client.put(
            "/api/v1/volundr/features/tokens/toggle",
            json={"enabled": False},
        )
        assert legacy.status_code == 200
        assert legacy.headers["Deprecation"] == "true"
        canonical = client.patch(
            "/api/v1/features/modules/tokens",
            json={"enabled": False},
        )
        assert canonical.status_code == 200
        assert legacy.json() == canonical.json()

    def test_volundr_modules_toggle_alias_matches_canonical_toggle(self) -> None:
        client = _make_app(_make_feature_service())

        legacy = client.post(
            "/api/v1/volundr/features/modules/tokens/toggle",
            json={"enabled": False},
        )
        assert legacy.status_code == 200
        assert legacy.headers["Deprecation"] == "true"

        canonical = client.post(
            "/api/v1/features/modules/tokens/toggle",
            json={"enabled": False},
        )
        assert canonical.status_code == 200
        assert legacy.json() == canonical.json()

    def test_canonical_preferences_accept_raw_array(self) -> None:
        client = _make_app(_make_feature_service())

        response = client.put(
            "/api/v1/features/preferences",
            json=[
                {
                    "feature_key": "tokens",
                    "visible": True,
                    "sort_order": 0,
                }
            ],
        )

        assert response.status_code == 200
        assert response.json() == [
            {
                "feature_key": "tokens",
                "visible": True,
                "sort_order": 0,
            }
        ]

    def test_legacy_preferences_match_canonical_preferences(self) -> None:
        client = _make_app(_make_feature_service())

        legacy = client.put(
            "/api/v1/volundr/features/preferences",
            json={
                "preferences": [
                    {
                        "feature_key": "tokens",
                        "visible": True,
                        "sort_order": 0,
                    }
                ]
            },
        )
        assert legacy.status_code == 200
        assert legacy.headers["Deprecation"] == "true"

        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(
                path="/api/v1/volundr/features/preferences",
                method="PUT",
                json_body={
                    "preferences": [
                        {
                            "feature_key": "tokens",
                            "visible": True,
                            "sort_order": 0,
                        }
                    ]
                },
            ),
            canonical=RouteCallSpec(
                path="/api/v1/features/preferences",
                method="PUT",
                json_body=[
                    {
                        "feature_key": "tokens",
                        "visible": True,
                        "sort_order": 0,
                    }
                ],
            ),
        )
