"""Configuration models for the Mímir client side.

``MimirConfig`` describes how a runtime mounts one or more Mímir instances
(remote HTTP services, or a ``MimirPort`` adapter such as gbrain), how writes
are routed between them (each write goes to exactly one instance), and the
reflex settings that go with them. Runtimes embed it in their own settings;
``mimir.client_settings`` loads it standalone.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MimirAuthConfig(BaseModel):
    """Auth configuration for a remote Mímir instance."""

    type: Literal["bearer", "workload", "spiffe"] = Field(
        default="bearer",
        description=(
            "Auth mechanism: 'bearer' (dev), 'workload' (projected JWT exchange), "
            "or 'spiffe' (production mTLS)."
        ),
    )
    token: str | None = Field(
        default=None,
        description="Bearer token value (when type=bearer).",
    )
    token_file: str | None = Field(
        default=None,
        description="File containing a bearer token (when type=bearer).",
    )
    token_env: str | None = Field(
        default=None,
        description="Environment variable containing a bearer token (when type=bearer).",
    )
    exchange_url: str | None = Field(
        default=None,
        description="Workload token exchange URL (when type=workload).",
    )
    audiences: list[str] = Field(
        default_factory=lambda: ["mimir"],
        description="Target audiences requested from workload token exchange.",
    )
    trust_domain: str | None = Field(
        default=None,
        description="SPIFFE trust domain (when type=spiffe), e.g. 'niuu.world'.",
    )
    token_refresh_margin_seconds: float = Field(
        default=30.0,
        description=(
            "Seconds of safety margin before a cached workload token's reported "
            "expiry at which a fresh exchange is triggered (type=workload)."
        ),
    )


class MimirInstanceConfig(BaseModel):
    """Configuration for a single Mímir instance in the composite adapter.

    Either ``adapter`` (a ``MimirPort`` class) or ``url`` (HTTP service) must be set.
    """

    name: str = Field(description="Logical name, e.g. 'local', 'shared', 'kanuck'.")
    role: Literal["shared", "local", "domain"] = Field(
        default="local",
        description="Instance role.",
    )
    url: str | None = Field(
        default=None,
        description="Base URL for a remote HttpMimirAdapter.",
    )
    auth: MimirAuthConfig | None = Field(
        default=None,
        description="Auth config for remote instances.",
    )
    categories: list[str] | None = Field(
        default=None,
        description="Category filter for domain-scoped Mímirs. None means all categories.",
    )
    adapter: str = Field(
        default="",
        description=(
            "Fully-qualified MimirPort class, e.g. "
            "'mimir.adapters.gbrain.GBrainMimirAdapter'. Takes precedence "
            "over url; constructor arguments come from kwargs."
        ),
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Constructor kwargs passed to the adapter named in `adapter`.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Maps adapter kwarg names to env var names for secret injection.",
    )
    read_priority: int = Field(
        default=0,
        description="Read order — lower value is queried first (local=0, shared=1, domain=2).",
    )
    platform_mount: str = Field(
        default="",
        description=(
            "Name the platform's Mímir lists this instance under, the mount /mimir "
            "opens it by. The resident reports it with its identity so a workspace "
            "made around the resident can open this memory. Empty means the "
            "platform does not serve this instance, and the workspace says so."
        ),
    )


class MimirReadRetryConfig(BaseModel):
    """Retry policy for reads that every mount failed to answer.

    A mount restart takes a remote Mímir out of service for as long as it needs
    to rebuild its search index, during which the gateway in front of it answers
    503. Callers that verify provenance treat an unanswerable read as a hard
    failure, so a restart lands as a failed workflow rather than a pause.
    Retrying across that window is the difference between the two.

    The budget is deliberately wall-clock rather than an attempt count: what
    matters is covering a restart, and attempt counts silently stop covering it
    as backoff changes.
    """

    max_seconds: float = Field(
        default=90.0,
        ge=0.0,
        description=(
            "Total wall-clock budget for retrying a read that no mount could answer. "
            "Sized to outlast a Mímir restart. 0 disables retrying."
        ),
    )
    initial_backoff_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Delay before the first retry; doubles per attempt up to max_backoff_seconds.",
    )
    max_backoff_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Ceiling for the exponential backoff between retries.",
    )


class MimirWriteRoutingConfig(BaseModel):
    """Config-driven write routing for the CompositeMimirAdapter.

    Every rule and the default must name exactly one mount: a write goes to one
    mount, and a selection of none or several raises when the write is made.

    Example YAML::

        write_routing:
          rules:
            - prefix: "self/"
              mounts: ["local"]
            - prefix: "household/"
              mounts: ["shared"]
          default: ["local"]
    """

    rules: list[dict] = Field(
        default_factory=list,
        description="Ordered list of {prefix, mounts} routing rules.",
    )
    default: list[str] = Field(
        default_factory=lambda: ["local"],
        description="The one mount a write goes to when no prefix matches.",
    )


class MimirReflexConfig(BaseModel):
    """Retrieval reflex — deterministic Mímir entity pointer injection (NIU-1059).

    A zero-LLM scanner that matches incoming agent-turn messages against the
    Mímir entity feed and prefixes compact pointers (never page bodies).
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable automatic retrieval-reflex pointer injection on agent turns. "
            "Disabled by default; agents can still query Mímir explicitly."
        ),
    )
    max_pointers: int = Field(
        default=5,
        description="Maximum number of entity pointers injected per turn.",
    )
    cache_ttl_seconds: int = Field(
        default=300,
        description="How long (seconds) the entity index is cached before refetching.",
    )
    base_url: str = Field(
        default="",
        description=(
            "Base URL of the Mímir HTTP service exposing GET /mimir/entities/index. "
            "When empty, falls back to the first mimir.instances entry with a url."
        ),
    )
    timeout_seconds: float = Field(
        default=5.0,
        description="HTTP timeout (seconds) for the entity feed request.",
    )


class MimirConfig(BaseModel):
    """Mímir persistent compounding knowledge base configuration (NIU-540).

    The knowledge lives in the stores named by ``instances``. There is no
    implicit local store: ``enabled: true`` with no instances is a
    configuration error, and ``enabled: false`` is the operator's choice to run
    without Mímir.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Enable the Mímir knowledge base. When true, `instances` must name at "
            "least one; set false to run without Mímir."
        ),
    )
    instances: list[MimirInstanceConfig] = Field(
        default_factory=list,
        description=(
            "The Mímir instances to mount (CompositeMimirAdapter). Required when "
            "`enabled` is true. Each entry is a MimirPort adapter (adapter=) or a "
            "remote HTTP service (url=)."
        ),
    )
    write_routing: MimirWriteRoutingConfig = Field(
        default_factory=MimirWriteRoutingConfig,
        description="Write routing rules for the CompositeMimirAdapter.",
    )
    read_retry: MimirReadRetryConfig = Field(
        default_factory=MimirReadRetryConfig,
        description="Retry policy for reads that no mount could answer.",
    )
    reflex: MimirReflexConfig = Field(
        default_factory=MimirReflexConfig,
        description="Retrieval reflex — deterministic entity pointer injection per turn.",
    )
