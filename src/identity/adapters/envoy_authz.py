"""Envoy v3 ext_authz adapter for a pod-local, trusted-metadata connection."""

from __future__ import annotations

import logging
import math
import time

from envoy.service.auth.v3 import external_auth_pb2, external_auth_pb2_grpc
from google.protobuf.json_format import MessageToDict
from google.rpc import code_pb2

from identity.authz_config import AuthorizationGatewayConfig
from identity.models import Resource
from identity.ports import AuthorizationEvaluationError, AuthorizationPort
from niuu.domain.models import Principal

logger = logging.getLogger(__name__)


def _claim(claims: dict, path: str) -> object:
    value = claims
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _deny(http_status: int) -> external_auth_pb2.CheckResponse:
    return external_auth_pb2.CheckResponse(
        status={"code": code_pb2.PERMISSION_DENIED},
        denied_response={"status": {"code": http_status}},
    )


class EnvoyAuthorizationService(external_auth_pb2_grpc.AuthorizationServicer):
    """Enforce admission using claims supplied by Envoy's JWT filter.

    The gRPC listener MUST remain pod-local: metadata is a trusted Envoy assertion,
    not a credential that arbitrary network clients may supply.
    """

    def __init__(self, authorization: AuthorizationPort, config: AuthorizationGatewayConfig):
        self._authorization = authorization
        self._config = config

    async def Check(self, request, context):  # noqa: N802 — Envoy's protobuf RPC name
        metadata = request.attributes.metadata_context.filter_metadata.get(
            "envoy.filters.http.jwt_authn"
        )
        if metadata is None:
            return _deny(401)
        claims = MessageToDict(metadata).get("jwt_payload")
        if not isinstance(claims, dict):
            return _deny(401)
        provider = next((p for p in self._config.providers if p.issuer == claims.get("iss")), None)
        if provider is None:
            return _deny(401)
        audience = claims.get("aud", [])
        if isinstance(audience, str):
            audience = [audience]
        if not isinstance(audience, list) or not any(a in audience for a in provider.audiences):
            return _deny(401)
        expiry = claims.get("exp")
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= time.time():
            return _deny(401)
        user_id = claims.get("sub")
        tenant = _claim(claims, provider.tenant_claim)
        roles = _claim(claims, provider.roles_claim)
        if not isinstance(user_id, str) or not user_id.strip():
            return _deny(401)
        if not isinstance(tenant, str) or not tenant.strip():
            return _deny(403)
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            return _deny(403)
        scopes = claims.get("scopes", [])
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            return _deny(403)

        http = request.attributes.request.http
        path = http.path.split("?", 1)[0]
        # Reject ambiguous path spellings rather than disagree with upstream routing.
        if not path.startswith("/") or any(c in path for c in ("%", "\\", "//")):
            return _deny(403)
        if any(part in (".", "..") for part in path.split("/")):
            return _deny(403)
        route = next(
            (
                r
                for r in self._config.routes
                if http.method in r.methods
                and (path == r.path or (r.prefix and path.startswith(r.path.rstrip("/") + "/")))
            ),
            None,
        )
        if route is None:
            return _deny(403)
        principal = Principal(
            user_id=user_id,
            email=claims.get("email", "") if isinstance(claims.get("email", ""), str) else "",
            tenant_id=tenant,
            roles=[self._config.role_mapping.get(r, r) for r in roles],
        )
        resource = Resource(
            kind="gateway",
            id=path,
            attr={
                "owner_id": "",
                "tenant_id": tenant,
                "method": http.method,
                "required_scope": route.required_scope,
                "scoped": claims.get("token_use") == "valkyrie_build",
                "scopes": scopes,
            },
        )
        try:
            allowed = await self._authorization.is_allowed(principal, "enter", resource)
        except AuthorizationEvaluationError:
            logger.error("Gateway authorization evaluation failed")
            return _deny(503)
        if not allowed:
            return _deny(403)
        return external_auth_pb2.CheckResponse(status={"code": code_pb2.OK}, ok_response={})
