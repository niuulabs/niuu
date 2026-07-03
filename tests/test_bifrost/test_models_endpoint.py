"""Tests for GET /v1/models endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from niuu.domain.model_catalog import ProviderHealthState


class TestModelsEndpoint:
    def test_list_models_returns_200(self, client: TestClient) -> None:
        response = client.get("/api/v1/bifrost/v1/models")
        assert response.status_code == 200

    def test_list_models_top_level_object_field(self, client: TestClient) -> None:
        body = client.get("/api/v1/bifrost/v1/models").json()
        assert body.get("object") == "list"

    def test_list_models_returns_data_list(self, client: TestClient) -> None:
        body = client.get("/api/v1/bifrost/v1/models").json()
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_list_models_contains_expected_models(self, client: TestClient) -> None:
        data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        ids = {m["id"] for m in data}
        assert "claude-sonnet-4-6" in ids
        assert "claude-opus-4-6" in ids

    def test_list_models_contains_gpt_5_6_sol(self, client: TestClient) -> None:
        data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        by_id = {m["id"]: m for m in data}
        assert "gpt-5.6-sol" in by_id
        assert by_id["gpt-5.6-sol"]["owned_by"] == "openai"

    def test_list_models_has_object_field(self, client: TestClient) -> None:
        data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        assert all(m.get("object") == "model" for m in data)

    def test_list_models_has_display_name(self, client: TestClient) -> None:
        data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        assert all("display_name" in m for m in data)

    def test_list_models_has_owned_by(self, client: TestClient) -> None:
        data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        assert all("owned_by" in m for m in data)

    def test_list_models_owned_by_is_provider_name(self, client: TestClient) -> None:
        data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        by_id = {entry["id"]: entry for entry in data}
        assert by_id["claude-sonnet-4-6"]["owned_by"] == "anthropic"
        assert by_id["gpt-5.6-sol"]["owned_by"] == "openai"


class TestModelsEndpointAliases:
    def test_aliases_included_in_model_list(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"smart": "claude-sonnet-4-6"},
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        ids = {m["id"] for m in data}
        assert "smart" in ids

    def test_alias_display_name_is_canonical_model(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"smart": "claude-sonnet-4-6"},
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        alias_entry = next(m for m in data if m["id"] == "smart")
        assert alias_entry["display_name"] == "claude-sonnet-4-6"

    def test_alias_owned_by_resolves_to_provider(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"smart": "claude-sonnet-4-6"},
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        alias_entry = next(m for m in data if m["id"] == "smart")
        assert alias_entry["owned_by"] == "anthropic"

    def test_no_duplicate_entries_when_alias_matches_canonical(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"claude-sonnet-4-6": "claude-sonnet-4-6"},
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        ids = [m["id"] for m in data]
        assert ids.count("claude-sonnet-4-6") == 1

    def test_alias_with_unknown_canonical_owned_by_unknown(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"mystery": "some-unknown-model"},
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        alias_entry = next(m for m in data if m["id"] == "mystery")
        assert alias_entry["owned_by"] == "unknown"

    def test_no_aliases_config_returns_only_provider_models(self) -> None:
        config = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(models=["claude-sonnet-4-6", "claude-opus-4-6"])
            },
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        ids = {entry["id"] for entry in data}
        assert "claude-sonnet-4-6" in ids
        assert "claude-opus-4-6" in ids
        assert "gpt-5.6-sol" in ids


class TestModelsEndpointMultiProvider:
    def test_models_from_all_providers_included(self) -> None:
        config = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(models=["claude-sonnet-4-6"]),
                "openai": ProviderConfig(models=["gpt-4o"]),
            }
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        ids = {m["id"] for m in data}
        assert "claude-sonnet-4-6" in ids
        assert "gpt-4o" in ids

    def test_owned_by_reflects_correct_provider(self) -> None:
        config = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(models=["claude-sonnet-4-6"]),
                "openai": ProviderConfig(models=["gpt-4o"]),
            }
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        by_id = {m["id"]: m for m in data}
        assert by_id["claude-sonnet-4-6"]["owned_by"] == "anthropic"
        assert by_id["gpt-4o"]["owned_by"] == "openai"

    def test_deduplicates_model_listed_in_multiple_providers(self) -> None:
        config = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(models=["claude-sonnet-4-6"]),
                "mirror": ProviderConfig(models=["claude-sonnet-4-6"]),
            }
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        ids = [m["id"] for m in data]
        assert ids.count("claude-sonnet-4-6") == 1

    def test_empty_providers_still_return_canonical_catalog(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            body = client.get("/api/v1/bifrost/v1/models").json()
        assert body["object"] == "list"
        ids = {entry["id"] for entry in body["data"]}
        assert "claude-sonnet-5" in ids
        assert "gpt-5.6-sol" in ids


class TestInternalCatalogEndpoints:
    def test_health_endpoint_is_available_without_public_prefix(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/health").json()
        assert data == {"status": "ok"}

    def test_internal_catalog_models_also_available_without_public_prefix(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/models").json()
        ids = {entry["id"] for entry in data}
        assert "claude-sonnet-5" in ids
        assert "gpt-5.6-sol" in ids

    def test_internal_catalog_providers_also_available_without_public_prefix(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/providers").json()
        vendors = {entry["vendor"] for entry in data}
        assert "anthropic" in vendors
        assert "openai" in vendors

    def test_internal_catalog_models_use_bifrost_owned_catalog_without_providers(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/models").json()
        ids = {entry["id"] for entry in data}
        assert "claude-sonnet-5" in ids
        assert "gpt-5.6-sol" in ids

    def test_settings_endpoint_exposes_canonical_service_schema(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/settings").json()
        assert data["title"] == "Bifrost"
        assert data["sections"][0]["id"] == "service"
        keys = {field["key"] for field in data["sections"][0]["fields"]}
        assert {"auth_mode", "routing_strategy", "provider_count", "model_count"} <= keys

    def test_internal_catalog_providers_are_derived_from_model_vendors_when_unconfigured(
        self,
    ) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/providers").json()
        vendors = {entry["vendor"] for entry in data}
        assert "anthropic" in vendors
        assert "openai" in vendors

    def test_internal_catalog_model_detail_resolves_alias(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"smart": "claude-sonnet-4-6"},
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/models/smart").json()
        assert data["id"] == "claude-sonnet-4-6"
        assert "smart" in data["aliases"]

    def test_internal_catalog_model_detail_returns_404_for_unknown_model(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            response = client.get("/api/v1/bifrost/models/does-not-exist")
        assert response.status_code == 404
        assert response.json()["detail"] == "Unknown model: does-not-exist"

    def test_internal_catalog_aliases_list_configured_aliases(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"smart": "claude-sonnet-4-6", "fast": "claude-sonnet-4-6"},
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/aliases").json()
        assert data == [
            {"alias": "fast", "target": "claude-sonnet-4-6"},
            {"alias": "smart", "target": "claude-sonnet-4-6"},
        ]

    def test_internal_catalog_providers_preserve_configured_provider_details(self) -> None:
        config = BifrostConfig(
            providers={
                "openai": ProviderConfig(
                    models=["gpt-5.5"],
                    base_url="https://api.openai.example",
                    timeout=45.0,
                    cost_per_token=0.42,
                )
            }
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/providers").json()
        assert data == [
            {
                "key": "openai",
                "vendor": "openai",
                "base_url": "https://api.openai.example",
                "model_ids": ["gpt-5.5"],
                "timeout_seconds": 45.0,
                "cost_per_token": 0.42,
                "state": "unknown",
                "detail": "",
            }
        ]

    def test_internal_catalog_provider_health_reports_probe_results(self) -> None:
        config = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(
                    models=["claude-sonnet-4-6"],
                    base_url="https://anthropic.example",
                ),
                "openai": ProviderConfig(
                    models=["gpt-5.5"],
                    base_url="https://openai.example",
                ),
            }
        )
        app = create_app(config)
        with patch(
            "bifrost.inbound.routes._provider_health_snapshot",
            new=AsyncMock(
                return_value=(
                    {
                        "anthropic": ProviderHealthState.HEALTHY,
                        "openai": ProviderHealthState.DEGRADED,
                    },
                    {
                        "anthropic": "HTTP 200",
                        "openai": "HTTP 503",
                    },
                )
            ),
        ):
            with TestClient(app) as client:
                data = client.get("/api/v1/bifrost/providers/health").json()
        by_key = {entry["key"]: entry for entry in data}
        assert by_key["anthropic"]["state"] == "healthy"
        assert by_key["anthropic"]["detail"] == "HTTP 200"
        assert by_key["openai"]["state"] == "degraded"
        assert by_key["openai"]["detail"] == "HTTP 503"

    def test_internal_catalog_provider_health_probes_live_base_urls(self) -> None:
        config = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(
                    models=["claude-sonnet-4-6"],
                    base_url="https://anthropic.example",
                ),
                "openai": ProviderConfig(
                    models=["gpt-5.5"],
                    base_url="https://openai.example",
                ),
                "local": ProviderConfig(models=["llama3.2:latest"], base_url=""),
            }
        )
        app = create_app(config)

        class _FakeResponse:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        class _FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def head(self, url: str) -> _FakeResponse:
                if "anthropic" in url:
                    return _FakeResponse(204)
                if "openai" in url:
                    return _FakeResponse(503)
                raise AssertionError(f"unexpected URL {url}")

        with patch("bifrost.inbound.routes.httpx.AsyncClient", return_value=_FakeAsyncClient()):
            with TestClient(app) as client:
                data = client.get("/api/v1/bifrost/providers/health").json()

        by_key = {entry["key"]: entry for entry in data}
        assert by_key["anthropic"]["state"] == "healthy"
        assert by_key["anthropic"]["detail"] == "HTTP 204"
        assert by_key["openai"]["state"] == "degraded"
        assert by_key["openai"]["detail"] == "HTTP 503"
        assert by_key["local"]["state"] == "unknown"
        assert by_key["local"]["detail"] == "No base URL configured."

    def test_openai_models_endpoint_skips_disabled_models_and_keeps_aliases(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"smart": "claude-sonnet-4-6"},
            models=[
                {
                    "id": "claude-sonnet-4-6",
                    "name": "Claude Sonnet 4.6",
                    "vendor": "anthropic",
                    "enabled": True,
                },
                {
                    "id": "disabled-model",
                    "name": "Disabled Model",
                    "vendor": "anthropic",
                    "enabled": False,
                },
            ],
        )
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/v1/models").json()["data"]
        ids = {entry["id"] for entry in data}
        assert "claude-sonnet-4-6" in ids
        assert "smart" in ids
        assert "disabled-model" not in ids

    def test_cache_stats_and_reload_keys_endpoints_return_ok(self, client: TestClient) -> None:
        stats = client.get("/api/v1/bifrost/v1/cache/stats")
        assert stats.status_code == 200
        assert "hits" in stats.json()

        reload = client.post("/api/v1/bifrost/admin/reload-keys")
        assert reload.status_code == 200
        assert reload.json() == {"status": "ok"}
