"""Tests for GatewayContributor."""

from unittest.mock import MagicMock

import pytest

from volundr.adapters.outbound.contributors.gateway import GatewayContributor
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext


@pytest.fixture
def session():
    return Session(
        name="test",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id="user-1",
    )


class TestGatewayContributor:
    async def test_name(self):
        c = GatewayContributor()
        assert c.name == "gateway"

    async def test_no_gateway_returns_empty(self, session):
        c = GatewayContributor()
        result = await c.contribute(session, SessionContext())
        assert result.values == {}

    async def test_empty_config_returns_empty(self, session):
        gw = MagicMock()
        gw.get_gateway_config.return_value = {}
        c = GatewayContributor(gateway=gw)
        result = await c.contribute(session, SessionContext())
        assert result.values == {}

    async def test_gateway_values(self, session):
        gw = MagicMock()
        gw.get_gateway_config.return_value = {
            "gateway_name": "my-gw",
            "gateway_namespace": "system",
            "cors_origins": "https://app.example.com",
        }
        c = GatewayContributor(gateway=gw)
        result = await c.contribute(session, SessionContext())
        assert result.values["gateway"]["enabled"] is True
        assert result.values["gateway"]["name"] == "my-gw"
        assert result.values["gateway"]["namespace"] == "system"
        assert result.values["gateway"]["userId"] == "user-1"
        assert result.values["gateway"]["cors"]["allowOrigins"] == ["https://app.example.com"]

    async def test_gateway_with_jwt(self, session):
        gw = MagicMock()
        gw.get_gateway_config.return_value = {
            "gateway_name": "gw",
            "gateway_namespace": "system",
            "issuer_url": "https://idp.example.com",
            "audience": "volundr",
            "jwks_uri": "https://idp.example.com/.well-known/jwks",
            "workload_issuer_url": "https://yggdrasil.niuu.world/api/v1/tokens/workload",
            "workload_audience": "volundr-api",
            "workload_jwks_uri": "https://yggdrasil.niuu.world/api/v1/tokens/workload/jwks",
        }
        c = GatewayContributor(gateway=gw)
        result = await c.contribute(session, SessionContext())
        jwt = result.values["gateway"]["jwt"]
        assert jwt["enabled"] is True
        assert jwt["issuer"] == "https://idp.example.com"
        assert jwt["audiences"] == ["volundr"]
        gateway_workload = jwt["workload"]
        assert gateway_workload["enabled"] is True
        assert gateway_workload["issuer"] == "https://yggdrasil.niuu.world/api/v1/tokens/workload"
        assert gateway_workload["audiences"] == ["volundr-api"]
        assert gateway_workload["jwksUri"] == "https://yggdrasil.niuu.world/api/v1/tokens/workload/jwks"

        envoy_workload = result.values["envoy"]["jwt"]["workload"]
        assert envoy_workload["enabled"] is True
        assert envoy_workload["issuer"] == "https://yggdrasil.niuu.world/api/v1/tokens/workload"
        assert envoy_workload["audiences"] == ["volundr-api"]
        assert envoy_workload["jwksUri"] == "https://yggdrasil.niuu.world/api/v1/tokens/workload/jwks"
        assert envoy_workload["jwksHost"] == "yggdrasil.niuu.world"
        assert envoy_workload["jwksPort"] == 443
        assert envoy_workload["jwksTls"] is True
