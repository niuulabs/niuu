"""Config-first, dynamic-adapter construction of the Ravn API's durable stores.

``TriggerStoreConfig``/``BudgetLedgerConfig`` name an ``adapter:`` class path
plus ``kwargs`` (dynamic-adapters.md), not a database_url/store_path pair.
Both are opt-in: an empty ``adapter`` (the default) means this Ravn API has
no durable store at all, and the routes that would need one return 503 —
exactly how every already-deployed chart behaves today, with no new values
set. The chart renders the file adapter
(``ravn.adapters.trigger_store.FileTriggerStore`` /
``ravn.adapters.budget_ledger.FileBudgetLedger``) when
``persistence.enabled: true``, and the Postgres adapter
(``ravn.adapters.trigger_store.LazyPostgresTriggerStore`` /
``ravn.adapters.budget_ledger.LazyPostgresBudgetLedger``, both of which
connect lazily and share one pool per DSN — see
``ravn.adapters._lazy_asyncpg_pool``) when ``database.enabled: true``.
"""

from __future__ import annotations

from niuu.utils import import_class, resolve_secret_kwargs
from ravn.adapters._lazy_asyncpg_pool import aclose_all_pools
from ravn.config import BudgetLedgerConfig, ResidentBudgetConfig, TriggerStoreConfig
from ravn.ports.budget_ledger import BudgetLedgerPort
from ravn.ports.resident_budget import ResidentBudgetPort
from ravn.ports.trigger_store import TriggerStorePort


def build_trigger_store(config: TriggerStoreConfig) -> TriggerStorePort | None:
    """Instantiate ``config.adapter`` with ``config.kwargs`` (+ secrets), or
    ``None`` when unconfigured — this Ravn API's decision to run without a
    durable trigger store (see ``TriggerStoreConfig``)."""
    if not config.adapter:
        return None
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    return cls(**kwargs)


def build_budget_ledger(config: BudgetLedgerConfig) -> BudgetLedgerPort | None:
    """Instantiate ``config.adapter`` with ``config.kwargs`` (+ secrets), or
    ``None`` when unconfigured — this Ravn API's decision to run without a
    durable budget ledger (see ``BudgetLedgerConfig``)."""
    if not config.adapter:
        return None
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    return cls(**kwargs)


def build_resident_budget(
    config: ResidentBudgetConfig, *, default_timeout_seconds: float | None = None
) -> ResidentBudgetPort | None:
    """Instantiate ``config.adapter`` with ``config.kwargs`` (+ secrets), or
    ``None`` when unconfigured — the operator's decision to run without
    durable budget reporting (see ``ResidentBudgetConfig``).

    *default_timeout_seconds* (pass ``settings.gateway.platform.timeout``,
    the same value ``ApiTriggerSource``'s HTTP client already uses — see
    ``ravn.cli.trigger_wiring``) seeds ``kwargs["timeout_seconds"]`` when the
    operator has not set one explicitly, so ``PlatformBudgetReporter``'s
    client does not drift from every other resident-side platform client's
    timeout by way of its own, independent 30s default.
    """
    if not config.adapter:
        return None
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    if default_timeout_seconds is not None and "timeout_seconds" not in kwargs:
        kwargs["timeout_seconds"] = default_timeout_seconds
    return cls(**kwargs)


async def aclose_stores() -> None:
    """Close every Postgres pool this process opened, exactly once each.

    Both ``build_trigger_store`` and ``build_budget_ledger`` may resolve to a
    Lazy...Store that shares its pool (by DSN) with the other — closing
    through the shared registry, rather than calling a per-store ``aclose``,
    is what guarantees one close per pool no matter how many stores point at
    the same DSN.
    """
    await aclose_all_pools()
