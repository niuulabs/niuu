"""Tests for BifrostConfig and ProviderConfig."""

from __future__ import annotations

from pathlib import Path

from bifrost.__main__ import _load_config
from bifrost.auth import AuthMode
from bifrost.config import BifrostConfig, ProviderConfig, RoutingStrategy


class TestProviderConfig:
    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret-123")
        cfg = ProviderConfig(api_key_env="MY_KEY")
        assert cfg.api_key == "secret-123"

    def test_api_key_missing_env(self):
        cfg = ProviderConfig(api_key_env="NONEXISTENT_KEY_12345")
        assert cfg.api_key == ""

    def test_api_key_no_env_var(self):
        cfg = ProviderConfig()
        assert cfg.api_key == ""

    def test_defaults(self):
        cfg = ProviderConfig()
        assert cfg.base_url == ""
        assert cfg.models == []
        assert cfg.timeout == 120.0
        assert cfg.chat_template_kwargs == {}

    def test_chat_template_kwargs_from_config(self):
        cfg = ProviderConfig.model_validate(
            {"chat_template_kwargs": {"force_nonempty_content": True}}
        )

        assert cfg.chat_template_kwargs == {"force_nonempty_content": True}


class TestBifrostConfig:
    def test_resolve_alias_known(self):
        cfg = BifrostConfig(aliases={"fast": "claude-haiku-4-5-20251001"})
        assert cfg.resolve_alias("fast") == "claude-haiku-4-5-20251001"

    def test_resolve_alias_passthrough(self):
        cfg = BifrostConfig(aliases={"fast": "claude-haiku-4-5-20251001"})
        assert cfg.resolve_alias("gpt-4o") == "gpt-4o"

    def test_muse_models_resolve_to_skuld_muse_definition(self):
        # Every Muse Spark id must be registered with session_definition=skuldMuse, or a
        # forge session created for one of them falls back to the platform default
        # runtime and no Muse MSP worker ever spawns (the Grok lesson, applied up front).
        from bifrost.config import _default_models
        from niuu.domain.model_catalog import ManagedModelTier

        models = _default_models()
        for model_id in ("muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.3-contributor"):
            muse = next((m for m in models if m.id == model_id), None)
            assert muse is not None, f"{model_id} missing from default model catalog"
            assert muse.session_definition == "skuldMuse"
            assert muse.vendor == "meta"
        default = next(m for m in models if m.id == "muse-spark-1.3")
        assert default.tier == ManagedModelTier.FRONTIER

    def test_fable_entry_is_fable_5_1(self):
        # The Fable row is Claude Fable 5.1 (Damien, 2026-09-02). Fable 5 stays served by
        # Anthropic, so an old client sending `claude-fable-5` still works — it is just no
        # longer the catalogue's Fable entry.
        from bifrost.config import _default_models
        from niuu.domain.model_catalog import ManagedModelTier

        models = _default_models()
        fable = next((m for m in models if m.id == "claude-fable-5-1"), None)
        assert fable is not None, "claude-fable-5-1 missing from default model catalog"
        assert fable.name == "Claude Fable 5.1"
        assert fable.session_definition == "skuldClaude"
        assert fable.tier == ManagedModelTier.FRONTIER
        assert not [m for m in models if m.id == "claude-fable-5"], (
            "claude-fable-5 must not remain as a second Fable row"
        )

    def test_codex_catalog_preserves_astra_sol_and_terra(self):
        # Astra + Sol are the only two Codex choices, Astra the default (Damien,
        # 2026-09-05). Terra was removed with the same decision.
        from bifrost.config import _default_models
        from niuu.domain.model_catalog import ManagedModelTier

        models = _default_models()
        astra = next((m for m in models if m.id == "gpt-6-astra"), None)
        assert astra is not None, "gpt-6-astra missing from default model catalog"
        assert astra.name == "GPT-6 Astra"
        assert astra.vendor == "openai"
        assert astra.session_definition == "skuldCodex"
        assert astra.tier == ManagedModelTier.FRONTIER
        assert astra.supports_tools and astra.supports_thinking
        sol = next((m for m in models if m.id == "gpt-5.6-sol"), None)
        assert sol is not None, "gpt-5.6-sol must stay in the catalogue"
        assert sol.session_definition == "skuldCodex"
        openai_ids = [m.id for m in models if m.session_definition == "skuldCodex"]
        assert openai_ids == ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra"], (
            f"Codex catalogue must preserve Astra, Sol and upstream Terra, got {openai_ids}"
        )
        assert next(m for m in models if m.id == "gpt-5.6-terra").session_definition == "skuldCodex"

    def test_grok_models_resolve_to_skuld_grok_definition(self):
        # Regression: every Grok model must be registered in the managed-model
        # catalog with session_definition=skuldGrok. Otherwise a forge session
        # created by model alone (no explicit definition) fails the catalog
        # lookup in ForgeService._resolve_session_definition and falls back to
        # the default skuldClaude definition — provisioning the Claude transport
        # for a Grok session, so no grok ACP worker ever spawns.
        #
        # The ids are the ones `grok models` actually serves. This test used to
        # name "grok-build", which the CLI has never served: it rejects the id
        # outright, so every Grok session died at its first prompt while this
        # test stayed green. Ids here must track the live catalogue.
        models = BifrostConfig().models
        for model_id in ("grok-4.6", "grok-4.5"):
            grok = next((m for m in models if m.id == model_id), None)
            assert grok is not None, f"{model_id} missing from default model catalog"
            assert grok.session_definition == "skuldGrok"
            assert grok.vendor == "xai"
        assert not [m for m in models if m.id == "grok-build"], (
            "grok-build is not a real model id and must not reappear in the catalogue"
        )

    def test_provider_for_model_found(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o", "gpt-4o-mini"]),
                "anthropic": ProviderConfig(models=["claude-sonnet-4-20250514"]),
            }
        )
        assert cfg.provider_for_model("gpt-4o") == "openai"
        assert cfg.provider_for_model("claude-sonnet-4-20250514") == "anthropic"

    def test_provider_for_model_not_found(self):
        cfg = BifrostConfig(providers={"openai": ProviderConfig(models=["gpt-4o"])})
        assert cfg.provider_for_model("unknown-model") is None

    def test_effective_base_url_from_config(self):
        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(base_url="https://custom.openai.com")}
        )
        assert cfg.effective_base_url("openai") == "https://custom.openai.com"

    def test_effective_base_url_default_anthropic(self):
        cfg = BifrostConfig(providers={"anthropic": ProviderConfig()})
        assert cfg.effective_base_url("anthropic") == "https://api.anthropic.com"

    def test_effective_base_url_default_openai(self):
        cfg = BifrostConfig(providers={"openai": ProviderConfig()})
        assert cfg.effective_base_url("openai") == "https://api.openai.com"

    def test_effective_base_url_default_ollama(self):
        cfg = BifrostConfig(providers={"ollama": ProviderConfig()})
        assert cfg.effective_base_url("ollama") == "http://localhost:11434"

    def test_effective_base_url_unknown_provider(self):
        cfg = BifrostConfig()
        assert cfg.effective_base_url("mystery") == ""

    def test_defaults(self):
        cfg = BifrostConfig()
        assert cfg.routing_strategy == RoutingStrategy.FAILOVER
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8088
        assert cfg.providers == {}
        assert cfg.aliases == {}

    def test_routing_strategy_field(self):
        cfg = BifrostConfig(routing_strategy=RoutingStrategy.ROUND_ROBIN)
        assert cfg.routing_strategy == RoutingStrategy.ROUND_ROBIN

    def test_routing_strategy_from_string(self):
        cfg = BifrostConfig.model_validate({"routing_strategy": "cost_optimised"})
        assert cfg.routing_strategy == RoutingStrategy.COST_OPTIMISED

    def test_providers_for_model_returns_all_matching(self):
        cfg = BifrostConfig(
            providers={
                "a": ProviderConfig(models=["gpt-4o"]),
                "b": ProviderConfig(models=["gpt-4o", "gpt-4o-mini"]),
                "c": ProviderConfig(models=["claude-sonnet-4-20250514"]),
            }
        )
        providers = cfg.providers_for_model("gpt-4o")
        assert providers == ["a", "b"]

    def test_providers_for_model_empty_when_none_match(self):
        cfg = BifrostConfig(providers={"a": ProviderConfig(models=["gpt-4o"])})
        assert cfg.providers_for_model("unknown") == []

    def test_cost_per_token_default(self):
        cfg = ProviderConfig()
        assert cfg.cost_per_token == 0.0

    def test_latency_ewma_alpha_default(self):
        cfg = BifrostConfig()
        assert cfg.latency_ewma_alpha == 0.2

    def test_latency_ewma_alpha_configurable(self):
        cfg = BifrostConfig.model_validate({"latency_ewma_alpha": 0.5})
        assert cfg.latency_ewma_alpha == 0.5

    def test_full_config(self):
        cfg = BifrostConfig.model_validate(
            {
                "providers": {
                    "anthropic": {
                        "api_key_env": "ANTHROPIC_API_KEY",
                        "models": ["claude-sonnet-4-20250514"],
                    },
                    "openai": {
                        "api_key_env": "OPENAI_API_KEY",
                        "models": ["gpt-4o"],
                    },
                    "ollama": {
                        "base_url": "http://localhost:11434",
                        "models": ["llama3.1:8b"],
                    },
                },
                "aliases": {
                    "fast": "claude-haiku-4-5-20251001",
                    "balanced": "claude-sonnet-4-20250514",
                    "local": "llama3.1:8b",
                },
            }
        )
        assert len(cfg.providers) == 3
        assert len(cfg.aliases) == 3
        assert cfg.provider_for_model("gpt-4o") == "openai"
        assert cfg.resolve_alias("local") == "llama3.1:8b"


# ---------------------------------------------------------------------------
# Pi-mode config
# ---------------------------------------------------------------------------

#: Absolute path to the Pi-mode example config shipped in the repo root.
_PI_CONFIG = Path(__file__).parents[2] / "bifrost.pi.example.yaml"


class TestPiModeConfig:
    """Verify that BifrostConfig boots cleanly with only Ollama configured."""

    def test_pi_example_file_exists(self):
        assert _PI_CONFIG.exists(), "bifrost.pi.example.yaml must exist in repo root"

    def test_pi_config_loads_without_error(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert isinstance(cfg, BifrostConfig)

    def test_pi_config_single_ollama_provider(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert list(cfg.providers.keys()) == ["ollama"]

    def test_pi_config_no_cloud_api_keys(self):
        cfg = _load_config(str(_PI_CONFIG))
        for provider in cfg.providers.values():
            assert provider.api_key_env == "", (
                "Pi-mode config must not reference any cloud API key env vars"
            )

    def test_pi_config_ollama_default_base_url(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert cfg.effective_base_url("ollama") == "http://localhost:11434"

    def test_pi_config_open_auth(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert cfg.auth_mode == AuthMode.OPEN

    def test_pi_config_direct_routing(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert cfg.routing_strategy == RoutingStrategy.DIRECT

    def test_pi_config_aliases_point_to_local_models(self):
        cfg = _load_config(str(_PI_CONFIG))
        fast = cfg.resolve_alias("fast")
        balanced = cfg.resolve_alias("balanced")
        best = cfg.resolve_alias("best")
        ollama_models = cfg.providers["ollama"].models
        assert fast in ollama_models, f"fast alias '{fast}' not in ollama models"
        assert balanced in ollama_models, f"balanced alias '{balanced}' not in ollama models"
        assert best in ollama_models, f"best alias '{best}' not in ollama models"

    def test_pi_config_provider_for_alias_resolves_to_ollama(self):
        cfg = _load_config(str(_PI_CONFIG))
        for alias in ("fast", "balanced", "best", "local"):
            canonical = cfg.resolve_alias(alias)
            provider = cfg.provider_for_model(canonical)
            assert provider == "ollama", (
                f"alias '{alias}' → '{canonical}' should route to ollama, got {provider!r}"
            )

    def test_pi_config_sqlite_usage_store(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert cfg.usage_store.adapter == "sqlite"

    def test_pi_config_localhost_binding(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8088

    def test_pi_config_ollama_free_cost(self):
        cfg = _load_config(str(_PI_CONFIG))
        assert cfg.providers["ollama"].cost_per_token == 0.0
