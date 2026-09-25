"""ModelGatewayContributor: route catalog-local models through Bifrost."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from volundr.adapters.outbound.contributors.model_gateway import (
    MODEL_GATEWAY_TOKEN_ENV,
    MODEL_GATEWAY_URL_ENV,
    OPEN_GATEWAY_TOKEN,
    ModelGatewayContributor,
)
from volundr.domain.models import ModelProvider
from volundr.domain.ports import SessionContext

URL = "http://niuu-bifrost-internal.volundr.svc.cluster.local/api/v1/bifrost"
_URL_AND_OPEN_TOKEN = [
    {"name": MODEL_GATEWAY_URL_ENV, "value": URL},
    {"name": MODEL_GATEWAY_TOKEN_ENV, "value": OPEN_GATEWAY_TOKEN},
]


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
        ("deepseek-v4-flash-0731", _URL_AND_OPEN_TOKEN),
        ("claude-sonnet-5", None),
        ("not-in-catalog", None),
        ("", None),
    ],
)
async def test_only_local_models_get_the_gateway(model, expected) -> None:
    contributor = ModelGatewayContributor(gateway_url=URL, pricing_provider=_Catalog())

    result = await contributor.contribute(_session(model), SessionContext())

    assert result.values.get("envVars") == expected


async def test_default_auth_mode_gets_a_non_blank_token() -> None:
    """The Claude/Codex transports raise on a blank token when the gateway

    URL is set (see skuld/transports/claude_env.py, codex_ws.py) — this
    contributor must never leave a local-model session with none.
    """
    contributor = ModelGatewayContributor(gateway_url=URL, pricing_provider=_Catalog())

    result = await contributor.contribute(_session("deepseek-v4-flash-0731"), SessionContext())

    env = {e["name"]: e["value"] for e in result.values["envVars"]}
    assert env[MODEL_GATEWAY_TOKEN_ENV]
    assert env[MODEL_GATEWAY_TOKEN_ENV] == OPEN_GATEWAY_TOKEN


async def test_none_mode_gets_the_open_gateway_token() -> None:
    contributor = ModelGatewayContributor(
        gateway_url=URL, pricing_provider=_Catalog(), auth_mode="none"
    )

    result = await contributor.contribute(_session("deepseek-v4-flash-0731"), SessionContext())

    assert result.values["envVars"] == _URL_AND_OPEN_TOKEN


async def test_oidc_mode_sets_no_token() -> None:
    """No real per-session credential exists yet for oidc — never send a

    meaningless one; the transports fail loudly instead (see
    OPEN_GATEWAY_TOKEN's docstring). oidc hosts cannot reach this at all
    today anyway (the bifrost plugin is refused at startup there — see
    cli.config.CLISettings._OIDC_UNCOVERED_PLUGINS['bifrost']).
    """
    contributor = ModelGatewayContributor(
        gateway_url=URL, pricing_provider=_Catalog(), auth_mode="oidc"
    )

    result = await contributor.contribute(_session("deepseek-v4-flash-0731"), SessionContext())

    names = {e["name"] for e in result.values["envVars"]}
    assert names == {MODEL_GATEWAY_URL_ENV}


async def test_default_none_mode_path_end_to_end_contributor_to_transport() -> None:
    """The full chain for the common local-model case: the contributor's

    output env vars, read the way skuld.transport_lifecycle actually reads
    them (SKULD__MODEL_GATEWAY__URL/TOKEN via SkuldSettings), reach
    claude_spawn_env without it refusing a blank token.
    """
    from skuld.config import SkuldSettings
    from skuld.transports.claude_env import claude_spawn_env

    contributor = ModelGatewayContributor(
        gateway_url=URL, pricing_provider=_Catalog(), auth_mode="none"
    )
    contribution = await contributor.contribute(
        _session("deepseek-v4-flash-0731"), SessionContext()
    )
    env_vars = {e["name"]: e["value"] for e in contribution.values["envVars"]}

    with patch.dict("os.environ", env_vars, clear=True):
        settings = SkuldSettings()
        assert settings.model_gateway.url == URL
        assert settings.model_gateway.token == OPEN_GATEWAY_TOKEN

        spawn_env = claude_spawn_env(
            gateway_url=settings.model_gateway.url,
            gateway_token=settings.model_gateway.token,
        )  # must not raise
    assert spawn_env["ANTHROPIC_AUTH_TOKEN"] == OPEN_GATEWAY_TOKEN


async def test_inert_without_a_gateway_url() -> None:
    contributor = ModelGatewayContributor(gateway_url="", pricing_provider=_Catalog())

    result = await contributor.contribute(_session("deepseek-v4-flash-0731"), SessionContext())

    assert result.values == {}


async def test_inert_without_a_catalog() -> None:
    contributor = ModelGatewayContributor(gateway_url=URL)

    result = await contributor.contribute(_session("deepseek-v4-flash-0731"), SessionContext())

    assert result.values == {}
