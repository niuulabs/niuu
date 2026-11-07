"""Only a runtime that separates slots may claim to serve several sessions at once."""

import importlib

import pytest

from tests.test_adapters.test_openshell_gateway import _import_adapter
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.domain.vm_runtime import VmRuntime

# Runtimes that keep one session per machine; SshContainerVmRuntime declares slots when
# slot_isolation is configured (tests/test_adapters/test_ssh_vm_runtime_slots.py).
SINGLE_SESSION_RUNTIMES = [
    "volundr.adapters.outbound.ssh_openshell_vm_runtime.SshOpenShellVmRuntime",
    "volundr.adapters.outbound.profiled_vm_runtime.ProfiledVmRuntime",
    "volundr.adapters.outbound.openshell_vm_runtime.OpenShellVmRuntime",
]


@pytest.mark.parametrize("path", SINGLE_SESSION_RUNTIMES)
def test_runtimes_with_their_own_sandbox_do_not_declare_slot_support(path, monkeypatch):
    _import_adapter(monkeypatch)
    module_path, name = path.rsplit(".", 1)
    runtime = getattr(importlib.import_module(module_path), name)

    assert issubclass(runtime, VmRuntime)
    if issubclass(runtime, SshContainerVmRuntime):
        # They inherit the property but refuse slot_isolation, so it stays False.
        assert runtime._separates_slots is False
    else:
        assert runtime.supports_slots is VmRuntime.supports_slots
    assert VmRuntime.supports_slots.fget(object()) is False


def test_the_contract_is_per_slot_state_keyed_by_allocation_and_slot():
    assert "(allocation, slot)" in VmRuntime.supports_slots.__doc__
    assert "network namespace" in VmRuntime.supports_slots.__doc__
