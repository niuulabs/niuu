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

__all__ = ["PluginLoadError", "PluginRegistry", "Service", "ServicePlugin", "TUIPageSpec"]

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "niuu.plugins"


class PluginLoadError(RuntimeError):
    """An installed or configured plugin could not be loaded.

    A plugin that fails to load is never skipped: the platform would start
    without one of its services and nothing would say so. Disable it in
    ``plugins.enabled`` (an operator decision), or fix what it needs.
    """


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

    def discover_entry_points(self, disabled: set[str] | frozenset[str] = frozenset()) -> None:
        """Discover plugins from the niuu.plugins entry point group.

        Entry points named in *disabled* (``plugins.enabled: {name: false}``)
        are not loaded at all. Any other entry point that fails to load raises
        :class:`PluginLoadError`.
        """
        eps = importlib.metadata.entry_points()
        if hasattr(eps, "select"):
            group = eps.select(group=ENTRY_POINT_GROUP)
        else:
            group = eps.get(ENTRY_POINT_GROUP, [])
        for ep in group:
            if ep.name in disabled:
                logger.debug("plugin %s disabled in config; not loading it", ep.name)
                continue
            try:
                plugin_cls = ep.load()
                plugin = plugin_cls()
            except Exception as exc:
                raise PluginLoadError(
                    f"plugin {ep.name!r} (entry point {ep.value!r}) failed to load: {exc}. "
                    f"Fix its installation, or set plugins.enabled.{ep.name}: false to run "
                    "without it"
                ) from exc
            self.register(plugin)

    def discover_config(self, plugin_configs: list[dict[str, Any]]) -> None:
        """Discover plugins from config YAML (dynamic adapter pattern).

        Each config dict must have an 'adapter' key with a fully-qualified
        class path. Remaining keys are passed as kwargs to the constructor.
        """
        for cfg in plugin_configs:
            adapter_path = cfg.get("adapter")
            if not adapter_path:
                raise PluginLoadError(
                    f"plugins.extra entry has no 'adapter' key: {cfg!r}. Give it the "
                    "fully-qualified plugin class path, or remove the entry"
                )
            kwargs = {k: v for k, v in cfg.items() if k != "adapter"}
            try:
                cls = import_class(adapter_path)
                plugin = cls(**kwargs)
            except Exception as exc:
                raise PluginLoadError(
                    f"plugin {adapter_path!r} from plugins.extra failed to load: {exc}. "
                    "Fix the class path or its arguments, or remove the entry"
                ) from exc
            self.register(plugin)

    def apply_config(self, enabled_plugins: dict[str, bool] | None = None) -> None:
        """Apply enabled/disabled config to registered plugins."""
        if not enabled_plugins:
            return
        for name, enabled in enabled_plugins.items():
            if not enabled:
                self.disable(name)
            else:
                self.enable(name)
