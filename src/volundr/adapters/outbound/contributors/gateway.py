"""Gateway contributor — wraps GatewayPort."""

from typing import Any
from urllib.parse import urlparse

from volundr.domain.models import Session
from volundr.domain.ports import (
    GatewayPort,
    SessionContext,
    SessionContribution,
    SessionContributor,
)


class GatewayContributor(SessionContributor):
    """Provides gateway configuration for HTTPRoute creation in Skuld."""

    def __init__(
        self,
        *,
        gateway: GatewayPort | None = None,
        **_extra: object,
    ):
        self._gateway = gateway

    @property
    def name(self) -> str:
        return "gateway"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if self._gateway is None:
            return SessionContribution()

        gateway_config = self._gateway.get_gateway_config()
        if not gateway_config:
            return SessionContribution()

        gw: dict[str, Any] = {
            "enabled": True,
            "name": gateway_config.get("gateway_name", "volundr-gateway"),
            "namespace": gateway_config.get(
                "gateway_namespace",
                "volundr-system",
            ),
            "userId": session.owner_id or "",
        }
        cors_origins_str = gateway_config.get("cors_origins", "*")
        gw["cors"] = {
            "allowOrigins": cors_origins_str.split(","),
            "allowMethods": ["GET", "POST", "OPTIONS"],
            "allowHeaders": ["Authorization", "Content-Type"],
            "allowCredentials": True,
        }
        issuer = gateway_config.get("issuer_url", "")
        if issuer:
            gw["jwt"] = {
                "enabled": True,
                "issuer": issuer,
                "audiences": [gateway_config.get("audience", "volundr")],
                "jwksUri": gateway_config.get("jwks_uri", ""),
            }
            if workload := _workload_jwt_config(gateway_config):
                gw["jwt"]["workload"] = {
                    "enabled": True,
                    "issuer": workload["issuer"],
                    "audiences": workload["audiences"],
                    "jwksUri": workload["jwksUri"],
                }

        values: dict[str, Any] = {"gateway": gw}
        if workload := _workload_jwt_config(gateway_config):
            values["envoy"] = {
                "jwt": {
                    "workload": workload,
                },
            }

        return SessionContribution(values=values)


def _workload_jwt_config(gateway_config: dict[str, str]) -> dict[str, Any]:
    issuer = gateway_config.get("workload_issuer_url", "")
    jwks_uri = gateway_config.get("workload_jwks_uri", "")
    if not issuer or not jwks_uri:
        return {}

    parsed = urlparse(jwks_uri)
    if not parsed.hostname:
        return {}

    scheme = parsed.scheme.lower()
    tls = scheme == "https"
    default_port = 443 if tls else 80
    return {
        "enabled": True,
        "issuer": issuer,
        "audiences": [gateway_config.get("workload_audience", "volundr-api")],
        "jwksUri": jwks_uri,
        "jwksHost": parsed.hostname,
        "jwksPort": parsed.port or default_port,
        "jwksTls": tls,
    }
