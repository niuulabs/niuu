"""CLI configuration — pydantic-settings with ~/.niuu/config.yaml."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from bifrost.config import BifrostConfig
from niuu.domain.observability import ObservabilityConfig
from volundr.compute.config import ComputeConfig

DEFAULT_CONFIG_DIR = Path.home() / ".niuu"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"


def config_paths() -> list[Path]:
    """Resolve config file paths. NIUU_CONFIG env var takes precedence."""
    import os

    env = os.environ.get("NIUU_CONFIG")
    if env:
        return [Path(env)]
    return [
        DEFAULT_CONFIG_FILE,
        Path("/etc/niuu/config.yaml"),
    ]


class PerServiceConfig(BaseModel):
    """Per-service enabled/port overrides."""

    enabled: bool | None = Field(
        default=None,
        description="Override whether this service is enabled. None = use plugin default.",
    )
    port: int | None = Field(
        default=None,
        description="Override the listen port. None = use plugin default.",
    )


class PluginConfig(BaseModel):
    """Per-plugin enable/disable configuration."""

    enabled: dict[str, bool] = Field(
        default_factory=dict,
        description="Map of plugin name to enabled status.",
    )
    extra: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Extra plugins loaded via dynamic adapter pattern.",
    )


class DatabaseConfig(BaseModel):
    """Database configuration for mini mode."""

    mode: str = Field(
        default="embedded",
        description="Database mode: 'embedded' (bundled PostgreSQL) or 'external'.",
    )
    dsn: str = Field(
        default="",
        description="Database DSN for external mode.",
    )


class PodManagerConfig(BaseModel):
    """Pod manager configuration — dynamic adapter pattern.

    The ``adapter`` key specifies the fully-qualified class path.
    All remaining keys are forwarded as ``**kwargs`` to the adapter constructor.
    Mini mode defaults are kept for backwards compatibility; cluster mode
    overrides them via the YAML config file.
    """

    model_config = {"extra": "allow"}

    adapter: str = Field(
        default="volundr.adapters.outbound.local_process.LocalProcessPodManager",
        description="Fully-qualified class path for the pod manager adapter.",
    )
    runtime_backend: str | None = Field(
        default=None,
        description=(
            "Contributor backend identity for adapters that wrap another runtime, "
            "such as an OpenShell-backed VM pool."
        ),
    )
    # Mini-mode defaults (ignored by DirectK8sPodManager via **_extra)
    workspaces_dir: str = Field(
        default="~/.niuu/workspaces",
        description="Directory for session workspaces (mini mode).",
    )
    claude_binary: str = Field(
        default="claude",
        description="Path or name of the claude binary (mini mode).",
    )
    max_concurrent: int = Field(
        default=4,
        description="Maximum concurrent sessions.",
    )
    sdk_port_start: int = Field(
        default=9100,
        description="Starting port for Skuld/SDK WebSocket allocation.",
    )

    def adapter_kwargs(self) -> dict[str, Any]:
        """Return kwargs to pass to the adapter constructor.

        Excludes ``adapter`` (the class path) and returns everything else,
        including extra fields from the YAML config.
        """
        data = self.model_dump()
        data.pop("adapter", None)
        data.pop("runtime_backend", None)
        return data


class DockerVllmConfig(BaseModel):
    """Optional local model served by vLLM inside the compose bundle."""

    enabled: bool = Field(
        default=False,
        description="Start a vLLM container serving `model` on the host GPU.",
    )
    model: str = Field(
        default="",
        description="Hugging Face model id to serve (e.g. nvidia/Nemotron-3-Nano-30B-A3B).",
    )
    image: str = Field(
        default="",
        description=(
            "vLLM container image (must match the host architecture). The installer "
            "writes it into ~/.niuu/config.yaml; required when `enabled` is true."
        ),
    )
    port: int = Field(default=8000, description="Port vLLM listens on inside the compose network.")
    max_model_len: int = Field(default=65536, description="Context length passed to vLLM.")
    gpu_memory_utilization: float = Field(
        default=0.6,
        description="Fraction of GPU memory vLLM may reserve; leave room for sandboxes.",
    )
    hf_token: str = Field(
        default="",
        description="Hugging Face token for gated repositories (empty = anonymous).",
    )
    trust_remote_code: bool = Field(
        default=False,
        description=(
            "Pass --trust-remote-code to vLLM for a custom model whose repository ships "
            "model code. Curated models that need it are handled without this flag."
        ),
    )


class DockerModelConfig(BaseModel):
    """One model the setup wizard offers to serve locally with vLLM.

    The installer writes the initial list into ``~/.niuu/config.yaml``; editing
    that file and running ``niuu up`` again changes what the wizard offers and
    how vLLM is started, without a new platform image.
    """

    id: str = Field(description="Short id the wizard uses (e.g. nemotron-3-nano-30b).")
    model: str = Field(description="Hugging Face model id vLLM serves.")
    name: str = Field(description="Name shown in the wizard.")
    description: str = Field(default="", description="One line shown under the name.")
    weight_gib: int = Field(
        description="Memory vLLM reserves for the weights plus a 64k-token KV cache, rounded up."
    )
    recommended: bool = Field(default=False, description="Preselected in the wizard.")
    trust_remote_code: bool = Field(
        default=False,
        description="The repository ships model code vLLM must run (--trust-remote-code).",
    )
    serve_args: list[str] = Field(
        default_factory=list,
        description="Extra `vllm serve` arguments from the model card (tool-call parser, ...).",
    )


class DockerModelServerConfig(BaseModel):
    """A model server you already run (vLLM, sparkrun, Ollama, ...).

    Registered as the ``local`` provider of the platform's model gateway
    (Bifrost) and seeded as the "Model server" AI provider, so Claude Code,
    Codex and Ravn sessions can use its models.
    """

    enabled: bool = Field(
        default=False,
        description="Route the `local` gateway provider and a session provider at this server.",
    )
    base_url: str = Field(
        default="",
        description=(
            "OpenAI-compatible base URL without the /v1 suffix, as reachable from the "
            "platform container (e.g. http://host.docker.internal:8000 for a server on "
            "this host)."
        ),
    )
    models: list[str] = Field(
        default_factory=list,
        description="Model ids the server serves; the first is what sessions pick by default.",
    )
    api_key: str = Field(
        default="",
        description="Bearer token the server expects, if it needs one.",
    )

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        return url

    @model_validator(mode="after")
    def _enabled_needs_a_server(self) -> DockerModelServerConfig:
        if not self.enabled:
            return self
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(
                "docker.model_server.base_url must be an http(s) URL when the model server "
                "is enabled"
            )
        if not [m for m in self.models if m.strip()]:
            raise ValueError(
                "docker.model_server.models must name at least one model when the model "
                "server is enabled"
            )
        return self


class DockerExternalIntegrationConfig(BaseModel):
    """A machine-local external package mounted into the Niuu container."""

    source_dir: str = Field(
        description=(
            "Host directory containing a module manifest, integration definitions, "
            "and importable adapter code."
        ),
    )
    definition_files: list[str] = Field(
        default_factory=list,
        description="Definition files relative to source_dir.",
    )
    manifest_file: str = Field(
        default="",
        description="Optional versioned external-module manifest relative to source_dir.",
    )

    @field_validator("source_dir")
    @classmethod
    def _source_dir_is_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_dir must not be empty")
        return value

    @field_validator("definition_files")
    @classmethod
    def _definition_files_are_relative(cls, values: list[str]) -> list[str]:
        for value in values:
            path = Path(value)
            if not value.strip() or path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    "definition_files entries must be non-empty paths within source_dir"
                )
        return values

    @field_validator("manifest_file")
    @classmethod
    def _manifest_file_is_relative(cls, value: str) -> str:
        if not value:
            return value
        path = Path(value)
        if not value.strip() or path.is_absolute() or ".." in path.parts:
            raise ValueError("manifest_file must be a path within source_dir")
        return value

    @model_validator(mode="after")
    def _has_package_metadata(self) -> DockerExternalIntegrationConfig:
        if not self.definition_files and not self.manifest_file:
            raise ValueError("an external package requires manifest_file or definition_files")
        return self


class DockerReadOnlyFileConfig(BaseModel):
    """One deployment-owned host file mounted read-only into the platform."""

    source_file: str = Field(description="Absolute file path on the Docker host.")
    target_file: str = Field(description="Absolute file path inside the platform container.")

    @field_validator("source_file")
    @classmethod
    def _source_is_absolute(cls, value: str) -> str:
        path = Path(value.strip()).expanduser()
        if not value.strip() or not path.is_absolute():
            raise ValueError("source_file must be an absolute host path")
        return str(path)

    @field_validator("target_file")
    @classmethod
    def _target_is_safe_absolute_path(cls, value: str) -> str:
        path = PurePosixPath(value.strip())
        if not value.strip() or not path.is_absolute() or path == PurePosixPath("/"):
            raise ValueError("target_file must be an absolute container file path")
        if ".." in path.parts:
            raise ValueError("target_file must not contain '..'")
        return str(path)


class DockerConfig(BaseModel):
    """Docker mode: the whole platform as containers on one Docker host."""

    data_dir: str = Field(
        default="~/.niuu/data",
        description=(
            "Host directory for postgres data, workspaces, credentials and models. "
            "Owned by the user who runs the platform; no root needed. Set a system "
            "path such as /var/lib/niuu to share the install between users."
        ),
    )
    compose_dir: str = Field(
        default="~/.niuu/docker",
        description="Where the rendered compose bundle and env file are written.",
    )
    host_os: str = Field(default="", description="Native host OS supplied by the installer.")
    host_arch: str = Field(
        default="", description="Native host architecture before entering Docker."
    )
    host_lan_ip: str = Field(
        default="", description="Native host LAN address, before entering Docker."
    )
    socket_path: str = Field(
        default="/var/run/docker.sock",
        description="Docker socket bind source on the daemon host (inside its VM on macOS).",
    )
    project_name: str = Field(default="niuu", description="Docker Compose project name.")
    image: str = Field(
        default="ghcr.io/niuulabs/niuu:dev",
        description="All-in-one platform image (CI publishes multi-arch `dev` and version tags).",
    )
    postgres_image: str = Field(
        default="pgvector/pgvector:pg17",
        description="PostgreSQL image with pgvector.",
    )
    skuld_image: str = Field(
        default="ghcr.io/niuulabs/skuld:dev",
        description="Session broker image started once per Forge session.",
    )
    bind_host: str = Field(
        default="0.0.0.0",
        description="Host interface the platform port is published on "
        "(0.0.0.0 = whole LAN, 127.0.0.1 = this machine only).",
    )
    postgres_password: str = Field(
        default="",
        description="Password for the postgres superuser; generated on first `niuu up` when empty.",
    )
    require_gpu: bool = Field(
        default=False,
        description="Fail preflight when no NVIDIA GPU or container runtime is present "
        "(off by default: a GPU is detected and used when present, never required).",
    )
    min_disk_space_gib: int = Field(
        default=50,
        description="Warn when the data directory has less free space than this.",
    )
    startup_timeout_seconds: float = Field(
        default=180.0,
        description="How long `niuu up` waits for the platform health endpoint.",
    )
    vllm: DockerVllmConfig = Field(default_factory=DockerVllmConfig)
    models: list[DockerModelConfig] = Field(
        default_factory=list,
        description="Models the wizard offers to serve locally; the installer writes this list.",
    )
    model_server: DockerModelServerConfig = Field(default_factory=DockerModelServerConfig)
    sign_in_client_ids: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Public OAuth client ids for device-flow sign-in, keyed by integration slug "
            "(github, gitlab). No secret is needed; the app must have the device flow enabled."
        ),
    )
    sign_in_client_secrets: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional OAuth client secrets keyed by integration slug. GitHub only needs one to "
            "refresh expiring user tokens; GitLab refreshes with the public client id alone."
        ),
    )
    external_integrations: list[DockerExternalIntegrationConfig] = Field(
        default_factory=list,
        description=(
            "Machine-local integration packages mounted read-only into the platform container."
        ),
    )
    read_only_files: list[DockerReadOnlyFileConfig] = Field(
        default_factory=list,
        description=(
            "Deployment-owned files mounted read-only into the platform container, "
            "for example provider credentials that are consumed through a file adapter."
        ),
    )
    applier_image: str = Field(
        default="docker:28-cli",
        description=(
            "Image that runs `docker compose up` when the wizard applies a stack change "
            "(a sibling container, so the platform can be recreated underneath it)."
        ),
    )
    external_integration_validation_timeout_seconds: float = Field(
        default=30.0,
        description=(
            "How long the external-integration import validator subprocess may run before "
            "it is killed and treated as a validation failure."
        ),
    )

    @model_validator(mode="after")
    def _mount_targets_are_unique(self) -> DockerConfig:
        targets = [mount.target_file for mount in self.read_only_files]
        if len(targets) != len(set(targets)):
            raise ValueError("docker.read_only_files target_file values must be unique")
        return self


class ServerConfig(BaseModel):
    """Server configuration — single port for all services."""

    host: str = Field(
        default="127.0.0.1",
        description="Host to bind the server to.",
    )
    external_host: str = Field(
        default="",
        description="Externally reachable host/IP for browser-facing URLs. Empty = reuse host.",
    )
    port: int = Field(
        default=8080,
        description="Single port for all services (Volundr, Ting, Web UI).",
    )


class ResidentsConfig(BaseModel):
    """How mini mode hosts long-lived residents on this machine."""

    runtime: Literal["process", "docker"] = Field(
        default="process",
        description=(
            "'process' runs Ravn residents as Skuld and Ravn processes on this host and "
            "needs no container engine. 'docker' runs resident images through the local "
            "Docker Engine, which must be running, and also offers the NemoClaw and "
            "NemoHermes profiles."
        ),
    )


class ServiceConfig(BaseModel):
    """Service management configuration."""

    health_check_interval_seconds: float = Field(
        default=2.0,
        description="Interval between health check polls.",
    )
    health_check_timeout_seconds: float = Field(
        default=30.0,
        description="Max time to wait for a service to become healthy.",
    )
    health_check_max_retries: int = Field(
        default=15,
        description="Max retries for health checks before declaring failure.",
    )


class TUIConfig(BaseModel):
    """TUI appearance configuration."""

    theme: str = Field(
        default="textual-dark",
        description="Textual theme name.",
    )


class CLIObservabilityConfig(ObservabilityConfig):
    """OpenTelemetry settings for the mini-mode host's local stack.

    The single place a `niuu platform up` user points the whole local stack
    (Volundr, Ting, Bifröst, and the shared host) at an OTLP collector
    (Tempo, Jaeger, etc.), instead of repeating ``observability:`` in every
    per-service config file. Each service still owns its own
    ``configure_observability`` call at its own composition root — see
    ``docs/site/operations/observability.md``.
    """

    service_name: str = Field(default="niuu-mini")


class OidcIssuerConfig(BaseModel):
    """A single trusted OIDC issuer for in-process JWT verification.

    ``jwks_uri`` may be left empty; the adapter then resolves it once via
    OIDC discovery (``<issuer>/.well-known/openid-configuration``). Both
    ``issuer`` and ``jwks_uri`` must be HTTPS, except localhost for dev.
    """

    issuer: str = Field(default="", description="OIDC issuer URL (the JWT 'iss' claim).")
    audiences: list[str] = Field(
        default_factory=list,
        description="Accepted JWT audiences for this issuer.",
    )
    jwks_uri: str = Field(
        default="",
        description="JWKS endpoint. Empty = resolve via OIDC discovery.",
    )


class AuthOidcConfig(BaseModel):
    """OIDC settings for in-process JWT verification (``auth.mode: oidc``).

    Claim names default to the same claims Envoy's jwt_authn filter maps
    today (see ``charts/volundr/values.yaml`` ``envoy.jwt.*``) so a token
    accepted in Kubernetes carries the same identity here.
    """

    issuers: list[OidcIssuerConfig] = Field(
        default_factory=list,
        description="Trusted OIDC issuers. At least one is required when auth.mode: oidc.",
    )
    clock_leeway_seconds: int = Field(
        default=60,
        ge=0,
        le=600,
        description="Allowed clock skew when checking exp/nbf.",
    )
    jwks_cache_ttl_seconds: int = Field(
        default=300,
        ge=30,
        le=86400,
        description="How long a fetched JWKS is cached before a routine refresh.",
    )
    jwks_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=60.0,
        description="HTTP timeout for OIDC discovery and JWKS fetches.",
    )
    min_refresh_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=300.0,
        description=(
            "Minimum time between forced JWKS refreshes for the same issuer "
            "(an unrecognised kid always forces one, but at most once per "
            "window — bounds a probe of many forged kids to a handful of "
            "fetches instead of one per request)."
        ),
    )
    user_id_claim: str = Field(default="sub")
    email_claim: str = Field(default="email")
    tenant_claim: str = Field(default="tenant_id")
    roles_claim: str = Field(default="resource_access.volundr.roles")


class AuthConfig(BaseModel):
    """Authentication mode for hosts without an Envoy JWT filter (mini, docker).

    ``none`` is an explicit operator choice, not a silent fallback: every
    caller is treated as admin, exactly like today. It is the default so
    existing mini and docker installs keep working unchanged. ``oidc`` is a
    claim that every inbound identity path on this host verifies a bearer
    token's signature (``identity.adapters.jwks``) plus Cedar authorization,
    matching how Kubernetes deployments behind Envoy work — see
    ``docs/site/operations/security-and-permissions.md`` for exactly which
    paths that covers today and which are still gated off.
    """

    mode: Literal["none", "oidc"] = Field(
        default="none",
        description="'none' (default, explicit no-auth) or 'oidc' (in-process JWT verification).",
    )
    oidc: AuthOidcConfig = Field(default_factory=AuthOidcConfig)

    @model_validator(mode="after")
    def _oidc_requires_issuers(self) -> AuthConfig:
        if self.mode != "oidc":
            return self
        if not self.oidc.issuers:
            raise ValueError(
                "auth.mode: oidc requires at least one auth.oidc.issuers entry "
                "(issuer + audiences), or set auth.mode: none to run without "
                "authentication instead"
            )
        for entry in self.oidc.issuers:
            if not entry.issuer:
                raise ValueError("Each auth.oidc.issuers entry requires 'issuer'")
            if not entry.audiences:
                raise ValueError(
                    f"auth.oidc issuer {entry.issuer!r} requires at least one audience"
                )
        return self


#: The mapping Envoy's jwt_authn claim already carries in practice (raw
#: Keycloak client roles) onto platform role names. Matches
#: ``volundr.config.IdentityConfig.role_mapping``'s own default so a token
#: accepted in Kubernetes maps to the same platform roles here. Völundr's own
#: identity composition (``niuu.service_runtime.create_identity_adapter``)
#: applies this automatically from ``identity.role_mapping``; services that
#: compose their own identity adapter directly (Ravn's inbound API, Ting) do
#: not, so it is threaded through explicitly below for those.
_DEFAULT_OIDC_ROLE_MAPPING = {
    "admin": "volundr:admin",
    "developer": "volundr:developer",
    "viewer": "volundr:viewer",
}


def _oidc_adapter_kwargs(oidc: AuthOidcConfig) -> dict[str, Any]:
    issuer_kwargs = [
        {"issuer": i.issuer, "audiences": list(i.audiences), "jwks_uri": i.jwks_uri}
        for i in oidc.issuers
    ]
    return {
        "issuers": issuer_kwargs,
        "clock_leeway_seconds": oidc.clock_leeway_seconds,
        "jwks_cache_ttl_seconds": oidc.jwks_cache_ttl_seconds,
        "jwks_timeout_seconds": oidc.jwks_timeout_seconds,
        "min_refresh_interval_seconds": oidc.min_refresh_interval_seconds,
        "user_id_claim": oidc.user_id_claim,
        "email_claim": oidc.email_claim,
        "tenant_claim": oidc.tenant_claim,
        "roles_claim": oidc.roles_claim,
        "role_mapping": dict(_DEFAULT_OIDC_ROLE_MAPPING),
    }


def auth_adapter_env(auth: AuthConfig) -> dict[str, str]:
    """Env vars selecting identity/authorization adapters from ``auth.mode``.

    Shared by the mini host (``cli.commands.platform``) and docker mode
    (``cli.services.compose_bundle``) so both compute the same adapter
    selection from the same config, for every co-hosted service that reads
    these env vars: Völundr/Identity's shared ``IDENTITY__ADAPTER`` /
    ``AUTHORIZATION__ADAPTER`` slot, Ravn's own inbound API
    (``RAVN_API_AUTH__*``), Ting's own inbound API (``AUTH__*``), the
    niuu root app's session-proxy identity (``HOST_IDENTITY__*``), and
    Mímir's own inbound API (``MIMIR_AUTH__*``, read via
    ``mimir.config.MimirServiceConfig.identity_adapter``). ``none``
    reproduces today's explicit allow-all wiring; ``oidc`` switches every one
    of those slots to in-process JWT verification via JWKS plus the bundled
    Cedar policies, matching Kubernetes. Bifröst does not read one of these
    slots — it is a standalone process configured via ``BIFROST_CONFIG``
    (see ``cli.commands.platform._resolve_local_pod_manager_env``). Guild
    needs no slot either: it forwards only the caller's bearer token to a
    remote instance (see
    ``niuu.adapters.inbound.remote_urls.forward_identity_headers``).
    """
    if auth.mode == "none":
        return {
            "IDENTITY__ADAPTER": "identity.adapters.identity.AllowAllIdentityAdapter",
            "AUTHORIZATION__ADAPTER": (
                "identity.adapters.authorization.AllowAllAuthorizationAdapter"
            ),
            "RAVN_API_AUTH__ADAPTER": (
                "identity.adapters.identity.AllowAllHeaderAuthenticationAdapter"
            ),
            # Ting's own default (EnvoyHeaderAuthenticationAdapter +
            # allow_anonymous_dev) trusts caller-supplied x-auth-* headers
            # whenever they're present, even with no Envoy in front of it.
            # 'none' means every caller is admin, not "trust whatever the
            # caller claims" — select the same explicit allow-all adapter
            # every other co-hosted service gets.
            "AUTH__ADAPTER": "identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
            "HOST_IDENTITY__ADAPTER": (
                "identity.adapters.identity.AllowAllHeaderAuthenticationAdapter"
            ),
            "MIMIR_AUTH__ADAPTER": (
                "identity.adapters.identity.AllowAllHeaderAuthenticationAdapter"
            ),
            "AUTH__ALLOW_ANONYMOUS_DEV": "true",
            "AUTH_MODE": "none",
        }

    oidc_kwargs = _oidc_adapter_kwargs(auth.oidc)
    oidc_kwargs_json = json.dumps(oidc_kwargs)
    return {
        "IDENTITY__ADAPTER": "identity.adapters.jwks.JwksIdentityAdapter",
        "IDENTITY__KWARGS": oidc_kwargs_json,
        "AUTHORIZATION__ADAPTER": "identity.adapters.cedar.CedarAuthorizationAdapter",
        "RAVN_API_AUTH__ADAPTER": "identity.adapters.jwks.JwksBearerAuthenticationAdapter",
        "RAVN_API_AUTH__KWARGS": oidc_kwargs_json,
        "AUTH__ADAPTER": "identity.adapters.jwks.JwksBearerAuthenticationAdapter",
        "AUTH__KWARGS": oidc_kwargs_json,
        "HOST_IDENTITY__ADAPTER": "identity.adapters.jwks.JwksBearerAuthenticationAdapter",
        "HOST_IDENTITY__KWARGS": oidc_kwargs_json,
        "MIMIR_AUTH__ADAPTER": "identity.adapters.jwks.JwksBearerAuthenticationAdapter",
        "MIMIR_AUTH__KWARGS": oidc_kwargs_json,
        "AUTH__ALLOW_ANONYMOUS_DEV": "false",
        "AUTH_MODE": "oidc",
    }


class CLISettings(BaseSettings):
    """Root configuration for the niuu CLI."""

    model_config = SettingsConfigDict(
        env_prefix="NIUU_",
        env_nested_delimiter="__",
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Resolve config paths at instantiation time (after --config callback)
        return (
            kwargs["init_settings"],
            kwargs["env_settings"],
            YamlConfigSettingsSource(
                settings_cls,
                yaml_file=[str(p) for p in config_paths()],
            ),
        )

    mode: str = Field(
        default="mini",
        description="Operating mode: 'mini', 'openshell', 'cluster', or 'docker'.",
    )
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    # Named host_auth, not auth: Ting's own Settings (src/ting/config.py) reads
    # a top-level `auth:` key from this SAME config.yaml/NIUU_CONFIG file for
    # its own, differently-shaped AuthConfig (adapter/kwargs/allow_anonymous_dev).
    # A shared `auth:` key would silently collide between the two schemas.
    host_auth: AuthConfig = Field(default_factory=AuthConfig)
    pod_manager: PodManagerConfig = Field(default_factory=PodManagerConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    residents: ResidentsConfig = Field(default_factory=ResidentsConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    services: ServiceConfig = Field(default_factory=ServiceConfig)
    bifrost: BifrostConfig = Field(default_factory=BifrostConfig)
    observability: CLIObservabilityConfig = Field(default_factory=CLIObservabilityConfig)
    compute: ComputeConfig | None = None
    service_overrides: dict[str, PerServiceConfig] = Field(
        default_factory=dict,
        description="Per-service enabled/port overrides keyed by service name.",
    )
    tui: TUIConfig = Field(default_factory=TUIConfig)
    context: str = Field(
        default="local",
        description="Active context (local, remote, etc.).",
    )
    version: str = Field(default="0.1.0")

    @model_validator(mode="after")
    def _docker_compute_uses_vm_pod_manager(self) -> CLISettings:
        if self.mode != "docker" or self.compute is None:
            return self
        expected = "volundr.adapters.outbound.vm_pod_manager.VmPodManager"
        if self.pod_manager.adapter != expected:
            raise ValueError(f"Docker compute requires pod_manager.adapter={expected}")
        kwargs = self.pod_manager.adapter_kwargs()
        if not str(kwargs.get("profile") or "").strip():
            raise ValueError("Docker compute requires pod_manager.profile")
        if kwargs.get("pool_id") != self.compute.pool_id:
            raise ValueError("Docker compute pool_id must match pod_manager.pool_id")
        if kwargs.get("max_machines") != self.compute.max_machines:
            raise ValueError("Docker compute max_machines must match pod_manager.max_machines")
        if self.compute.runtime is None:
            raise ValueError("Docker compute requires a runtime adapter")
        return self

    #: Plugins that do not yet go through any auth.mode-aware identity check
    #: (they trust x-auth-* headers, or accept requests unauthenticated,
    #: independently of IDENTITY__ADAPTER/AUTH_MODE) but default to enabled.
    #: An operator must explicitly disable each one to run auth.mode: oidc —
    #: oidc must not claim coverage a host does not actually have. Hardening
    #: any of these removes it from this list rather than adding an override.
    _OIDC_UNCOVERED_PLUGINS: ClassVar[dict[str, str]] = {
        "bifrost": (
            "Bifröst's own inbound auth now verifies bearer tokens correctly under "
            "oidc, but nothing yet supplies sessions or residents with a verifiable "
            "credential to send it: skuld.config.ModelGatewayConfig.token has no "
            "real default (it used to be the placeholder literal 'niuu-gateway', "
            "meaningful only because 'open' mode ignores it), and "
            "ravn.adapters.llm.bifrost.BifrostAdapter (used by local residents) "
            "sends no Authorization header at all. Every model call routed through "
            "Bifröst — every session and resident — would get 401. Minting and "
            "threading a real per-session/per-resident PAT through both paths is "
            "tracked as follow-up work"
        ),
    }

    @model_validator(mode="after")
    def _oidc_covers_every_enabled_mount(self) -> CLISettings:
        if self.host_auth.mode != "oidc":
            return self
        for name, reason in self._OIDC_UNCOVERED_PLUGINS.items():
            if self.plugins.enabled.get(name, True):
                raise ValueError(
                    f"auth.mode: oidc is not enabled while the {name!r} plugin is "
                    f"active: {reason}. Set plugins.enabled.{name}: false until it is "
                    "hardened, or set auth.mode: none to be honest that this host "
                    "runs without authentication."
                )
        return self
