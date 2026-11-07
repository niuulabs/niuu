"""Explicit configuration for generic VM allocation administration."""

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niuu.config import DynamicAdapterConfig, HttpAuthAdapterConfig
from niuu.config_models import DatabaseConfig
from volundr.domain.compute import MachineBootstrap


class HostConnectorConfig(DynamicAdapterConfig):
    """One way Forge reaches registered hosts; a host names it by ``binding_id``."""

    binding_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")


#: A hostname or IPv4 address: what a join URL may name, since it goes into a shell line.
_JOIN_HOST = re.compile(r"^[A-Za-z0-9.-]+$")


class HostJoinListenerConfig(BaseModel):
    """Forge's own TLS listener for the two routes a joining host calls, and nothing else.

    Its certificate is self-signed and the join command pins its public key, so a host
    joins safely without a CA. The key and certificate are generated once (mode 0600),
    never overwritten, and reused across restarts, so the pin stays the same.
    """

    model_config = ConfigDict(extra="forbid")

    bind_host: str = Field(
        default="0.0.0.0",
        min_length=1,
        description="Interface the listener binds; hosts on the LAN must reach it",
    )
    port: int = Field(default=8443, ge=1, le=65535, description="TCP port of the listener")
    cert_file: str = Field(
        default="~/.niuu/forge-host-join-cert.pem",
        min_length=1,
        description="Self-signed certificate, generated from key_file when absent",
    )
    key_file: str = Field(
        default="~/.niuu/forge-host-join-key.pem",
        min_length=1,
        description="Private key the pin is computed from; generated once, mode 0600",
    )
    key_bits: int = Field(default=2048, ge=2048, description="Size of a generated RSA key")


class ComputeHostsConfig(BaseModel):
    """Registered hosts: machines that join this Forge's pool instead of being created.

    Needs ``compute.provider`` to be the registered-host provider and an execution catalog,
    since each host runs one catalog execution.

    Where hosts reach the join routes:

    - ``join_url`` without ``join_listener``: the routes are served by Forge's API and
      ``join_url`` points at it (a cluster ingress with a real certificate).
    - ``join_listener``: Forge serves the routes on its own pinned TLS listener, and only
      there. Hosts reach it at ``join_url`` when set (for example a port forward), else
      at ``https://<server_public_host>:<port>``.
    - Neither: ``join_listener`` with its defaults, so a local Forge needs no setting.
    """

    model_config = ConfigDict(extra="forbid")

    join_url: str = Field(
        default="",
        # A hostname (or IPv4) and an optional port: the URL goes into a shell line.
        pattern=r"^(https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?)?$",
        description=(
            "Forge's base URL as hosts reach it (scheme and host, no path); the join "
            "command and join.sh call it. http:// is refused unless allow_plaintext_join. "
            "Unset: the join listener at server_public_host"
        ),
    )
    join_listener: HostJoinListenerConfig | None = Field(
        default=None,
        description="Forge's pinned TLS listener for joining hosts; the default without join_url",
    )
    allow_plaintext_join: bool = Field(
        default=False,
        description="Accept an http:// join_url; pairing codes then cross the network in clear",
    )
    connectors: list[HostConnectorConfig] = Field(min_length=1)
    pairing_signing_key_file: str = Field(
        default="~/.niuu/forge-host-pairing-key.pem",
        min_length=1,
        description=(
            "Private RSA key that signs host pairing codes when workload identity has no "
            "configured key and the identity adapter is allow-all (local mini mode); "
            "generated once with mode 0600 and never overwritten"
        ),
    )
    pairing_signing_key_bits: int = Field(
        default=2048, ge=2048, description="Size of a generated pairing signing key"
    )
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
        if not self.join_url and self.join_listener is None:
            self.join_listener = HostJoinListenerConfig()
        if self.join_url.startswith("http://") and self.join_listener is not None:
            raise ValueError(
                "compute.hosts.join_url is http, but join_listener serves TLS only; make "
                "join_url https://, or remove join_listener"
            )
        if self.join_url.startswith("http://") and not self.allow_plaintext_join:
            raise ValueError(
                "compute.hosts.join_url is plaintext http; pairing codes would cross the "
                "network in clear. Use https, or set allow_plaintext_join: true"
            )
        ids = [connector.binding_id for connector in self.connectors]
        if len(ids) != len(set(ids)):
            raise ValueError("compute.hosts.connectors binding_id values must be unique")
        return self

    def join_base_url(self, server_public_host: str) -> str:
        """The URL hosts join at; derived from ``server_public_host`` without ``join_url``."""
        if self.join_url:
            return self.join_url
        listener = self.join_listener
        if listener is None:
            raise ValueError("compute.hosts needs join_url or join_listener")
        host = server_public_host.strip()
        remedy = (
            "set server_public_host (NIUU_SERVER_PUBLIC_HOST; server.external_host for "
            "`niuu platform up`) to the address hosts reach this machine at, or set "
            "compute.hosts.join_url"
        )
        if not _JOIN_HOST.match(host):
            raise ValueError(
                f"compute.hosts cannot derive a join URL from server_public_host "
                f"{server_public_host!r}: it must be a hostname or IPv4 address; {remedy}"
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if host == "localhost" or (
            address is not None and (address.is_loopback or address.is_unspecified)
        ):
            raise ValueError(
                f"compute.hosts cannot derive a join URL from server_public_host {host!r}: "
                f"other machines cannot reach it; {remedy}"
            )
        return f"https://{host}:{listener.port}"


class SessionImagesConfig(BaseModel):
    """The release's own session images, which Forge pins by digest at startup.

    Host runtimes that are given no image of their own (the built-in host execution) run
    these. A chart deployment renders them from its own image tag and ``niuu platform up``
    from its build, so a release bump moves them; set them only to run other images.
    """

    model_config = ConfigDict(extra="forbid")

    linux: str = Field(
        default="",
        description=(
            "Skuld image Linux hosts unpack, on a relocatable Python "
            "(ghcr.io/niuulabs/openshell:<tag>); a tag is resolved to its digest at startup"
        ),
    )
    darwin: str = Field(
        default="",
        description=(
            "macOS session bundle Macs fetch (ghcr.io/niuulabs/skuld-darwin:<tag>); a tag is "
            "resolved to its digest at startup"
        ),
    )
    native: list[str] = Field(
        default_factory=list,
        description=(
            "Digest-pinned pod images a Mac runs natively from the session bundle's commands "
            "instead of as containers (OpenBao's agent image, whose bao the bundle carries)"
        ),
    )
    resolve_timeout_seconds: float = Field(
        default=30,
        gt=0,
        description=(
            "One registry request while pinning a tag at startup; bounded by how long a "
            "starting Forge may wait for its registry. A timeout stops startup"
        ),
    )


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
    session_images: SessionImagesConfig | None = Field(
        default=None,
        description="The release's session images for host runtimes given none of their own",
    )
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
