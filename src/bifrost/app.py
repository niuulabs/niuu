"""Bifröst FastAPI application factory.

Wires the inbound routing layer (route handlers, middleware) to the
infrastructure adapters (usage store, model router, key vault) and
returns a configured ``FastAPI`` instance.

The inbound HTTP layer lives in ``bifrost.inbound``:
  - ``inbound/routes.py``   — route handlers and quota/access enforcement
  - ``inbound/tracking.py`` — SSE token-tracking helpers
  - ``inbound/chat_completions.py`` — OpenAI Chat Completions translation
"""

from __future__ import annotations

import logging
import signal
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from bifrost.adapters.auth import build_auth_adapter
from bifrost.adapters.key_vault import EnvKeyVault
from bifrost.config import AuditAdapter, BifrostConfig, CacheMode
from bifrost.inbound.observability import create_observability_router
from bifrost.inbound.routes import create_router
from bifrost.ports.audit import AuditPort
from bifrost.ports.cache import CachePort
from bifrost.ports.events import CostEventEmitter
from bifrost.ports.key_vault import KeyVaultPort
from bifrost.ports.rules import RuleEnginePort
from bifrost.ports.usage_store import UsageStore
from bifrost.pricing import ModelPricing, load_pricing_from_yaml
from bifrost.router import ModelRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule engine factory
# ---------------------------------------------------------------------------


def _build_rule_engine(config: BifrostConfig) -> RuleEnginePort | None:
    """Instantiate a ``YamlRuleEngine`` when rules are configured, else return ``None``."""
    if not config.rules:
        return None
    from bifrost.adapters.rules.yaml_engine import YamlRuleEngine

    return YamlRuleEngine(rules=config.rules, config=config)


# ---------------------------------------------------------------------------
# Audit adapter factory
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Usage store factory
# ---------------------------------------------------------------------------


def _build_usage_store(config: BifrostConfig) -> UsageStore:
    """Instantiate the configured usage store adapter."""
    match config.usage_store.adapter:
        case "sqlite":
            from bifrost.adapters.sqlite_store import SQLiteUsageStore

            return SQLiteUsageStore(path=config.usage_store.path)
        case "postgres":
            from bifrost.adapters.postgres_store import PostgresUsageStore

            dsn = config.usage_store.effective_dsn()
            if not dsn:
                raise ValueError(
                    "PostgreSQL usage store requires a DSN. "
                    "Set usage_store.dsn in config or the BIFROST_USAGE_DSN environment variable."
                )
            return PostgresUsageStore(dsn=dsn)
        case _:
            from bifrost.adapters.memory_store import MemoryUsageStore

            return MemoryUsageStore()


# ---------------------------------------------------------------------------
# Event emitter factory
# ---------------------------------------------------------------------------


def _build_event_emitter(config: BifrostConfig) -> CostEventEmitter:
    """Instantiate the configured cost event emitter adapter."""
    match config.events.adapter:
        case "sleipnir":
            from bifrost.adapters.events.sleipnir import SleipnirEventEmitter

            return SleipnirEventEmitter(
                url=config.events.url,
                exchange=config.events.exchange,
                exchange_type=config.events.exchange_type,
            )
        case _:
            from bifrost.adapters.events.null import NullEventEmitter

            return NullEventEmitter()


# ---------------------------------------------------------------------------
# Key vault factory
# ---------------------------------------------------------------------------


def _build_key_vault(config: BifrostConfig) -> KeyVaultPort:
    """Instantiate the key vault from config.

    Uses ``SecretsFileKeyVault`` when ``key_vault.secrets_file`` is set,
    otherwise falls back to ``EnvKeyVault`` (reads ``api_key_env`` per provider).
    """
    if config.key_vault.secrets_file:
        from bifrost.adapters.key_vault import SecretsFileKeyVault

        return SecretsFileKeyVault(path=config.key_vault.secrets_file)
    return EnvKeyVault(config)


# ---------------------------------------------------------------------------
# Audit adapter factory
# ---------------------------------------------------------------------------


def _build_audit(config: BifrostConfig) -> AuditPort:
    """Instantiate the configured audit adapter."""
    match config.audit.adapter:
        case AuditAdapter.POSTGRES:
            from bifrost.adapters.audit.postgres import PostgresAuditAdapter

            dsn = config.audit.effective_dsn()
            if not dsn:
                raise ValueError(
                    "PostgreSQL audit adapter requires a DSN. "
                    "Set audit.dsn in config or the BIFROST_AUDIT_DSN environment variable."
                )
            return PostgresAuditAdapter(dsn=dsn)
        case AuditAdapter.SQLITE:
            from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

            return SQLiteAuditAdapter(path=config.audit.path)
        case AuditAdapter.OTEL:
            from bifrost.adapters.audit.otel import OtelAuditAdapter

            return OtelAuditAdapter(
                otel_endpoint=config.audit.otel.endpoint,
                service_name=config.audit.otel.service_name,
            )
        case _:
            from bifrost.adapters.audit.null import NullAuditAdapter

            return NullAuditAdapter()


# ---------------------------------------------------------------------------
# Cache factory
# ---------------------------------------------------------------------------


def _build_cache(config: BifrostConfig) -> CachePort:
    """Instantiate the configured cache adapter."""
    match config.cache.mode:
        case CacheMode.REDIS:
            from bifrost.adapters.cache.redis_cache import RedisCache

            return RedisCache(redis_url=config.cache.redis_url)
        case CacheMode.MEMORY:
            from bifrost.adapters.cache.memory_cache import MemoryCache

            return MemoryCache(max_entries=config.cache.max_memory_entries)
        case _:
            from bifrost.adapters.cache.disabled import DisabledCache

            return DisabledCache()


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------


def _pricing_overrides(config: BifrostConfig) -> dict[str, ModelPricing]:
    """Build the effective pricing override table from config and optional YAML file.

    Priority (highest wins):
    1. Inline ``pricing`` entries in ``BifrostConfig``.
    2. Entries from ``pricing_file`` (YAML).
    3. Built-in snapshot in ``bifrost.pricing.BUILTIN_PRICING``.
    """
    # Start from the YAML file (lower priority).
    result: dict[str, ModelPricing] = load_pricing_from_yaml(config.pricing_file)

    # Inline config entries override the file.
    for model, override in config.pricing.items():
        result[model] = ModelPricing(
            input_per_million=override.input_per_million,
            output_per_million=override.output_per_million,
            cache_creation_per_million=override.cache_creation_per_million,
            cache_read_per_million=override.cache_read_per_million,
        )
    return result


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config: BifrostConfig) -> FastAPI:
    """Create and return the Bifröst FastAPI application.

    Args:
        config: Gateway configuration (providers, aliases, auth, quotas, etc.).

    Returns:
        A configured ``FastAPI`` instance.
    """
    rule_engine = _build_rule_engine(config)
    key_vault = _build_key_vault(config)
    router = ModelRouter(config, rule_engine=rule_engine, key_vault=key_vault)
    store = _build_usage_store(config)
    cache = _build_cache(config)
    audit = _build_audit(config)
    pricing_overrides = _pricing_overrides(config)
    auth_adapter = build_auth_adapter(config.auth_mode, config.effective_pat_secret())
    event_emitter = _build_event_emitter(config)

    # ── SIGHUP handler — reload keys without restarting ──────────────────────
    def _handle_sighup(signum: int, frame: object) -> None:  # noqa: ARG001
        logger.info("Received SIGHUP — reloading provider keys")
        router.reload_keys()

    try:
        signal.signal(signal.SIGHUP, _handle_sighup)
    except (OSError, ValueError):
        # SIGHUP is not available on Windows or in some restricted environments.
        logger.debug("SIGHUP not available on this platform; key rotation via signal disabled")

    obs_router = create_observability_router(config=config, router=router, store=store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await router.close()
        if hasattr(store, "close"):
            await store.close()
        await event_emitter.close()
        await cache.close()
        await audit.close()
        if hasattr(obs_router, "http_client"):
            await obs_router.http_client.aclose()

    app = FastAPI(
        title="Bifröst LLM Gateway",
        description="Multi-provider LLM gateway with Anthropic-compatible API.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):  # noqa: ANN001
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    @app.get("/health")
    @app.get("/api/v1/bifrost/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    api_router = create_router(
        config=config,
        router=router,
        store=store,
        pricing_overrides=pricing_overrides,
        auth_adapter=auth_adapter,
        event_emitter=event_emitter,
        cache=cache,
        audit=audit,
    )
    # Expose native Bifrost routes for direct service usage and test compatibility,
    # plus the canonical public prefix used by the unified niuu host and ingress.
    app.include_router(api_router)
    app.include_router(api_router, prefix="/api/v1/bifrost")
    app.include_router(obs_router)
    app.include_router(obs_router, prefix="/api/v1/bifrost")

    # Expose the audit adapter on app.state so route handlers can log events.
    app.state.audit = audit

    return app
