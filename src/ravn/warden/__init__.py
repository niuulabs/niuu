"""Warden models and persistence helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ravn.warden.models import (
    WardenConsoleConfig,
    WardenDreamSummary,
    WardenFeatures,
    WardenMimirBinding,
    WardenOperator,
    WardenRuntime,
    WardenScheduleConfig,
    WardenSpec,
    WardenSupervisor,
)

if TYPE_CHECKING:
    from ravn.warden.store import WardenStore


def resolve_deployment_adapter(deployment: str) -> str:
    from ravn.warden.deployment import resolve_deployment_adapter as _resolve_deployment_adapter

    return _resolve_deployment_adapter(deployment)


def create_warden_deployer(spec: WardenSpec):
    from ravn.warden.deployment import create_warden_deployer as _create_warden_deployer

    return _create_warden_deployer(spec)


def build_warden_store(root=None):
    from ravn.warden.deployment import build_warden_store as _build_warden_store

    return _build_warden_store(root=root)


def __getattr__(name: str):
    if name == "WardenStore":
        from ravn.warden.store import WardenStore

        return WardenStore
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


__all__ = [
    "WardenDreamSummary",
    "WardenConsoleConfig",
    "WardenFeatures",
    "WardenMimirBinding",
    "WardenOperator",
    "WardenRuntime",
    "WardenScheduleConfig",
    "WardenSpec",
    "WardenStore",
    "WardenSupervisor",
    "build_warden_store",
    "create_warden_deployer",
    "resolve_deployment_adapter",
]
