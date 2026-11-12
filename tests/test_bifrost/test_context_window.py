"""``context_window`` in the Bifröst catalog: config, domain type, routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bifrost import catalog
from bifrost.app import create_app
from bifrost.config import BifrostConfig, ManagedModelConfig, ProviderConfig

#: The default-catalog models whose 1M-token window this catalog documents (descriptions say
#: "1M-token context"); Forge's LEX-1421 `session_usage.model_windows` agrees for the first two.
DOCUMENTED = {
    "claude-fable-5-1",
    "claude-opus-5-5",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "muse-spark-1.3",
    "muse-spark-1.2",
}


def _entry(**overrides: object) -> ManagedModelConfig:
    return ManagedModelConfig(id="m", name="M", **overrides)  # type: ignore[arg-type]


class TestConfigField:
    def test_defaults_to_null_never_a_guess(self) -> None:
        assert _entry().context_window is None

    def test_accepts_a_positive_token_count(self) -> None:
        assert _entry(context_window=200_000).context_window == 200_000

    @pytest.mark.parametrize("bad", [0, -1])
    def test_rejects_a_non_positive_window(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            _entry(context_window=bad)

    def test_description_names_what_null_means(self) -> None:
        field = ManagedModelConfig.model_fields["context_window"]
        assert "Null" in (field.description or "")


class TestDefaultCatalog:
    def test_the_documented_models_carry_their_window(self) -> None:
        by_id = {m.id: m for m in BifrostConfig().models}
        assert {mid for mid, m in by_id.items() if m.context_window is not None} == DOCUMENTED
        assert all(by_id[mid].context_window == 1_000_000 for mid in DOCUMENTED)

    def test_every_other_default_model_is_null(self) -> None:
        others = [m for m in BifrostConfig().models if m.id not in DOCUMENTED]
        assert others, "the default catalog lists models without a documented window"
        assert all(m.context_window is None for m in others)

    def test_a_default_that_states_a_window_in_its_description_has_one(self) -> None:
        # A described "1M-token context" with no number would leave a consumer refusing a
        # model whose limit the catalog itself states.
        described = {m.id for m in BifrostConfig().models if "1M-token context" in m.description}
        assert described <= DOCUMENTED


class TestCatalogEntries:
    def test_configured_window_reaches_the_domain_entry(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["m"])},
            models=[_entry(context_window=123_456)],
        )
        [model] = [m for m in catalog.list_models(config) if m.id == "m"]
        assert model.context_window == 123_456

    def test_a_model_known_only_to_a_provider_has_a_null_window(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-only-here"])},
            models=[],
        )
        [model] = [m for m in catalog.list_models(config) if m.id == "claude-only-here"]
        assert model.context_window is None


class TestRoutes:
    @pytest.fixture
    def client(self) -> TestClient:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["m", "n", "claude-only-here"])},
            models=[_entry(context_window=500_000), ManagedModelConfig(id="n", name="N")],
            aliases={"big": "m"},
        )
        with TestClient(create_app(config)) as client:
            yield client

    def test_list_carries_the_window_and_null(self, client: TestClient) -> None:
        body = {m["id"]: m for m in client.get("/api/v1/bifrost/models").json()}
        assert body["m"]["context_window"] == 500_000
        assert body["n"]["context_window"] is None
        assert body["claude-only-here"]["context_window"] is None

    def test_one_model_carries_the_window(self, client: TestClient) -> None:
        assert client.get("/api/v1/bifrost/models/m").json()["context_window"] == 500_000

    def test_one_model_by_alias_carries_the_window(self, client: TestClient) -> None:
        assert client.get("/api/v1/bifrost/models/big").json()["context_window"] == 500_000

    def test_a_model_without_a_limit_answers_null_explicitly(self, client: TestClient) -> None:
        body = client.get("/api/v1/bifrost/models/n").json()
        assert "context_window" in body and body["context_window"] is None
