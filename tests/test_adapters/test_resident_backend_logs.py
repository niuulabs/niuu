"""Every resident runtime backend Völundr ships reads its residents' logs.

A profile advertises ``logs`` per backend, and the resident Logs action reaches
whichever backend owns the resident. A backend controller without
``ResidentRuntimeLogReader`` would make that action fail on one cluster only, so
this test discovers every controller in ``volundr.adapters.outbound`` rather
than listing them: a new backend that skips the port fails here.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys

import pytest

import volundr.adapters.outbound as outbound
from tests.test_adapters.test_openshell_gateway import _import_adapter as _import_openshell
from volundr.domain.models import ResidentBackend
from volundr.domain.ports import ResidentRuntimeController, ResidentRuntimeLogReader


def _subclasses(cls: type) -> set[type]:
    found: set[type] = set()
    for subclass in cls.__subclasses__():
        found.add(subclass)
        found |= _subclasses(subclass)
    return found


@pytest.fixture
def shipped_controllers(monkeypatch: pytest.MonkeyPatch) -> list[type]:
    """Import every outbound adapter module; the OpenShell SDK is stubbed as its tests do."""
    before = set(sys.modules)
    _import_openshell(monkeypatch)
    for module in pkgutil.walk_packages(outbound.__path__, f"{outbound.__name__}."):
        importlib.import_module(module.name)
    controllers = [
        cls
        for cls in _subclasses(ResidentRuntimeController)
        if cls.__module__.startswith(f"{outbound.__name__}.") and not inspect.isabstract(cls)
    ]
    yield controllers
    # Modules imported against the stubbed SDK must not leak into later tests.
    for name in set(sys.modules) - before:
        if name.startswith(f"{outbound.__name__}.openshell"):
            sys.modules.pop(name, None)


def test_every_resident_backend_controller_reads_logs(shipped_controllers: list[type]) -> None:
    missing = sorted(
        f"{cls.__module__}.{cls.__name__}"
        for cls in shipped_controllers
        if not issubclass(cls, ResidentRuntimeLogReader)
    )

    assert missing == [], (
        "Resident backend controllers without ResidentRuntimeLogReader; a profile on "
        f"their backend cannot serve the Logs action: {missing}"
    )


def test_discovery_finds_a_controller_for_every_resident_backend(
    shipped_controllers: list[type],
) -> None:
    """Guards the test above against passing because it found nothing."""
    names = {cls.__name__ for cls in shipped_controllers}
    assert {
        "FluxPodManager",
        "OpenShellGatewayPodManager",
        "HostProcessResidentRuntimeController",
        "LocalContainerResidentRuntimeController",
    } <= names
    # Each controller's backend property is a constant; read it without an instance.
    backends = {cls.backend.fget(None) for cls in shipped_controllers}
    assert set(ResidentBackend) <= backends
