"""Generic dynamic-adapter composition helpers.

Implements the shared ``adapter:`` dotted-path + kwargs configuration pattern
(see ``.claude/rules/dynamic-adapters.md``). Discovery composition modules
(warden discovery, resident discovery, ...) build their adapter chains through
these helpers instead of duplicating the import/kwargs plumbing.
"""

from __future__ import annotations

import importlib
import inspect
import os
from typing import Any


def build_dynamic_adapter(
    adapter_config: Any,
    *,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any | None:
    """Instantiate the adapter described by a dynamic adapter config entry.

    Returns None when the entry carries no adapter path. ``extra_kwargs`` are
    offered as defaults only when the adapter constructor accepts them.
    """
    adapter_path = str(config_value(adapter_config, "adapter", "")).strip()
    if not adapter_path:
        return None

    kwargs = adapter_kwargs(adapter_config)
    kwargs.update(secret_kwargs(adapter_config))
    adapter_cls = import_adapter(adapter_path)
    for name, value in (extra_kwargs or {}).items():
        if accepts_kwarg(adapter_cls, name):
            kwargs.setdefault(name, value)
    return adapter_cls(**kwargs)


def adapter_kwargs(adapter_config: Any) -> dict[str, Any]:
    """Return constructor kwargs from an adapter config entry."""
    if hasattr(adapter_config, "adapter_kwargs"):
        return dict(adapter_config.adapter_kwargs())
    if hasattr(adapter_config, "model_dump"):
        payload = adapter_config.model_dump()
    elif isinstance(adapter_config, dict):
        payload = dict(adapter_config)
    else:
        payload = dict(getattr(adapter_config, "__dict__", {}))
    nested = payload.pop("kwargs", {}) or {}
    payload.pop("adapter", None)
    payload.pop("secret_kwargs_env", None)
    return {**nested, **payload}


def secret_kwargs(adapter_config: Any) -> dict[str, str]:
    """Resolve secret kwargs from the environment names the config maps."""
    secret_env = config_value(adapter_config, "secret_kwargs_env", {}) or {}
    resolved: dict[str, str] = {}
    for key, env_name in dict(secret_env).items():
        value = os.environ.get(str(env_name), "").strip()
        if value:
            resolved[str(key)] = value
    return resolved


def config_value(adapter_config: Any, key: str, default: Any) -> Any:
    """Read one key from a dict- or attribute-style adapter config entry."""
    if isinstance(adapter_config, dict):
        return adapter_config.get(key, default)
    return getattr(adapter_config, key, default)


def import_adapter(adapter_path: str) -> type:
    """Import and return the class referenced by a dotted adapter path."""
    module_name, _, class_name = adapter_path.rpartition(".")
    if not module_name or not class_name:
        msg = f"Invalid adapter path: {adapter_path}"
        raise ValueError(msg)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def accepts_kwarg(adapter_cls: type, name: str) -> bool:
    """Return True when the adapter constructor accepts the named kwarg."""
    signature = inspect.signature(adapter_cls)
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name:
            return True
    return False
