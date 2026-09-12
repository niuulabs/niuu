"""Configuration for the pod-local Envoy authorization service."""

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AuthorizationAdapterConfig(BaseModel):
    """Dynamic resource authorization configuration."""

    adapter: str = "identity.adapters.cedar.CedarAuthorizationAdapter"
    kwargs: dict = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class JWTMetadataProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str = Field(min_length=1)
    audiences: list[str] = Field(min_length=1)
    tenant_claim: str = "tenant_id"
    roles_claim: str = "resource_access.volundr.roles"


class GatewayRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^/[^?#%\\]*$")
    prefix: bool = False
    methods: list[str] = Field(min_length=1)
    required_scope: str = ""
    path_template: bool = False

    @model_validator(mode="after")
    def valid_template(self) -> "GatewayRoute":
        if not self.path_template:
            return self
        if self.prefix:
            raise ValueError("Template routes cannot also match prefixes")
        for part in self.path.split("/"):
            if ("{" in part or "}" in part) and not re.fullmatch(
                r"\{[a-zA-Z_][a-zA-Z_0-9]*\}", part
            ):
                raise ValueError("Route parameters must occupy a complete path segment")
        return self

    def matches(self, path: str) -> bool:
        if not self.path_template:
            return path == self.path or (
                self.prefix and path.startswith(self.path.rstrip("/") + "/")
            )
        expected, actual = self.path.split("/"), path.split("/")
        return len(expected) == len(actual) and all(
            a == e or (e.startswith("{") and e.endswith("}") and bool(a))
            for e, a in zip(expected, actual, strict=True)
        )


class SessionGatewayResource(BaseModel):
    """Authoritative session binding supplied by the pod deployment."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)


class AuthorizationGatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int = Field(default=9002, ge=1, le=65535)
    shutdown_grace_seconds: float = Field(default=5, ge=0)
    adapter: str = "identity.adapters.cedar.CedarAuthorizationAdapter"
    kwargs: dict = Field(default_factory=dict)
    providers: list[JWTMetadataProvider] = Field(min_length=1)
    routes: list[GatewayRoute] = Field(min_length=1)
    role_mapping: dict[str, str] = Field(default_factory=dict)
    session: SessionGatewayResource | None = None

    @model_validator(mode="after")
    def unique_issuers(self) -> "AuthorizationGatewayConfig":
        if len({p.issuer for p in self.providers}) != len(self.providers):
            raise ValueError("Authorization gateway JWT issuers must be unique")
        return self
