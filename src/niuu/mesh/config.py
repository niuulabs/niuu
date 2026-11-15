"""Shared configuration for Niuu mesh peers and their NATS transports."""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

#: Completed mesh RPC replies a Sleipnir mesh peer keeps, keyed on the request
#: ``event_id``, so a redelivered request gets its stored reply instead of a
#: second handler run.  This is the default for the Ravn and Skuld
#: ``mesh.rpc_reply_cache_size`` setting.
DEFAULT_RPC_REPLY_CACHE_SIZE = 1024


class MeshNatsExtraSubscriptionConfig(BaseModel):
    """Additional JetStream filter subject for an existing stream."""

    subject: str = Field(default="")
    stream_name: str = Field(default="")
    event_types: list[str] = Field(default_factory=list)


class MeshNatsCoreSubscriptionConfig(BaseModel):
    """Additional core NATS filter subject for live control messages."""

    subject: str = Field(default="")


class MeshNatsConfig(BaseSettings):
    """NATS JetStream settings shared by mesh participants."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", case_sensitive=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del cls, settings_cls, dotenv_settings
        return env_settings, init_settings, file_secret_settings

    servers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["nats://localhost:4222"],
        validation_alias=AliasChoices("servers", "NATS_URL"),
    )
    stream_name: str = Field(default="ravn_environment")
    jetstream_domain: str = Field(default="")
    subject_prefix: str = Field(default="ravn.environment")
    consumer_group: str = Field(default="")
    publish_timeout_s: float = Field(default=10.0)
    replay_from_sequence: int | None = Field(default=None)
    retention: str = Field(default="limits")
    max_age_seconds: int = Field(default=7 * 24 * 3600)
    max_bytes: int = Field(default=1024 * 1024 * 1024)
    ring_buffer_depth: int = Field(
        default=1000,
        description="Events queued per subscription before acks are withheld (backpressure).",
    )
    max_deliver: int = Field(
        default=5,
        description="JetStream deliveries of one event before it is dead-lettered.",
    )
    ack_wait_s: float = Field(
        default=30.0,
        description="Seconds without an ack or progress ping before JetStream redelivers.",
    )
    ack_progress_interval_s: float = Field(
        default=10.0,
        description="Seconds between progress pings for unsettled events (< ack_wait_s).",
    )
    max_ack_pending: int | None = Field(
        default=None,
        description="Unacked events JetStream may push per consumer; unset = ring_buffer_depth.",
    )
    nak_backoff_s: list[float] = Field(
        default_factory=lambda: [1.0, 5.0, 30.0, 60.0],
        description="Redelivery delay after each failed handler attempt; last entry repeats.",
    )
    consumer_health_check_interval_s: float = Field(
        default=15.0,
        description="Seconds between checks that each JetStream consumer (and its stream) "
        "still exists.",
    )
    consumer_recovery_backoff_s: list[float] = Field(
        default_factory=lambda: [1.0, 5.0, 15.0, 30.0],
        description="Retry delay after each failed attempt to recreate a lost consumer "
        "while its stream is still absent; last entry repeats.",
    )
    connect_timeout_s: float = Field(default=10.0)
    max_reconnect_attempts: int = Field(default=60)
    ensure_stream: bool = Field(default=True)
    tls_ca_file: str = Field(default="")
    tls_ca_pem: str = Field(default="")
    tls_cert_file: str = Field(default="")
    tls_key_file: str = Field(default="")
    tls_hostname: str = Field(default="")
    tls_handshake_first: bool = Field(default=False)
    tls_legacy_ca: bool = Field(default=False)
    tls_insecure_skip_verify: bool = Field(default=False)
    user: str = Field(default="")
    user_env: str = Field(default="")
    password_env: str = Field(default="")
    token_env: str = Field(default="")
    nkeys_seed_file: str = Field(default="")
    nkeys_seed_env: str = Field(default="")
    extra_subscriptions: list[MeshNatsExtraSubscriptionConfig] = Field(default_factory=list)
    core_subscriptions: list[MeshNatsCoreSubscriptionConfig] = Field(default_factory=list)

    @field_validator("servers", mode="before")
    @classmethod
    def _parse_servers(cls, value: object) -> object:
        del cls
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):  # a list that travelled through an environment variable
            return json.loads(text)
        return [entry.strip() for entry in text.split(",") if entry.strip()]


class NngMeshConfig(BaseModel):
    """nng transport settings for Pi-mode mesh (NIU-517)."""

    pub_sub_address: str = Field(
        default="tcp://*:7480",
        description="nng PUB/SUB listen address (e.g. tcp://*:7480 or ipc:///tmp/ravn-pub.ipc).",
    )
    req_rep_address: str = Field(
        default="tcp://*:7481",
        description="nng REQ/REP listen address (e.g. tcp://*:7481 or ipc:///tmp/ravn-rep.ipc).",
    )


class MeshSleipnirConfig(BaseModel):
    """RabbitMQ-specific mesh settings for infra-mode mesh (NIU-517)."""

    exchange: str = Field(
        default="mesh",
        description="Topic exchange used for Ravn mesh pub/sub and RPC.",
    )
    rpc_timeout_s: float = Field(
        default=10.0,
        description="Default RPC reply timeout in seconds.",
    )


class MeshAttestationConfig(BaseModel):
    """Proof of who sent each mesh message (``niuu.mesh.attestation``).

    On by default wherever the mesh is: a mesh peer signs what it sends with a key
    that Forge certified, and verifies what it receives. Nothing here needs setting for
    it to work; the knobs only tune it.
    """

    required: bool = Field(
        default=True,
        description=(
            "Sign every mesh message and refuse every one that is not verifiably signed. "
            "False is an operator decision to run an unauthenticated mesh (a peer then "
            "accepts any claimed sender); with True, a peer that cannot get a certificate "
            "from Forge does not start its mesh."
        ),
    )
    renew_fraction: float = Field(
        default=0.5,
        gt=0.0,
        lt=1.0,
        description=(
            "Fraction of the certificate lifetime after which it is renewed in the "
            "background. The lifetime itself is Forge's forge_mcp.mesh_identity.ttl_seconds."
        ),
    )
    retry_fraction: float = Field(
        default=0.1,
        gt=0.0,
        lt=1.0,
        description=(
            "Fraction of the certificate lifetime between retries after a failed renewal. "
            "Retries are counted; a certificate that expires before one succeeds makes "
            "sending fail with the reason, and peers refuse what this peer signed."
        ),
    )
    service_certificate_ttl_s: int = Field(
        default=900,
        ge=2,
        description=(
            "Lifetime of the certificate a platform service (Ting, Mímir, ...) issues itself "
            "to sign the platform events it publishes. It bounds how long a leaked certificate "
            "is believed; the service renews it at half of it."
        ),
    )
    clock_skew_s: float = Field(
        default=300.0,
        gt=0.0,
        description=(
            "How far a request's signing time may differ from this peer's clock. Bounds "
            "how long a captured request or reply could be replayed; a request outside it "
            "is refused as stale."
        ),
    )
    event_max_age_s: float = Field(
        default=3600.0,
        gt=0.0,
        description=(
            "Oldest a published event may be when it is received. Bounds replay of a "
            "captured event. It must exceed the transport's redelivery window (JetStream "
            "nak backoff) and any consumer-recovery replay window, or those redeliveries "
            "are refused as stale and counted."
        ),
    )


class MeshSettings(BaseModel):
    """The one decision about proof of who sent a mesh message or published a platform event.

    A top-level ``mesh`` section of Forge's, Ting's and every other platform service's
    settings, with the same meaning in each. See :class:`MeshAttestationConfig`.
    """

    attestation: MeshAttestationConfig = Field(default_factory=MeshAttestationConfig)


class DiscoveryMdnsConfig(BaseModel):
    """mDNS-specific discovery settings (Pi mode)."""

    service_type: str = Field(
        default="_ravn._tcp.local.",
        description="mDNS service type for flock discovery. Must end with '.' per DNS-SD spec.",
    )
    handshake_timeout_s: float = Field(
        default=5.0,
        description="Seconds to wait for HMAC handshake completion.",
    )
    handshake_port: int = Field(
        default=7482,
        description=(
            "nng PAIR port used for HMAC handshake exchange. "
            "Must be unique per instance on the same host."
        ),
    )
    convergence_wait_s: float = Field(
        default=3.0,
        description=(
            "Seconds to wait after start() for mDNS announcements "
            "to arrive before querying the peer table."
        ),
    )


class DiscoverySleipnirConfig(BaseModel):
    """Sleipnir-specific discovery settings (infra mode)."""

    heartbeat_interval_s: float = Field(
        default=60.0,
        description="Seconds between Sleipnir announce heartbeats.",
    )
    convergence_wait_s: float = Field(
        default=5.0,
        description="Seconds to wait on startup for other peers to announce.",
    )
    spiffe_audience_env: str = Field(
        default="SPIFFE_TRUST_DOMAIN",
        description="Env var containing the SPIFFE trust domain for JWT-SVID validation.",
    )


class DiscoveryK8sConfig(BaseModel):
    """Kubernetes label adapter settings (infra mode cold-start)."""

    namespace: str = Field(
        default="",
        description="K8s namespace to query (empty = all namespaces).",
    )
    label_selector: str = Field(
        default="ravn.niuu.world/role=agent",
        description="Label selector used to list Ravn pods.",
    )


class DiscoveryConfig(BaseModel):
    """Flock peer detection configuration (NIU-538).

    Uses dynamic adapter loading. All adapters in the ``adapters`` list run
    simultaneously — peers are merged from all backends (union semantics).

    Example::

        discovery:
          enabled: true
          adapters:
            - adapter: niuu.adapters.discovery.mdns.MdnsDiscoveryAdapter
              handshake_port: 7482
            - adapter: niuu.adapters.discovery.k8s.K8sDiscoveryAdapter
              namespace: ravn
              label_selector: "ravn.niuu.world/role=agent"
    """

    enabled: bool = Field(
        default=False,
        description="Enable flock peer discovery.",
    )
    realm_id: str = Field(
        default="",
        description="Explicit discovery realm identity; empty uses the persisted realm key.",
    )
    adapters: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of discovery adapters to run simultaneously. Each entry has "
            "'adapter' (fully-qualified class path) plus adapter-specific kwargs. "
            "All adapters run in parallel; peer tables are merged."
        ),
    )
    # Common settings passed to all adapters
    heartbeat_interval_s: float = Field(
        default=30.0,
        description="Seconds between liveness heartbeats.",
    )
    peer_ttl_s: float = Field(
        default=90.0,
        description="Seconds of missed heartbeats before a peer is evicted (≈ 3 heartbeats).",
    )
    realm_id_env: str = Field(
        default="RAVN_REALM_ID",
        description="Env var carrying the realm_id for infra mode (OpenBao / K8s label).",
    )
    # Legacy fields — deprecated, use adapters list instead
    adapter: str = Field(
        default="",
        description="DEPRECATED: Use 'adapters' list instead. Legacy single-adapter mode.",
    )
    mdns: DiscoveryMdnsConfig = Field(default_factory=DiscoveryMdnsConfig)
    sleipnir: DiscoverySleipnirConfig = Field(default_factory=DiscoverySleipnirConfig)
    k8s: DiscoveryK8sConfig = Field(default_factory=DiscoveryK8sConfig)


class MeshConfig(BaseModel):
    """Peer-to-peer mesh transport configuration (NIU-517).

    Uses dynamic adapter loading. All adapters in the ``adapters`` list run
    simultaneously — publish fans out to all transports, subscribe receives
    from any transport.

    Example::

        mesh:
          enabled: true
          adapters:
            - adapter: sleipnir
            - adapter: niuu.adapters.mesh.webhook.WebhookMeshAdapter
              listen_port: 7483
              hmac_secret_env: RAVN_WEBHOOK_SECRET
    """

    enabled: bool = Field(default=False)
    adapters: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of mesh transport adapters to run simultaneously. Each entry has "
            "'adapter' (fully-qualified class path) plus adapter-specific kwargs. "
            "All transports are active: publish fans out to all, subscribe receives from any."
        ),
    )
    # Common settings passed to all adapters
    rpc_timeout_s: float = Field(
        default=10.0,
        description="Default RPC reply timeout in seconds.",
    )
    rpc_reply_cache_size: int = Field(
        default=DEFAULT_RPC_REPLY_CACHE_SIZE,
        ge=1,
        description=(
            "Completed RPC replies a Sleipnir mesh adapter keeps, keyed on the request "
            "event_id, so a redelivered request gets its stored reply instead of "
            "running the handler again. Keep it above the number of RPC requests this "
            "peer completes within the transport's redelivery window (JetStream: the "
            "nak backoff across max_deliver deliveries, and ack_wait)."
        ),
    )
    own_peer_id: str = Field(
        default="",
        description="This peer's unique mesh identifier (auto: hostname when empty).",
    )
    # Legacy fields — deprecated, use adapters list instead
    adapter: str = Field(
        default="",
        description="DEPRECATED: Use 'adapters' list instead. Legacy single-adapter mode.",
    )
    nng: NngMeshConfig = Field(default_factory=NngMeshConfig)
    sleipnir: MeshSleipnirConfig = Field(default_factory=MeshSleipnirConfig)
    nats: MeshNatsConfig = Field(default_factory=MeshNatsConfig)


__all__ = [
    "DEFAULT_RPC_REPLY_CACHE_SIZE",
    "DiscoveryConfig",
    "DiscoveryK8sConfig",
    "DiscoveryMdnsConfig",
    "DiscoverySleipnirConfig",
    "MeshConfig",
    "MeshNatsConfig",
    "MeshNatsCoreSubscriptionConfig",
    "MeshNatsExtraSubscriptionConfig",
    "MeshSleipnirConfig",
    "NngMeshConfig",
]
