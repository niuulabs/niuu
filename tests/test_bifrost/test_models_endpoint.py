"""Tests for GET /v1/models endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig


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
        assert by_id["gpt-5.5"]["owned_by"] == "openai"


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
        assert "gpt-5.5" in ids


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
        assert "claude-sonnet-4-6" in ids
        assert "gpt-5.5" in ids


class TestInternalCatalogEndpoints:
    def test_internal_catalog_models_use_bifrost_owned_catalog_without_providers(self) -> None:
        config = BifrostConfig(providers={})
        app = create_app(config)
        with TestClient(app) as client:
            data = client.get("/api/v1/bifrost/models").json()
        ids = {entry["id"] for entry in data}
        assert "claude-sonnet-4-6" in ids
        assert "gpt-5.5" in ids

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
