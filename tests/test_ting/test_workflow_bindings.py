"""Imported resource bindings resolve only to enabled local tenant entries."""

from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from ting.api.workflow_bindings import binding_errors


def test_binding_checks_require_configured_registry():
    assert not binding_errors({}, registry_path="", tenant_id="a")
    assert binding_errors({"memory": "local"}, registry_path="", tenant_id="a")


def test_binding_checks_filter_tenants_disabled_and_missing_entries(tmp_path):
    path = tmp_path / "registry.json"
    store = MimirRegistryStore(path)
    for id_, tenant, enabled in [
        ("mine", "a", True),
        ("other", "b", True),
        ("shared", "", True),
        ("disabled", "a", False),
    ]:
        store.save_entry(MimirRegistryEntry(id=id_, name=id_, tenant_id=tenant, enabled=enabled))
    assert not binding_errors({"memory": "mine"}, registry_path=str(path), tenant_id="a")
    assert not binding_errors({"memory": "shared"}, registry_path=str(path), tenant_id="a")
    for entry in ("other", "disabled", "missing"):
        assert binding_errors({"memory": entry}, registry_path=str(path), tenant_id="a")
