"""Explicit configuration for generic VM allocation administration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niuu.config import DynamicAdapterConfig, HttpAuthAdapterConfig
from niuu.config_models import DatabaseConfig
from volundr.domain.compute import MachineBootstrap


class HostConnectorConfig(DynamicAdapterConfig):
    """One way Forge reaches registered hosts; a host names it by ``binding_id``."""

    binding_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")


class ComputeHostsConfig(BaseModel):
    """Registered hosts: machines that join this Forge's pool instead of being created.

    Needs ``compute.provider`` to be the registered-host provider and an execution catalog,
    since each host runs one catalog execution.
    """

    model_config = ConfigDict(extra="forbid")

    join_url: str = Field(
        min_length=1,
        # A hostname (or IPv4) and an optional port: the URL goes into a shell line.
        pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$",
        description=(
            "Forge's base URL as hosts reach it (scheme and host, no path); the join "
            "command and join.sh call it. http:// is refused unless allow_plaintext_join"
        ),
    )
    allow_plaintext_join: bool = Field(
        default=False,
        description="Accept an http:// join_url; pairing codes then cross the network in clear",
    )
    connectors: list[HostConnectorConfig] = Field(min_length=1)
    pairing_code_ttl_seconds: float = Field(
        default=600,
        gt=0,
        description="How long a minted host pairing code can be used; bounded by the "
        "workload token's own lifetime",
    )
    check_interval_seconds: float = Field(
        default=300,
        gt=0,
        description="How often every host is audited while it is registered",
    )
    pending_check_interval_seconds: float = Field(
        default=5,
        gt=0,
        description="How often a host that has not been reached yet is tried",
    )
    command_timeout_seconds: float = Field(
        default=120, gt=0, description="Bound on one check or audit run on a host"
    )
    reserved_cpu: float = Field(
        ge=0, description="CPU cores kept for the host itself when slots are sized"
    )
    reserved_memory_mib: int = Field(
        ge=0, description="Memory kept for the host itself when slots are sized"
    )
    min_free_disk_mib: int = Field(
        ge=0,
        description="Free space on / below which the audit quarantines a host",
    )

    @model_validator(mode="after")
    def refuse_plaintext_join(self):
        if self.join_url.startswith("http://") and not self.allow_plaintext_join:
            raise ValueError(
                "compute.hosts.join_url is plaintext http; pairing codes would cross the "
                "network in clear. Use https, or set allow_plaintext_join: true"
            )
        ids = [connector.binding_id for connector in self.connectors]
        if len(ids) != len(set(ids)):
            raise ValueError("compute.hosts.connectors binding_id values must be unique")
        return self


class ComputeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    pool_id: str = Field(min_length=1)
    max_machines: int = Field(gt=0)
    provider: DynamicAdapterConfig
    auth: HttpAuthAdapterConfig | None = None
    runtime: DynamicAdapterConfig | None = None
    execution_catalog: DynamicAdapterConfig | None = Field(
        default=None, description="File-backed operator execution catalog adapter"
    )
    credential_delivery: DynamicAdapterConfig | None = Field(
        default=None,
        description=(
            "How VM guests receive ServiceAccount tokens and the Forge session credential: "
            "SshPushCredentialDelivery or MetadataCredentialDelivery "
            "(volundr.adapters.outbound.guest_credential_delivery). Required when sessions "
            "carry projected tokens or a Forge session credential."
        ),
    )
    guest_identity: DynamicAdapterConfig | None = Field(
        default=None,
        description=(
            "How a guest proves its allocation to Forge's metadata endpoint "
            "(HostKeyGuestIdentity or SpiffeGuestIdentity). Enables the endpoint; "
            "required by MetadataCredentialDelivery."
        ),
    )
    service_account_tokens: DynamicAdapterConfig | None = Field(
        default=None,
        description=(
            "Issuer of session ServiceAccount tokens for VM guests "
            "(KubernetesServiceAccountTokenIssuer)."
        ),
    )
    provider_binding: str = Field(
        default="", description="Stable deployment provider identity used by execution profiles"
    )
    database: DatabaseConfig | None = None
    hosts: ComputeHostsConfig | None = Field(
        default=None,
        description="Registered hosts; set with the registered-host provider",
    )
    bootstrap: MachineBootstrap = Field(default_factory=MachineBootstrap)
    provisioning_timeout_seconds: float = Field(default=600, gt=0)
    cleanup_timeout_seconds: float = Field(default=300, gt=0)
    slots_per_machine: int = Field(
        default=1,
        gt=0,
        description=(
            "Sessions one machine serves at once. Above 1 needs a runtime that supports slots; "
            "startup fails when the configured runtime does not"
        ),
    )
    max_sessions_per_machine: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Sessions a machine serves before it drains and is replaced (reuse_policy: replace). "
            "Unset means slots_per_machine, one session per slot. Above slots_per_machine the "
            "cleaned slots are reused, which needs a runtime that supports reuse"
        ),
    )
    max_machine_age_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Age after which a machine takes no new sessions and drains once empty "
            "(reuse_policy: replace). Unset means no age limit"
        ),
    )
    reuse_policy: Literal["replace", "reuse"] = "replace"
    warm_min: int = Field(default=0, ge=0)
    max_provisioning: int = Field(default=1, gt=0)
    idle_timeout_seconds: float = Field(default=3600, gt=0)
    retry_interval_seconds: float = Field(default=10, gt=0)
    retry_max_seconds: float = Field(default=300, gt=0)
    maintenance_interval_seconds: float = Field(default=10, gt=0)
    poll_interval_seconds: float = Field(default=2, gt=0)
