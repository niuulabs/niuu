"""Composition helpers for Warden discovery adapters."""

from __future__ import annotations

import importlib
import inspect
import logging
import os
from typing import Any

from ravn.adapters.warden_discovery.spec import WardenSpecDiscoveryAdapter
from ravn.ports.warden_discovery import WardenDiscoveryPort
from ravn.warden.models import WardenSpec
from ravn.warden.store import WardenStore

logger = logging.getLogger(__name__)


class CompositeWardenDiscoveryAdapter:
    """Combine multiple Warden discovery sources into one read model."""

    def __init__(self, adapters: list[WardenDiscoveryPort]) -> None:
        self._adapters = adapters

    async def list_wardens(self) -> list[WardenSpec]:
        """Return wardens from all adapters, de-duplicated by id."""
        by_id: dict[str, WardenSpec] = {}
        for adapter in self._adapters:
            try:
                wardens = await adapter.list_wardens()
            except Exception as exc:
                logger.warning(
                    "Warden discovery adapter %s failed: %s",
                    adapter.__class__.__name__,
                    exc,
                )
                continue
            for warden in wardens:
                by_id[warden.id] = warden
        return sorted(by_id.values(), key=lambda spec: (spec.name.lower(), spec.id))


def build_warden_discovery(
    config: Any | None = None,
    *,
    store: WardenStore | None = None,
) -> WardenDiscoveryPort:
    """Build the configured Warden discovery adapter chain."""
    if config is None:
        return WardenSpecDiscoveryAdapter(store=store)

    if not getattr(config, "enabled", True):
        return CompositeWardenDiscoveryAdapter([])

    adapters_config = list(getattr(config, "adapters", []) or [])
    if not adapters_config:
        return WardenSpecDiscoveryAdapter(store=store)

    adapters = [
        _build_adapter(adapter_config, store=store)
        for adapter_config in adapters_config
    ]
    return CompositeWardenDiscoveryAdapter([adapter for adapter in adapters if adapter is not None])


def _build_adapter(
    adapter_config: Any,
    *,
    store: WardenStore | None,
) -> WardenDiscoveryPort | None:
    adapter_path = str(_config_value(adapter_config, "adapter", "")).strip()
    if not adapter_path:
        return None

    kwargs = _adapter_kwargs(adapter_config)
    kwargs.update(_secret_kwargs(adapter_config))
    adapter_cls = _import_adapter(adapter_path)
    if store is not None and _accepts_kwarg(adapter_cls, "store"):
        kwargs.setdefault("store", store)
    return adapter_cls(**kwargs)


def _adapter_kwargs(adapter_config: Any) -> dict[str, Any]:
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


def _secret_kwargs(adapter_config: Any) -> dict[str, str]:
    secret_env = _config_value(adapter_config, "secret_kwargs_env", {}) or {}
    resolved: dict[str, str] = {}
    for key, env_name in dict(secret_env).items():
        value = os.environ.get(str(env_name), "").strip()
        if value:
            resolved[str(key)] = value
    return resolved


def _config_value(adapter_config: Any, key: str, default: Any) -> Any:
    if isinstance(adapter_config, dict):
        return adapter_config.get(key, default)
    return getattr(adapter_config, key, default)


def _import_adapter(adapter_path: str) -> type:
    module_name, _, class_name = adapter_path.rpartition(".")
    if not module_name or not class_name:
        msg = f"Invalid adapter path: {adapter_path}"
        raise ValueError(msg)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _accepts_kwarg(adapter_cls: type, name: str) -> bool:
    signature = inspect.signature(adapter_cls)
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name:
            return True
    return False
