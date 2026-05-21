"""Plugin registry — discovers and manages niuu CLI plugins.

Plugins register themselves via entry points (niuu.plugins group) or
config YAML (dynamic adapter pattern with fully-qualified class paths).
Each plugin brings its own commands, services, API clients, and TUI pages.

The plugin ABCs (ServicePlugin, Service, TUIPageSpec) live in
niuu.ports.plugin so that volundr/ting can implement them without
importing from cli. They are re-exported here for convenience.
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import Any

from niuu.ports.plugin import Service, ServicePlugin, TUIPageSpec
from niuu.utils import import_class

__all__ = ["Service", "ServicePlugin", "TUIPageSpec", "PluginRegistry"]

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "niuu.plugins"


class PluginRegistry:
    """Discovers and manages CLI plugins.

    Two discovery mechanisms:
    1. Entry points in pyproject.toml (niuu.plugins group)
    2. Config-driven: plugins list with adapter class path + kwargs
    """

    def __init__(self) -> None:
        self._plugins: dict[str, ServicePlugin] = {}
        self._disabled: set[str] = set()

    @property
    def plugins(self) -> dict[str, ServicePlugin]:
        """Return all registered (enabled) plugins."""
        return {k: v for k, v in self._plugins.items() if k not in self._disabled}

    @property
    def all_plugins(self) -> dict[str, ServicePlugin]:
        """Return all registered plugins including disabled ones."""
        return dict(self._plugins)

    def register(self, plugin: ServicePlugin) -> None:
        """Register a plugin instance."""
        self._plugins[plugin.name] = plugin
        logger.debug("registered plugin: %s", plugin.name)

    def disable(self, name: str) -> None:
        """Disable a plugin by name."""
        self._disabled.add(name)
        logger.debug("disabled plugin: %s", name)

    def enable(self, name: str) -> None:
        """Enable a previously disabled plugin."""
        self._disabled.discard(name)

    def is_enabled(self, name: str) -> bool:
        """Check if a plugin is registered and enabled."""
        return name in self._plugins and name not in self._disabled

    def get(self, name: str) -> ServicePlugin | None:
        """Get an enabled plugin by name."""
        if name in self._disabled:
            return None
        return self._plugins.get(name)

    def discover_entry_points(self) -> None:
        """Discover plugins from the niuu.plugins entry point group."""
        eps = importlib.metadata.entry_points()
        if hasattr(eps, "select"):
            group = eps.select(group=ENTRY_POINT_GROUP)
        else:
            group = eps.get(ENTRY_POINT_GROUP, [])
        for ep in group:
            try:
                plugin_cls = ep.load()
                plugin = plugin_cls()
                self.register(plugin)
            except Exception:
                logger.exception("failed to load plugin from entry point: %s", ep.name)

    def discover_config(self, plugin_configs: list[dict[str, Any]]) -> None:
        """Discover plugins from config YAML (dynamic adapter pattern).

        Each config dict must have an 'adapter' key with a fully-qualified
        class path. Remaining keys are passed as kwargs to the constructor.
        """
        for cfg in plugin_configs:
            adapter_path = cfg.get("adapter")
            if not adapter_path:
                logger.warning("plugin config missing 'adapter' key: %s", cfg)
                continue
            try:
                cls = import_class(adapter_path)
                kwargs = {k: v for k, v in cfg.items() if k != "adapter"}
                plugin = cls(**kwargs)
                self.register(plugin)
            except Exception:
                logger.exception("failed to load plugin from config: %s", adapter_path)

    def apply_config(self, enabled_plugins: dict[str, bool] | None = None) -> None:
        """Apply enabled/disabled config to registered plugins."""
        if not enabled_plugins:
            return
        for name, enabled in enabled_plugins.items():
            if not enabled:
                self.disable(name)
            else:
                self.enable(name)
