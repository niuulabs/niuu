"""Configuration for the pod-local Envoy authorization service."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class AuthorizationGatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int = Field(default=9002, ge=1, le=65535)
    shutdown_grace_seconds: float = Field(default=5, ge=0)
    adapter: str = "identity.adapters.cedar.CedarAuthorizationAdapter"
    kwargs: dict = Field(default_factory=dict)
    providers: list[JWTMetadataProvider] = Field(min_length=1)
    routes: list[GatewayRoute] = Field(min_length=1)
    role_mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_issuers(self) -> "AuthorizationGatewayConfig":
        if len({p.issuer for p in self.providers}) != len(self.providers):
            raise ValueError("Authorization gateway JWT issuers must be unique")
        return self
