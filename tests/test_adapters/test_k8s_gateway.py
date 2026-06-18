"""Tests for gateway adapters (config providers)."""

from __future__ import annotations

from volundr.adapters.outbound.k8s_gateway import (
    InMemoryGatewayAdapter,
    K8sGatewayAdapter,
)


class TestInMemoryGatewayAdapter:
    """Tests for InMemoryGatewayAdapter."""

    def test_default_config(self):
        adapter = InMemoryGatewayAdapter()
        config = adapter.get_gateway_config()
        assert config == {
            "gateway_name": "volundr-gateway",
            "gateway_namespace": "volundr-system",
        }

    def test_custom_config(self):
        adapter = InMemoryGatewayAdapter(
            gateway_name="my-gw",
            gateway_namespace="my-ns",
        )
        config = adapter.get_gateway_config()
        assert config["gateway_name"] == "my-gw"
        assert config["gateway_namespace"] == "my-ns"

    def test_ignores_extra_kwargs(self):
        adapter = InMemoryGatewayAdapter(unknown_param="ignored")
        config = adapter.get_gateway_config()
        assert "gateway_name" in config


class TestK8sGatewayAdapter:
    """Tests for K8sGatewayAdapter."""

    def test_minimal_config(self):
        adapter = K8sGatewayAdapter()
        config = adapter.get_gateway_config()
        assert config["gateway_name"] == "volundr-gateway"
        assert config["gateway_namespace"] == "volundr-system"

    def test_full_config(self):
        adapter = K8sGatewayAdapter(
            namespace="volundr",
            gateway_name="sessions-gw",
            gateway_namespace="volundr",
            gateway_domain="sessions.example.com",
            issuer_url="https://idp.example.com",
            audience="volundr",
            jwks_uri="https://idp.example.com/.well-known/jwks",
            workload_issuer_url="https://yggdrasil.niuu.world/api/v1/tokens/workload",
            workload_audience="volundr-api",
            workload_jwks_uri="https://yggdrasil.niuu.world/api/v1/tokens/workload/jwks",
        )
        config = adapter.get_gateway_config()
        assert config["gateway_name"] == "sessions-gw"
        assert config["gateway_namespace"] == "volundr"
        assert config["gateway_domain"] == "sessions.example.com"
        assert config["issuer_url"] == "https://idp.example.com"
        assert config["audience"] == "volundr"
        assert config["jwks_uri"] == "https://idp.example.com/.well-known/jwks"
        assert config["workload_issuer_url"] == "https://yggdrasil.niuu.world/api/v1/tokens/workload"
        assert config["workload_audience"] == "volundr-api"
        assert (
            config["workload_jwks_uri"]
            == "https://yggdrasil.niuu.world/api/v1/tokens/workload/jwks"
        )

    def test_optional_fields_omitted_when_empty(self):
        adapter = K8sGatewayAdapter(
            gateway_domain="",
            issuer_url="",
            audience="",
            jwks_uri="",
            workload_issuer_url="",
            workload_audience="",
            workload_jwks_uri="",
        )
        config = adapter.get_gateway_config()
        assert "gateway_domain" not in config
        assert "issuer_url" not in config
        assert "audience" not in config
        assert "jwks_uri" not in config
        assert "workload_issuer_url" not in config
        assert "workload_audience" not in config
        assert "workload_jwks_uri" not in config

    def test_ignores_extra_kwargs(self):
        adapter = K8sGatewayAdapter(some_future_param="value")
        config = adapter.get_gateway_config()
        assert "gateway_name" in config
