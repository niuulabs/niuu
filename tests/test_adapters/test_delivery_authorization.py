"""Tests for execution-owned delivery authorization projection."""

from __future__ import annotations

import httpx
import pytest

from niuu.domain.models import Principal
from volundr.adapters.outbound.delivery_authorization import HttpDeliveryAuthorizer

REPOSITORY = "https://gitlab.example/org/repo"


def _principal() -> Principal:
    return Principal(
        user_id="owner-1",
        email="owner@example.invalid",
        tenant_id="tenant-1",
        roles=["volundr:developer"],
    )


@pytest.mark.asyncio
async def test_forwards_bearer_to_ting_campaign_projection() -> None:
    seen: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://ting.example"
    )
    authorizer = HttpDeliveryAuthorizer(base_url="https://ting.example", client=client)
    await authorizer.authorize(
        _principal(),
        "campaign-1",
        "conditional_merge",
        repository=REPOSITORY,
        base_sha="a" * 40,
        candidate_sha="b" * 40,
        candidate_tree="c" * 40,
        target_branch="main",
        policy_id="developer-integration",
        credential="Bearer signed-workload-token",
    )
    assert seen[0].headers["authorization"] == "Bearer signed-workload-token"
    assert seen[0].url.path == (
        "/api/v1/ting/delivery-executions/campaign-1/delivery-authorizations"
    )
    assert seen[0].content == (
        b'{"operation":"conditional_merge","repository":"https://gitlab.example/org/repo",'
        b'"base_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"candidate_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        b'"candidate_tree":"cccccccccccccccccccccccccccccccccccccccc",'
        b'"target_branch":"main","policy_id":"developer-integration"}'
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_campaign_operation_requires_workload_credential() -> None:
    authorizer = HttpDeliveryAuthorizer(base_url="https://ting.example")
    with pytest.raises(PermissionError, match="workload credential"):
        await authorizer.authorize(_principal(), "campaign-1", "open_review", repository=REPOSITORY)


@pytest.mark.asyncio
async def test_ting_denial_fails_closed() -> None:
    async def deny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = httpx.AsyncClient(transport=httpx.MockTransport(deny))
    authorizer = HttpDeliveryAuthorizer(base_url="https://ting.example", client=client)
    with pytest.raises(PermissionError, match="denied"):
        await authorizer.authorize(
            _principal(),
            "campaign-1",
            "integrate_candidate",
            repository=REPOSITORY,
            credential="Bearer signed-workload-token",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_resolve_ref_is_limited_to_authenticated_principal() -> None:
    authorizer = HttpDeliveryAuthorizer(base_url="https://ting.example")
    await authorizer.authorize(_principal(), None, "resolve_ref", repository=REPOSITORY)
    with pytest.raises(PermissionError, match="authenticated"):
        await authorizer.authorize(
            Principal(user_id="", email="", tenant_id="", roles=[]),
            None,
            "resolve_ref",
            repository=REPOSITORY,
        )
    with pytest.raises(PermissionError, match="Campaign identity"):
        await authorizer.authorize(_principal(), None, "open_review", repository=REPOSITORY)


@pytest.mark.asyncio
async def test_explicit_dev_mode_projects_authenticated_headers() -> None:
    seen: list[httpx.Request] = []

    async def allow(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(allow))
    authorizer = HttpDeliveryAuthorizer(
        base_url="https://ting.example",
        allow_anonymous_dev=True,
        client=client,
    )
    await authorizer.authorize(_principal(), "campaign-1", "open_review", repository=REPOSITORY)
    assert seen[0].headers["x-auth-user-id"] == "owner-1"
    assert seen[0].headers["x-auth-tenant"] == "tenant-1"
    await client.aclose()


@pytest.mark.asyncio
async def test_projection_outage_and_server_error_fail_closed() -> None:
    async def unavailable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    failing_client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    authorizer = HttpDeliveryAuthorizer(base_url="https://ting.example", client=failing_client)
    with pytest.raises(RuntimeError, match="unavailable"):
        await authorizer.authorize(
            _principal(),
            "campaign-1",
            "open_review",
            repository=REPOSITORY,
            credential="Bearer signed",
        )
    await failing_client.aclose()

    async def server_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    error_client = httpx.AsyncClient(transport=httpx.MockTransport(server_error))
    authorizer = HttpDeliveryAuthorizer(base_url="https://ting.example", client=error_client)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await authorizer.authorize(
            _principal(),
            "campaign-1",
            "open_review",
            repository=REPOSITORY,
            credential="Bearer signed",
        )
    await error_client.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [{"base_url": ""}, {"base_url": "https://ting.example", "timeout_seconds": 0}],
)
def test_rejects_invalid_authorizer_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        HttpDeliveryAuthorizer(**kwargs)
