"""Tests for ravn.api.persistence_wiring: dynamic adapters + shared pool lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ravn.adapters._lazy_asyncpg_pool import _pool_cache, aclose_all_pools, lazy_pool_for_dsn
from ravn.adapters.budget_ledger import FileBudgetLedger, LazyPostgresBudgetLedger
from ravn.adapters.trigger_store import FileTriggerStore, LazyPostgresTriggerStore
from ravn.api.persistence_wiring import (
    build_budget_ledger,
    build_resident_budget,
    build_trigger_store,
)
from ravn.config import BudgetLedgerConfig, ResidentBudgetConfig, TriggerStoreConfig


@pytest.fixture(autouse=True)
def _clear_pool_cache():
    _pool_cache.clear()
    yield
    _pool_cache.clear()


class TestBuildTriggerStore:
    def test_default_config_is_unconfigured(self, tmp_path) -> None:
        """Opt-in (empty adapter by default — see TriggerStoreConfig): an
        already-deployed chart with no new values set has no store at all."""
        config = TriggerStoreConfig(kwargs={"path": str(tmp_path / "t.json")})
        assert build_trigger_store(config) is None

    def test_explicit_file_adapter_is_loaded_by_dotted_path(self, tmp_path) -> None:
        config = TriggerStoreConfig(
            adapter="ravn.adapters.trigger_store.FileTriggerStore",
            kwargs={"path": str(tmp_path / "t.json")},
        )
        store = build_trigger_store(config)
        assert isinstance(store, FileTriggerStore)

    def test_explicit_postgres_adapter_is_loaded_by_dotted_path(self) -> None:
        config = TriggerStoreConfig(
            adapter="ravn.adapters.trigger_store.LazyPostgresTriggerStore",
            kwargs={"dsn": "postgres://example/db"},
        )
        store = build_trigger_store(config)
        assert isinstance(store, LazyPostgresTriggerStore)

    def test_secret_kwargs_env_resolves_dsn_from_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("TEST_TRIGGER_STORE_DSN", "postgres://from-env/db")
        config = TriggerStoreConfig(
            adapter="ravn.adapters.trigger_store.LazyPostgresTriggerStore",
            kwargs={},
            secret_kwargs_env={"dsn": "TEST_TRIGGER_STORE_DSN"},
        )
        store = build_trigger_store(config)
        assert isinstance(store, LazyPostgresTriggerStore)
        assert store._lazy._dsn == "postgres://from-env/db"


class TestBuildBudgetLedger:
    def test_default_config_is_unconfigured(self, tmp_path) -> None:
        """Opt-in (empty adapter by default — see BudgetLedgerConfig): an
        already-deployed chart with no new values set has no ledger at all."""
        config = BudgetLedgerConfig(kwargs={"path": str(tmp_path / "b.json")})
        assert build_budget_ledger(config) is None

    def test_explicit_file_adapter_is_loaded_by_dotted_path(self, tmp_path) -> None:
        config = BudgetLedgerConfig(
            adapter="ravn.adapters.budget_ledger.FileBudgetLedger",
            kwargs={"path": str(tmp_path / "b.json")},
        )
        ledger = build_budget_ledger(config)
        assert isinstance(ledger, FileBudgetLedger)

    def test_explicit_postgres_adapter_is_loaded_by_dotted_path(self) -> None:
        config = BudgetLedgerConfig(
            adapter="ravn.adapters.budget_ledger.LazyPostgresBudgetLedger",
            kwargs={"dsn": "postgres://example/db"},
        )
        ledger = build_budget_ledger(config)
        assert isinstance(ledger, LazyPostgresBudgetLedger)


class TestBuildResidentBudget:
    def test_empty_adapter_returns_none(self) -> None:
        assert build_resident_budget(ResidentBudgetConfig()) is None

    def test_platform_reporter_is_built_with_a_fake_client(self) -> None:
        from ravn.adapters.resident_budget import PlatformBudgetReporter

        config = ResidentBudgetConfig(
            adapter="ravn.adapters.resident_budget.PlatformBudgetReporter",
            kwargs={"base_url": "https://volundr.internal", "client": AsyncMock()},
        )
        reporter = build_resident_budget(config)
        assert isinstance(reporter, PlatformBudgetReporter)

    def test_default_timeout_seconds_fills_in_when_the_operator_did_not_set_one(self) -> None:
        """The client (client_from_workload_identity) must see the SAME
        timeout ApiTriggerSource's client uses (settings.gateway.platform
        .timeout) rather than its own, independent 30s default — see
        ravn.cli.trigger_wiring."""
        seen_kwargs: dict = {}

        class _RecordingReporter:
            def __init__(self, **kwargs):
                seen_kwargs.update(kwargs)

        import ravn.api.persistence_wiring as wiring_module

        original_import_class = wiring_module.import_class
        wiring_module.import_class = lambda path: _RecordingReporter  # noqa: ARG005
        try:
            config = ResidentBudgetConfig(
                adapter="does.not.matter.AtAll",
                kwargs={"base_url": "https://volundr.internal"},
            )
            build_resident_budget(config, default_timeout_seconds=12.5)
        finally:
            wiring_module.import_class = original_import_class

        assert seen_kwargs["timeout_seconds"] == 12.5

    def test_operator_supplied_timeout_seconds_is_not_overridden(self) -> None:
        """config-first: an explicit kwarg always wins over the derived default."""
        seen_kwargs: dict = {}

        class _RecordingReporter:
            def __init__(self, **kwargs):
                seen_kwargs.update(kwargs)

        import ravn.api.persistence_wiring as wiring_module

        original_import_class = wiring_module.import_class
        wiring_module.import_class = lambda path: _RecordingReporter  # noqa: ARG005
        try:
            config = ResidentBudgetConfig(
                adapter="does.not.matter.AtAll",
                kwargs={"timeout_seconds": 5.0},
            )
            build_resident_budget(config, default_timeout_seconds=99.0)
        finally:
            wiring_module.import_class = original_import_class

        assert seen_kwargs["timeout_seconds"] == 5.0


class TestSharedPoolLifecycle:
    async def test_trigger_store_and_budget_ledger_on_the_same_dsn_share_one_pool(
        self, monkeypatch
    ) -> None:
        created_pools = []

        async def fake_create_pool(*, dsn, min_size, max_size):
            del min_size, max_size
            pool = AsyncMock()
            pool.dsn = dsn
            created_pools.append(pool)
            return pool

        import asyncpg

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)

        trigger_store = build_trigger_store(
            TriggerStoreConfig(
                adapter="ravn.adapters.trigger_store.LazyPostgresTriggerStore",
                kwargs={"dsn": "postgres://shared/db"},
            )
        )
        budget_ledger = build_budget_ledger(
            BudgetLedgerConfig(
                adapter="ravn.adapters.budget_ledger.LazyPostgresBudgetLedger",
                kwargs={"dsn": "postgres://shared/db"},
            )
        )

        await trigger_store._lazy.resolve()
        await budget_ledger._lazy.resolve()

        assert len(created_pools) == 1

    async def test_different_dsns_get_different_pools(self, monkeypatch) -> None:
        created = []

        async def fake_create_pool(*, dsn, min_size, max_size):
            del min_size, max_size
            created.append(dsn)
            return AsyncMock()

        import asyncpg

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)

        await lazy_pool_for_dsn("postgres://a/db").resolve()
        await lazy_pool_for_dsn("postgres://b/db").resolve()

        assert created == ["postgres://a/db", "postgres://b/db"]

    async def test_aclose_all_pools_closes_each_pool_exactly_once(self, monkeypatch) -> None:
        pool = AsyncMock()

        async def fake_create_pool(*, dsn, min_size, max_size):
            del dsn, min_size, max_size
            return pool

        import asyncpg

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)

        holder_a = lazy_pool_for_dsn("postgres://shared/db")
        holder_b = lazy_pool_for_dsn("postgres://shared/db")
        assert holder_a is holder_b
        await holder_a.resolve()

        await aclose_all_pools()

        pool.close.assert_awaited_once()

    async def test_aclose_all_pools_is_safe_with_nothing_ever_connected(self) -> None:
        await aclose_all_pools()  # must not raise
