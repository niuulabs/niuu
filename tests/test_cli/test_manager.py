"""Tests for cli.services.manager — ServiceManager."""

from __future__ import annotations

import pytest

from cli.registry import PluginRegistry
from cli.services.manager import (
    CircularDependencyError,
    ServiceManager,
    ServiceState,
    StartupError,
)
from tests.test_cli.conftest import FakePlugin, StubService


@pytest.fixture
def manager(registry: PluginRegistry) -> ServiceManager:
    return ServiceManager(
        registry=registry,
        health_check_interval=0.01,
        health_check_timeout=1.0,
        health_check_max_retries=3,
    )


class TestDependencyResolution:
    """Tests for topological sort and dependency resolution."""

    def test_no_deps(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b"))
        order = manager.resolve_start_order()
        assert set(order) == {"a", "b"}

    def test_linear_deps(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b", deps=["a"]))
        registry.register(FakePlugin(name="c", deps=["b"]))
        order = manager.resolve_start_order()
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_diamond_deps(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b", deps=["a"]))
        registry.register(FakePlugin(name="c", deps=["a"]))
        registry.register(FakePlugin(name="d", deps=["b", "c"]))
        order = manager.resolve_start_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_circular_dependency(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        registry.register(FakePlugin(name="a", deps=["b"]))
        registry.register(FakePlugin(name="b", deps=["a"]))
        with pytest.raises(CircularDependencyError, match="circular dependency"):
            manager.resolve_start_order()

    def test_only_filters_to_service_and_deps(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b", deps=["a"]))
        registry.register(FakePlugin(name="c"))
        order = manager.resolve_start_order(only="b")
        assert "a" in order
        assert "b" in order
        assert "c" not in order

    def test_only_single_no_deps(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b"))
        order = manager.resolve_start_order(only="a")
        assert order == ["a"]

    def test_ignores_deps_on_disabled_plugins(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b", deps=["a"]))
        registry.disable("a")
        # "a" is disabled, so "b" should have no deps resolved
        order = manager.resolve_start_order()
        assert order == ["b"]

    def test_enabled_services_filters_and_includes_deps(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b", deps=["a"]))
        registry.register(FakePlugin(name="c"))
        # Request only "b" via enabled_services — should pull in "a" as dep
        order = manager.resolve_start_order(enabled_services={"b"})
        assert "a" in order
        assert "b" in order
        assert "c" not in order

    def test_enabled_services_multiple(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b", deps=["a"]))
        registry.register(FakePlugin(name="c"))
        order = manager.resolve_start_order(enabled_services={"b", "c"})
        assert "a" in order
        assert "b" in order
        assert "c" in order

    def test_enabled_services_empty_set_starts_nothing(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b"))
        order = manager.resolve_start_order(enabled_services=set())
        assert order == []


class TestServiceLifecycle:
    """Tests for start/stop and health checks."""

    async def test_start_all(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        svc = StubService()
        registry.register(FakePlugin(name="a", service=svc))
        await manager.start_all()
        assert svc.started is True
        assert manager.services["a"].state == ServiceState.HEALTHY

    async def test_start_unhealthy(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        svc = StubService(healthy=False)
        registry.register(FakePlugin(name="a", service=svc))
        with pytest.raises(StartupError, match="health check failed"):
            await manager.start_all()
        assert svc.started is True
        assert manager.services["a"].state == ServiceState.UNHEALTHY

    async def test_start_unhealthy_no_rollback(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        svc = StubService(healthy=False)
        registry.register(FakePlugin(name="a", service=svc))
        await manager.start_all(rollback_on_failure=False)
        assert svc.started is True
        assert manager.services["a"].state == ServiceState.UNHEALTHY

    async def test_stop_all(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        svc = StubService()
        registry.register(FakePlugin(name="a", service=svc))
        await manager.start_all()
        await manager.stop_all()
        assert svc.stopped is True
        assert manager.services["a"].state == ServiceState.STOPPED

    async def test_start_with_only(self, registry: PluginRegistry, manager: ServiceManager) -> None:
        svc_a = StubService()
        svc_b = StubService()
        svc_c = StubService()
        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        registry.register(FakePlugin(name="c", service=svc_c))
        await manager.start_all(only="b")
        assert svc_a.started is True
        assert svc_b.started is True
        assert svc_c.started is False

    async def test_start_with_enabled_services(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        svc_a = StubService()
        svc_b = StubService()
        svc_c = StubService()
        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        registry.register(FakePlugin(name="c", service=svc_c))
        await manager.start_all(enabled_services={"b"})
        assert svc_a.started is True  # pulled in as dep of b
        assert svc_b.started is True
        assert svc_c.started is False  # not in enabled_services

    async def test_stop_all_after_only_stops_started_only(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        """stop_all after start_all(only=...) only stops what was actually started."""
        svc_a = StubService()
        svc_b = StubService()
        svc_c = StubService()
        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        registry.register(FakePlugin(name="c", service=svc_c))
        await manager.start_all(only="b")
        await manager.stop_all()
        assert svc_a.stopped is True
        assert svc_b.stopped is True
        assert svc_c.stopped is False

    async def test_plugin_without_service(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        await manager.start_all()
        assert manager.services["a"].state == ServiceState.HEALTHY

    async def test_stop_handles_errors(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        svc = StubService()
        registry.register(FakePlugin(name="a", service=svc))
        await manager.start_all()

        async def bad_stop() -> None:
            raise RuntimeError("stop failed")

        svc.stop = bad_stop  # type: ignore[assignment]
        await manager.stop_all()
        assert manager.services["a"].state == ServiceState.STOPPED

    async def test_services_property_is_copy(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        await manager.start_all()
        services = manager.services
        services.clear()
        assert len(manager.services) == 1


class TestRollback:
    """Tests for rollback behavior on startup failure."""

    async def test_rollback_stops_started_services(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        svc_a = StubService()
        svc_b = StubService(healthy=False)
        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        with pytest.raises(StartupError, match="b"):
            await manager.start_all()
        # a was started then rolled back
        assert svc_a.started is True
        assert svc_a.stopped is True
        # b was started but unhealthy, not in "started" list for rollback
        assert svc_b.started is True

    async def test_rollback_on_start_exception(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        svc_a = StubService()
        svc_b = StubService()

        async def bad_start() -> None:
            raise RuntimeError("boom")

        svc_b.start = bad_start  # type: ignore[assignment]

        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        with pytest.raises(StartupError, match="b"):
            await manager.start_all()
        assert svc_a.stopped is True

    async def test_rollback_handles_stop_errors(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        svc_a = StubService()

        async def bad_stop() -> None:
            raise RuntimeError("stop failed")

        svc_a.stop = bad_stop  # type: ignore[assignment]

        svc_b = StubService(healthy=False)
        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        # Should not raise from the rollback stop error
        with pytest.raises(StartupError, match="b"):
            await manager.start_all()

    async def test_no_rollback_when_disabled(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        svc_a = StubService()
        svc_b = StubService(healthy=False)
        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        await manager.start_all(rollback_on_failure=False)
        assert svc_a.stopped is False


class TestStatusCallback:
    """Tests for on_status_change callback."""

    async def test_callback_called_on_transitions(self, registry: PluginRegistry) -> None:
        events: list[tuple[str, ServiceState]] = []

        def callback(name: str, state: ServiceState) -> None:
            events.append((name, state))

        mgr = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=1.0,
            health_check_max_retries=3,
            on_status_change=callback,
        )
        svc = StubService()
        registry.register(FakePlugin(name="a", service=svc))
        await mgr.start_all()
        assert ("a", ServiceState.STARTING) in events
        assert ("a", ServiceState.HEALTHY) in events

    async def test_callback_on_stop(self, registry: PluginRegistry) -> None:
        events: list[tuple[str, ServiceState]] = []

        def callback(name: str, state: ServiceState) -> None:
            events.append((name, state))

        mgr = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=1.0,
            health_check_max_retries=3,
            on_status_change=callback,
        )
        svc = StubService()
        registry.register(FakePlugin(name="a", service=svc))
        await mgr.start_all()
        events.clear()
        await mgr.stop_all()
        assert ("a", ServiceState.STOPPING) in events
        assert ("a", ServiceState.STOPPED) in events

    async def test_callback_on_rollback(self, registry: PluginRegistry) -> None:
        events: list[tuple[str, ServiceState]] = []

        def callback(name: str, state: ServiceState) -> None:
            events.append((name, state))

        mgr = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=1.0,
            health_check_max_retries=3,
            on_status_change=callback,
        )
        svc_a = StubService()
        svc_b = StubService(healthy=False)
        registry.register(FakePlugin(name="a", service=svc_a))
        registry.register(FakePlugin(name="b", deps=["a"], service=svc_b))
        with pytest.raises(StartupError):
            await mgr.start_all()
        # a should have been rolled back
        assert ("a", ServiceState.STOPPING) in events
        assert ("a", ServiceState.STOPPED) in events

    async def test_no_callback_when_none(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        # Default manager has no callback — should not crash
        svc = StubService()
        registry.register(FakePlugin(name="a", service=svc))
        await manager.start_all()
        assert manager.services["a"].state == ServiceState.HEALTHY


class TestStartOrder:
    """Tests for start_order property."""

    async def test_start_order_recorded(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        registry.register(FakePlugin(name="b", deps=["a"]))
        await manager.start_all()
        assert manager.start_order == ["a", "b"]

    def test_start_order_empty_before_start(self, manager: ServiceManager) -> None:
        assert manager.start_order == []

    async def test_start_order_is_copy(
        self, registry: PluginRegistry, manager: ServiceManager
    ) -> None:
        registry.register(FakePlugin(name="a"))
        await manager.start_all()
        order = manager.start_order
        order.clear()
        assert len(manager.start_order) == 1


class TestStartupError:
    """Tests for StartupError exception."""

    def test_startup_error_attributes(self) -> None:
        err = StartupError("ting", "connection refused")
        assert err.service_name == "ting"
        assert "ting" in str(err)
        assert "connection refused" in str(err)
