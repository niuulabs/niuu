"""ServiceManager — orchestrates service startup via dependency graph.

Topological sort for start order, reverse for shutdown.
Health checks with backoff before declaring ready.
On failure: rollback already-started services in reverse order.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from cli.registry import PluginRegistry
from niuu.ports.plugin import Service, ServiceLifecycle

logger = logging.getLogger(__name__)


class ServiceState(Enum):
    """Current state of a managed service."""

    STOPPED = "stopped"
    HOSTED = "hosted"
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"


class CircularDependencyError(Exception):
    """Raised when a circular dependency is detected in the plugin graph."""


class StartupError(Exception):
    """Raised when a service fails to start or become healthy."""

    def __init__(self, service_name: str, message: str) -> None:
        self.service_name = service_name
        super().__init__(f"service '{service_name}' failed to start: {message}")


# Callback signature: (service_name, state) -> None
StatusCallback = Callable[[str, ServiceState], None]


@dataclass
class ServiceStatus:
    """Status of a single managed service."""

    name: str
    state: ServiceState = ServiceState.STOPPED
    service: Service | None = None


class ServiceManager:
    """Manages service lifecycle with dependency resolution."""

    def __init__(
        self,
        registry: PluginRegistry,
        health_check_interval: float = 2.0,
        health_check_timeout: float = 30.0,
        health_check_max_retries: int = 15,
        on_status_change: StatusCallback | None = None,
    ) -> None:
        self._registry = registry
        self._health_check_interval = health_check_interval
        self._health_check_timeout = health_check_timeout
        self._health_check_max_retries = health_check_max_retries
        self._services: dict[str, ServiceStatus] = {}
        self._start_order: list[str] = []
        self._on_status_change = on_status_change

    @property
    def services(self) -> dict[str, ServiceStatus]:
        """Return current service statuses."""
        return dict(self._services)

    @property
    def start_order(self) -> list[str]:
        """Return the last resolved start order."""
        return list(self._start_order)

    def _notify(self, name: str, state: ServiceState) -> None:
        """Notify the status callback if set."""
        if self._on_status_change:
            self._on_status_change(name, state)

    def resolve_start_order(
        self,
        only: str | None = None,
        enabled_services: set[str] | None = None,
    ) -> list[str]:
        """Resolve topological start order for enabled plugins.

        Parameters
        ----------
        only:
            If provided, starts only this single service and its dependencies.
            Mutually exclusive with ``enabled_services``.
        enabled_services:
            If provided, starts exactly these services plus their transitive
            dependencies. Takes precedence over ``only``.

        Raises CircularDependencyError if a cycle is detected.
        """
        plugins = self._registry.plugins
        graph: dict[str, list[str]] = {}
        for name, plugin in plugins.items():
            deps = [d for d in plugin.depends_on() if d in plugins]
            graph[name] = deps

        if enabled_services is not None:
            needed: set[str] = set()
            for svc_name in enabled_services:
                needed.update(self._collect_deps(svc_name, graph))
            graph = {k: v for k, v in graph.items() if k in needed}
        elif only:
            needed = self._collect_deps(only, graph)
            graph = {k: v for k, v in graph.items() if k in needed}

        return self._topological_sort(graph)

    def _collect_deps(self, name: str, graph: dict[str, list[str]]) -> set[str]:
        """Collect all transitive dependencies of a service."""
        result: set[str] = set()
        stack = [name]
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            for dep in graph.get(current, []):
                stack.append(dep)
        return result

    def _topological_sort(self, graph: dict[str, list[str]]) -> list[str]:
        """Kahn's algorithm for topological sort. Detects cycles."""
        adj: dict[str, list[str]] = {n: [] for n in graph}
        for node, deps in graph.items():
            for dep in deps:
                if dep not in adj:
                    adj[dep] = []
                adj[dep].append(node)

        in_deg: dict[str, int] = {n: 0 for n in adj}
        for node, deps in graph.items():
            in_deg[node] = len(deps)
            if node not in adj:
                adj[node] = []

        queue = [n for n in in_deg if in_deg[n] == 0]
        result: list[str] = []

        while queue:
            queue.sort()
            node = queue.pop(0)
            result.append(node)
            for dependent in adj.get(node, []):
                in_deg[dependent] -= 1
                if in_deg[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(in_deg):
            missing = set(in_deg) - set(result)
            raise CircularDependencyError(
                f"circular dependency detected among: {', '.join(sorted(missing))}"
            )

        return result

    async def start_all(
        self,
        only: str | None = None,
        enabled_services: set[str] | None = None,
        rollback_on_failure: bool = True,
    ) -> None:
        """Start services in dependency order.

        Parameters
        ----------
        only:
            Start only this single service and its dependencies.
        enabled_services:
            Start exactly these services plus their transitive dependencies.
            Takes precedence over ``only``.
        rollback_on_failure:
            If True and a service fails to become healthy, all previously
            started services are stopped in reverse order.
        """
        order = self.resolve_start_order(only=only, enabled_services=enabled_services)
        self._start_order = order
        plugins = self._registry.plugins
        started: list[str] = []

        for name in order:
            plugin = plugins.get(name)
            if not plugin:
                continue
            definition = plugin.register_service()
            service = plugin.create_service()
            if not service:
                state = (
                    ServiceState.HOSTED
                    if definition is not None and definition.lifecycle is ServiceLifecycle.HOSTED
                    else ServiceState.HEALTHY
                )
                self._services[name] = ServiceStatus(name=name, state=state)
                self._notify(name, state)
                started.append(name)
                continue

            status = ServiceStatus(name=name, state=ServiceState.STARTING, service=service)
            self._services[name] = status
            self._notify(name, ServiceState.STARTING)

            try:
                await service.start()
            except Exception as exc:
                status.state = ServiceState.UNHEALTHY
                self._notify(name, ServiceState.UNHEALTHY)
                if rollback_on_failure:
                    await self._rollback(started)
                raise StartupError(name, str(exc)) from exc

            healthy = await self._wait_healthy(name, service)
            if healthy:
                status.state = ServiceState.HEALTHY
                self._notify(name, ServiceState.HEALTHY)
                started.append(name)
                continue

            status.state = ServiceState.UNHEALTHY
            self._notify(name, ServiceState.UNHEALTHY)
            if rollback_on_failure:
                await self._rollback(started)
                raise StartupError(name, "health check failed")

    async def stop_all(self) -> None:
        """Stop services in reverse order of what was actually started."""
        order = self._start_order if self._start_order else self.resolve_start_order()
        await self._stop_services(list(reversed(order)))

    async def _rollback(self, started: list[str]) -> None:
        """Stop already-started services in reverse order after a failure."""
        await self._stop_services(list(reversed(started)))

    async def _stop_services(self, names: list[str]) -> None:
        """Stop the given services in the order provided."""
        for name in names:
            status = self._services.get(name)
            if not status or not status.service:
                continue
            status.state = ServiceState.STOPPING
            self._notify(name, ServiceState.STOPPING)
            try:
                await status.service.stop()
            except Exception:
                logger.exception("error stopping service: %s", name)
            status.state = ServiceState.STOPPED
            self._notify(name, ServiceState.STOPPED)

    async def _wait_healthy(self, name: str, service: Service) -> bool:
        """Poll health check until healthy, timeout, or max retries."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._health_check_timeout
        for attempt in range(self._health_check_max_retries):
            if loop.time() > deadline:
                break
            try:
                if await service.health_check():
                    return True
            except Exception:
                logger.debug("health check failed for %s (attempt %d)", name, attempt + 1)
            await asyncio.sleep(self._health_check_interval)
        logger.error("service %s failed to become healthy", name)
        return False
