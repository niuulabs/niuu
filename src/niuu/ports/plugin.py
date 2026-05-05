"""Plugin port — shared ABCs for the niuu CLI plugin system.

These live in niuu so that both volundr and tyr can implement
ServicePlugin without importing from cli (which would create a
bidirectional dependency).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import typer
    from textual.widget import Widget


class Service(ABC):
    """Lifecycle interface for a managed service."""

    @abstractmethod
    async def start(self) -> None:
        """Start the service."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the service gracefully."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the service is healthy."""


@dataclass
class ServiceDefinition:
    """Describes a managed service exposed by a plugin.

    Returned by ServicePlugin.register_service(). The CLI uses this to:
    - Build --<name>/--no-<name> flags on ``niuu platform up``
    - Resolve dependency order for startup
    - Apply per-service config overrides (enabled, port)
    """

    name: str
    """Unique service name; becomes --<name> flag on platform up."""

    description: str
    """One-line description shown in --help."""

    factory: Callable[[], Service]
    """Zero-arg callable that creates a fresh Service instance."""

    default_enabled: bool = True
    """Whether this service is enabled when no config or flag overrides it."""

    depends_on: list[str] = field(default_factory=list)
    """Names of other services that must start before this one."""

    default_port: int = 0
    """Default listen port (0 means no fixed port)."""


@dataclass(frozen=True)
class TUIPageSpec:
    """Specification for a TUI page registered by a plugin."""

    name: str
    icon: str
    widget_class: type[Widget]


@dataclass(frozen=True)
class APIRouteDomain:
    """Declarative route-domain metadata for host mounting."""

    name: str
    prefixes: tuple[str, ...]
    description: str = ""


class ServicePlugin(ABC):
    """Base class for niuu CLI plugins.

    Each package (volundr, tyr, etc.) implements this to register its
    commands, services, API clients, and TUI pages.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g. 'volundr')."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description of the plugin."""

    def register_service(self) -> ServiceDefinition | None:
        """Return a ServiceDefinition for this plugin's managed service.

        The definition is used to build dynamic service flags on
        ``niuu platform up`` and to resolve startup order.

        Return None if this plugin does not manage a service.
        """
        return None

    def create_service(self) -> Service | None:
        """Create the managed service instance, or None if not applicable.

        Plugins that implement register_service() should delegate to
        ``register_service().factory()`` here for consistency.
        """
        return None

    def register_commands(self, app: typer.Typer) -> None:
        """Register CLI commands on the given Typer app."""

    def create_api_app(self) -> Any:
        """Create a FastAPI/ASGI sub-application for this plugin.

        Returned app is mounted into the root server. Routes should use
        their own ``/api/v1/<plugin>/`` prefix to avoid collisions.
        Return None if this plugin does not expose HTTP endpoints.
        """
        return None

    def api_route_domains(self) -> Sequence[APIRouteDomain]:
        """Return declarative route-domain definitions for the API app."""
        return []

    def create_api_client(self) -> Any:
        """Create an async API client for this service, or None."""
        return None

    def tui_pages(self) -> Sequence[TUIPageSpec]:
        """Return TUI page specs for the Textual app."""
        return []

    def depends_on(self) -> Sequence[str]:
        """Return names of plugins this plugin depends on for startup."""
        svc_def = self.register_service()
        if svc_def is not None:
            return svc_def.depends_on
        return []
