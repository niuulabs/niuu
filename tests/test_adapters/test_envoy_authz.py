"""Exercise Envoy's actual gRPC protocol against the real Cedar engine."""

import time

import grpc
import pytest
from envoy.service.auth.v3 import external_auth_pb2, external_auth_pb2_grpc
from google.protobuf.json_format import ParseDict

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.adapters.envoy_authz import EnvoyAuthorizationService
from identity.authz_config import AuthorizationGatewayConfig
from identity.ports import AuthorizationEvaluationError


@pytest.fixture
def config():
    return AuthorizationGatewayConfig(
        providers=[{"issuer": "https://issuer.test", "audiences": ["forge"]}],
        routes=[
            {
                "path": "/api/v1/forge/sessions",
                "methods": ["POST"],
                "required_scope": "forge:session:create",
            },
            {"path": "/api/v1/forge", "prefix": True, "methods": ["GET", "POST", "DELETE"]},
        ],
    )


def request(path="/api/v1/forge/sessions", method="GET", **overrides):
    claims = {
        "iss": "https://issuer.test",
        "aud": "forge",
        "exp": time.time() + 60,
        "sub": "alice",
        "tenant_id": "acme",
        "resource_access": {"volundr": {"roles": ["volundr:developer"]}},
        **overrides,
    }
    return ParseDict(
        {
            "attributes": {
                "request": {
                    "http": {
                        "path": path,
                        "method": method,
                        "headers": {"x-auth-user-id": "forged-admin"},
                    }
                },
                "metadata_context": {
                    "filter_metadata": {
                        "envoy.filters.http.jwt_authn": {"jwt_payload": claims},
                    }
                },
            }
        },
        external_auth_pb2.CheckRequest(),
    )


@pytest.fixture
async def gateway(config):
    server = grpc.aio.server()
    external_auth_pb2_grpc.add_AuthorizationServicer_to_server(
        EnvoyAuthorizationService(CedarAuthorizationAdapter(), config),
        server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield external_auth_pb2_grpc.AuthorizationStub(channel)
    finally:
        await server.stop(0)


async def test_verified_identity_admitted_over_grpc(gateway):
    response = await gateway.Check(request(), timeout=2)
    assert response.status.code == 0
    assert response.HasField("ok_response")


@pytest.mark.parametrize(
    "claims,status",
    [
        ({"iss": "https://evil.test"}, 401),
        ({"aud": "different-service"}, 401),
        ({"exp": 1}, 401),
        ({"exp": "99999999999"}, 401),
        ({"exp": None}, 401),
        ({"sub": ""}, 401),
        ({"tenant_id": ""}, 403),
        ({"resource_access": {}}, 403),
        ({"scopes": "forge:session:create"}, 403),
    ],
)
async def test_invalid_metadata_denied(gateway, claims, status):
    response = await gateway.Check(request(**claims), timeout=2)
    assert response.denied_response.status.code == status


async def test_identity_headers_without_verified_metadata_are_rejected(gateway):
    req = request()
    req.attributes.ClearField("metadata_context")
    response = await gateway.Check(req, timeout=2)
    assert response.denied_response.status.code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/api/v1/forged",
        "/api/v1/forge/../admin",
        "/api/v1/forge/%2e%2e/admin",
        "/api/v1/forge//sessions",
        "/api/v1/forge\\sessions",
    ],
)
async def test_unknown_and_ambiguous_paths_denied(gateway, path):
    response = await gateway.Check(request(path=path), timeout=2)
    assert response.denied_response.status.code == 403


async def test_viewer_cannot_mutate_even_with_forged_admin_header(gateway):
    req = request(method="POST", resource_access={"volundr": {"roles": ["volundr:viewer"]}})
    req.attributes.request.http.headers["x-auth-roles"] = "volundr:admin"
    response = await gateway.Check(req, timeout=2)
    assert response.denied_response.status.code == 403


async def test_scoped_workload_cannot_use_owners_other_permissions(gateway):
    claims = {"token_use": "valkyrie_build", "scopes": ["forge:session:create"]}
    assert (await gateway.Check(request(method="POST", **claims), timeout=2)).status.code == 0
    for method, path in [
        ("GET", "/api/v1/forge/sessions"),
        ("DELETE", "/api/v1/forge/sessions/one"),
    ]:
        response = await gateway.Check(request(method=method, path=path, **claims), timeout=2)
        assert response.denied_response.status.code == 403
    response = await gateway.Check(request(method="POST", token_use="valkyrie_build"), timeout=2)
    assert response.denied_response.status.code == 403


async def test_engine_failure_denies_with_operational_status(config):
    from unittest.mock import AsyncMock

    authorization = AsyncMock()
    authorization.is_allowed.side_effect = AuthorizationEvaluationError("bad entities")
    service = EnvoyAuthorizationService(authorization, config)
    result = await service.Check(request(), None)
    assert result.denied_response.status.code == 503


def test_duplicate_issuer_configuration_is_rejected(config):
    with pytest.raises(ValueError, match="unique"):
        AuthorizationGatewayConfig.model_validate(
            {
                **config.model_dump(),
                "providers": [config.providers[0], config.providers[0]],
            }
        )


async def test_metadata_with_no_payload_is_not_authenticated(gateway):
    req = request()
    req.attributes.metadata_context.filter_metadata["envoy.filters.http.jwt_authn"].Clear()
    assert (await gateway.Check(req, timeout=2)).denied_response.status.code == 401


@pytest.mark.parametrize(
    "role,user,tenant,allowed",
    [
        ("developer", "alice", "acme", True),
        ("developer", "bob", "acme", False),
        ("admin", "bob", "acme", True),
        ("admin", "alice", "other", False),
        ("viewer", "alice", "acme", False),
    ],
)
async def test_session_bound_gateway_checks_deployment_owner(config, role, user, tenant, allowed):
    from identity.authz_config import SessionGatewayResource

    config.session = SessionGatewayResource(id="session-one", owner_id="alice", tenant_id="acme")
    service = EnvoyAuthorizationService(CedarAuthorizationAdapter(), config)
    response = await service.Check(
        request(
            sub=user, tenant_id=tenant, resource_access={"volundr": {"roles": [f"volundr:{role}"]}}
        ),
        None,
    )
    assert response.HasField("ok_response") is allowed


async def test_gateway_uses_current_authority_roles_and_fails_closed(config):
    from unittest.mock import AsyncMock

    from identity.models import Principal
    from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError

    identity = AsyncMock(spec=HeaderAuthenticationPort)
    identity.validate_headers.return_value = Principal("alice", "", "acme", ["volundr:viewer"])
    service = EnvoyAuthorizationService(CedarAuthorizationAdapter(), config, identity=identity)
    req = request(method="POST")
    req.attributes.request.http.headers["authorization"] = "Bearer verified"
    assert (await service.Check(req, None)).denied_response.status.code == 403
    identity.validate_headers.return_value = Principal("alice", "", "acme", ["volundr:developer"])
    assert (await service.Check(req, None)).status.code == 0
    identity.validate_headers.side_effect = InvalidTokenError("revoked")
    assert (await service.Check(req, None)).denied_response.status.code == 401
    identity.validate_headers.side_effect = AuthorizationEvaluationError("offline")
    assert (await service.Check(req, None)).denied_response.status.code == 503


async def test_gateway_scoped_pat_cannot_escape_named_route(gateway):
    scoped = {"type": "pat", "scope": "openid forge:session:create"}
    allowed = await gateway.Check(request(method="POST", **scoped), timeout=2)
    assert allowed.status.code == 0
    denied = await gateway.Check(
        request("/api/v1/forge/sessions/other", method="DELETE", **scoped), timeout=2
    )
    assert denied.denied_response.status.code == 403
