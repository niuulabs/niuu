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
        ({"registry_refs": [None]}, "No configured default"),
    ],
)
def test_invalid_selected_well_never_uses_hosted_default(config, error):
    with pytest.raises(ValueError, match=error):
        resolve_mimir_workload(config, hosted_url="https://wrong-store.test/api/v1")


def test_configured_adapter_must_implement_the_port():
    with pytest.raises(TypeError, match="must implement MimirPort"):
        resolve_mimir_connection(adapter="builtins.dict", url="https://wrong-store.test")
