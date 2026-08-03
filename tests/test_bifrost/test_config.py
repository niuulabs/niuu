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

    def test_grok_build_resolves_to_skuld_grok_definition(self):
        # Regression: grok-build must be registered in the managed-model
        # catalog with session_definition=skuldGrok. Otherwise a forge session
        # created by model alone (no explicit definition) fails the catalog
        # lookup in ForgeService._resolve_session_definition and falls back to
        # the default skuldClaude definition — provisioning the Claude transport
        # for a Grok session, so no grok ACP worker ever spawns.
        grok = next((m for m in BifrostConfig().models if m.id == "grok-build"), None)
        assert grok is not None, "grok-build missing from default model catalog"
        assert grok.session_definition == "skuldGrok"
        assert grok.vendor == "xai"

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
