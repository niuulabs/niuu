"""ModelGatewayContributor: route catalog-local models through Bifrost."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from volundr.adapters.outbound.contributors.model_gateway import ModelGatewayContributor
from volundr.domain.models import ModelProvider
from volundr.domain.ports import SessionContext

URL = "http://niuu-bifrost-internal.volundr.svc.cluster.local/api/v1/bifrost"


class _Catalog:
    def list_models(self):
        return [
            SimpleNamespace(id="deepseek-v4-flash-0731", provider=ModelProvider.LOCAL),
            SimpleNamespace(id="claude-sonnet-5", provider=ModelProvider.CLOUD),
        ]


def _session(model: str):
    return SimpleNamespace(model=model)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("deepseek-v4-flash-0731", [{"name": "SKULD__MODEL_GATEWAY__URL", "value": URL}]),
        ("claude-sonnet-5", None),
        ("not-in-catalog", None),
        ("", None),
    ],
)
async def test_only_local_models_get_the_gateway(model, expected) -> None:
    contributor = ModelGatewayContributor(gateway_url=URL, pricing_provider=_Catalog())

    result = await contributor.contribute(_session(model), SessionContext())

    assert result.values.get("envVars") == expected


async def test_inert_without_a_gateway_url() -> None:
    contributor = ModelGatewayContributor(gateway_url="", pricing_provider=_Catalog())

    result = await contributor.contribute(_session("deepseek-v4-flash-0731"), SessionContext())

    assert result.values == {}


async def test_inert_without_a_catalog() -> None:
    contributor = ModelGatewayContributor(gateway_url=URL)

    result = await contributor.contribute(_session("deepseek-v4-flash-0731"), SessionContext())

    assert result.values == {}
