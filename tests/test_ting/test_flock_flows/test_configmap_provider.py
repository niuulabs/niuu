"""Tests for KubernetesConfigMapFlockFlowProvider — mocked k8s client.

Contract-test parity with ConfigFlockFlowProvider via shared contract suite,
plus k8s-specific edge-case tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from tests.test_ting.test_flock_flows.contract import (
    FlockFlowProviderContract,
    make_flow,
)
from ting.adapters.flows.configmap import KubernetesConfigMapFlockFlowProvider
from ting.domain.flock_flow import FlockFlowConfig


def _make_configmap(flows: list[dict] | None = None) -> SimpleNamespace:
    """Build a mock ConfigMap response."""
    data = {}
    if flows is not None:
        data["flows.yaml"] = yaml.dump(flows, default_flow_style=False)
    return SimpleNamespace(data=data)


def _make_stateful_client(initial_flows: list[dict] | None = None) -> MagicMock:
    """Build a mock k8s client that reflects writes back to reads."""
    client = MagicMock()
    cm = _make_configmap(initial_flows or [])

    def _patch(*, name: str, namespace: str, body: dict) -> None:
        cm.data = body["data"]

    client.read_namespaced_config_map.return_value = cm
    client.patch_namespaced_config_map.side_effect = _patch
    return client


def _make_provider(
    flows: list[dict] | None = None,
) -> tuple[KubernetesConfigMapFlockFlowProvider, MagicMock]:
    """Build a provider with a mocked k8s client pre-loaded with flows."""
    client = _make_stateful_client(flows)
    provider = KubernetesConfigMapFlockFlowProvider(
        namespace="ting",
        configmap_name="flock-flows",
        kube_client=client,
    )
    return provider, client


class TestKubernetesConfigMapFlockFlowProviderContract(FlockFlowProviderContract):
    """Run the shared contract suite against the ConfigMap provider."""

    @pytest.fixture()
    def provider(self) -> KubernetesConfigMapFlockFlowProvider:
        client = _make_stateful_client([])
        return KubernetesConfigMapFlockFlowProvider(
            namespace="ting",
            configmap_name="flock-flows",
            kube_client=client,
        )


class TestKubernetesConfigMapFlockFlowProviderSpecific:
    """K8s-specific tests beyond the shared contract."""

    def test_save_calls_patch(self) -> None:
        provider, client = _make_provider(flows=[])
        provider.save(make_flow())
        client.patch_namespaced_config_map.assert_called_once()
        call_kwargs = client.patch_namespaced_config_map.call_args
        assert call_kwargs.kwargs["name"] == "flock-flows"

    def test_save_overwrites_existing(self) -> None:
        flow_data = [make_flow("test-flow").to_dict()]
        provider, client = _make_provider(flows=flow_data)

        updated = FlockFlowConfig(name="test-flow", description="Updated")
        provider.save(updated)

        call_body = client.patch_namespaced_config_map.call_args.kwargs["body"]
        saved_data = yaml.safe_load(call_body["data"]["flows.yaml"])
        assert len(saved_data) == 1
        assert saved_data[0]["description"] == "Updated"

    def test_save_appends_new(self) -> None:
        flow_data = [make_flow("existing").to_dict()]
        provider, client = _make_provider(flows=flow_data)

        provider.save(make_flow("new-flow"))

        call_body = client.patch_namespaced_config_map.call_args.kwargs["body"]
        saved_data = yaml.safe_load(call_body["data"]["flows.yaml"])
        assert len(saved_data) == 2

    def test_delete_calls_patch(self) -> None:
        flow_data = [make_flow("to-delete").to_dict()]
        provider, client = _make_provider(flows=flow_data)

        deleted = provider.delete("to-delete")
        assert deleted is True
        client.patch_namespaced_config_map.assert_called_once()
        call_body = client.patch_namespaced_config_map.call_args.kwargs["body"]
        saved_data = yaml.safe_load(call_body["data"]["flows.yaml"])
        assert saved_data == []

    def test_no_client_raises_on_save(self) -> None:
        provider = KubernetesConfigMapFlockFlowProvider(kube_client=None)
        with pytest.raises(RuntimeError, match="No k8s client"):
            provider.save(make_flow())

    def test_no_client_returns_empty(self) -> None:
        provider = KubernetesConfigMapFlockFlowProvider(kube_client=None)
        assert provider.list() == []
        assert provider.get("anything") is None

    def test_no_client_delete_returns_false(self) -> None:
        provider = KubernetesConfigMapFlockFlowProvider(kube_client=None)
        deleted = provider.delete("anything")
        assert deleted is False

    def test_write_failure_propagates(self) -> None:
        provider, client = _make_provider(flows=[])
        client.patch_namespaced_config_map.side_effect = RuntimeError("k8s write error")
        with pytest.raises(RuntimeError, match="k8s write error"):
            provider.save(make_flow())

    def test_read_error_returns_empty(self) -> None:
        client = MagicMock()
        client.read_namespaced_config_map.side_effect = RuntimeError("k8s error")
        provider = KubernetesConfigMapFlockFlowProvider(kube_client=client)
        assert provider.list() == []

    def test_configmap_with_no_data_key(self) -> None:
        client = MagicMock()
        client.read_namespaced_config_map.return_value = SimpleNamespace(data={})
        provider = KubernetesConfigMapFlockFlowProvider(kube_client=client)
        assert provider.list() == []
