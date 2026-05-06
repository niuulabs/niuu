"""Compatibility exports for the niuu host server."""

from niuu.app import (
    _PLUGIN_API_PREFIXES,
    DEFAULT_HOST_PROFILE,
    HOST_PROFILES,
    MountedRouteDomain,
    RootServer,
    SkuldPortRegistry,
    _PrefixRestoreApp,
    available_route_domains,
    build_root_app,
    collect_route_inventory,
    get_skuld_registry,
    parse_enabled_mounts,
    resolve_enabled_mounts,
)

__all__ = [
    "DEFAULT_HOST_PROFILE",
    "HOST_PROFILES",
    "MountedRouteDomain",
    "RootServer",
    "SkuldPortRegistry",
    "_PLUGIN_API_PREFIXES",
    "_PrefixRestoreApp",
    "available_route_domains",
    "build_root_app",
    "collect_route_inventory",
    "get_skuld_registry",
    "parse_enabled_mounts",
    "resolve_enabled_mounts",
]
