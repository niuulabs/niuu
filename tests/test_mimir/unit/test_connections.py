"""Workload connection selection is owned by Mímir, not its callers."""

import pytest

from mimir.connections import resolve_mimir_connection, resolve_mimir_workload


def test_explicit_hosted_connection_and_unconfigured_workload():
    assert resolve_mimir_workload() is None
    port = resolve_mimir_workload(hosted_url="https://knowledge.test/api/v1")
    assert port._base_url == "https://knowledge.test/api/v1"


@pytest.mark.parametrize(
    "config, error",
    [
        (
            {"registry_refs": [{"mount_name": "disabled", "enabled": False}]},
            "disabled",
        ),
        (
            {
                "default_mounts": ["missing"],
                "registry_refs": [{"mount_name": "other", "url": "https://other.test"}],
            },
            "No configured default",
        ),
        ({"registry_refs": [None]}, r"registry_refs\[0\] must be an object"),
    ],
)
def test_invalid_selected_well_never_uses_hosted_default(config, error):
    with pytest.raises(ValueError, match=error):
        resolve_mimir_workload(config, hosted_url="https://wrong-store.test/api/v1")


def test_configured_adapter_must_implement_the_port():
    with pytest.raises(TypeError, match="must implement MimirPort"):
        resolve_mimir_connection(adapter="builtins.dict", url="https://wrong-store.test")


def test_a_connection_with_nothing_to_connect_to_is_refused():
    with pytest.raises(ValueError, match="no adapter or url; configure its connection"):
        resolve_mimir_connection()


def test_registry_mount_carries_workload_auth_as_plain_kwargs():
    from mimir.domain import MimirAuth

    port = resolve_mimir_connection(
        adapter="mimir.adapters.http.HttpMimirAdapter",
        kwargs={
            "base_url": "https://regin.valhalla.test",
            "auth": {
                "type": "workload",
                "token_file": "/var/run/secrets/niuu-workload/token",
                "exchange_url": "https://platform.test/api/v1/tokens/workload/exchange",
                "audiences": ["regin-mimir"],
            },
        },
    )

    assert port._auth == MimirAuth(
        type="workload",
        token_file="/var/run/secrets/niuu-workload/token",
        exchange_url="https://platform.test/api/v1/tokens/workload/exchange",
        audiences=("regin-mimir",),
    )


def test_registry_mount_auth_with_an_unknown_field_is_refused():
    with pytest.raises(TypeError, match="unexpected keyword"):
        resolve_mimir_connection(
            adapter="mimir.adapters.http.HttpMimirAdapter",
            kwargs={"base_url": "https://regin.valhalla.test", "auth": {"tokn": "x"}},
        )


def _registry(tmp_path, *entries):
    from mimir.registry import MimirRegistryStore

    store = MimirRegistryStore(tmp_path / "registry.json")
    for entry in entries:
        store.save_entry(entry)
    return str(tmp_path / "registry.json")


def test_a_ref_with_no_connection_and_no_registry_entry_raises(tmp_path):
    from mimir.connections import resolve_mimir_registry_refs
    from mimir.registry import MimirRegistryEntry

    path = _registry(tmp_path, MimirRegistryEntry(id="known", name="known", url="https://k.test"))
    refs = {"registry_refs": [{"mount_name": "lost", "registry_entry_id": "gone"}]}

    with pytest.raises(ValueError, match="'gone' has no adapter or url.*registry.json"):
        resolve_mimir_registry_refs(refs, registry_path=path)
    with pytest.raises(ValueError, match="none is configured: set registry_path"):
        resolve_mimir_registry_refs(refs, registry_path="")


def test_a_ref_that_carries_its_own_connection_needs_no_registry_entry(tmp_path):
    from mimir.connections import resolve_mimir_registry_refs

    refs = {"registry_refs": [{"mount_name": "own", "url": "https://own.test"}]}
    assert resolve_mimir_registry_refs(refs, registry_path="") == refs


def test_a_resolved_ref_takes_the_entry_connection_and_facts_flag(tmp_path):
    from mimir.connections import resolve_mimir_registry_refs
    from mimir.registry import MimirRegistryEntry

    path = _registry(
        tmp_path, MimirRegistryEntry(id="e1", name="n", url="https://m.test", facts=True)
    )
    resolved = resolve_mimir_registry_refs(
        {"registry_refs": [{"mount_name": "n", "registry_entry_id": "e1"}]}, registry_path=path
    )
    ref = resolved["registry_refs"][0]
    assert ref["url"] == "https://m.test" and ref["facts"] is True
