"""Shared contracts for local and Guild-backed A2A agent directories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.models import Principal

AgentKind = Literal["steward", "resident", "workflow-session"]
SourceHealthStatus = Literal["healthy", "degraded", "failed"]


class AgentInterface(BaseModel):
    """One protocol binding declared by an A2A Agent Card."""

    model_config = ConfigDict(populate_by_name=True)

    url: str
    protocol_binding: str = Field(
        serialization_alias="protocolBinding",
        validation_alias="protocolBinding",
    )
    protocol_version: str = Field(
        serialization_alias="protocolVersion",
        validation_alias="protocolVersion",
    )
    tenant: str = ""


class AgentProvenance(BaseModel):
    """Source coordinates retained while aggregating equivalent agents."""

    model_config = ConfigDict(populate_by_name=True)

    source_agent_id: str = Field(
        serialization_alias="sourceAgentId",
        validation_alias="sourceAgentId",
    )
    source_instance_id: str = Field(
        serialization_alias="sourceInstanceId",
        validation_alias="sourceInstanceId",
    )
    cluster_id: str = Field(serialization_alias="clusterId", validation_alias="clusterId")
    environment_id: str | None = Field(
        default=None,
        serialization_alias="environmentId",
        validation_alias="environmentId",
    )
    topology_node_id: str = Field(
        serialization_alias="topologyNodeId",
        validation_alias="topologyNodeId",
    )


class AgentDirectoryEntry(BaseModel):
    """Searchable projection of one addressable A2A agent."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    canonical_id: str = Field(
        serialization_alias="canonicalId",
        validation_alias="canonicalId",
    )
    source_agent_id: str = Field(
        serialization_alias="sourceAgentId",
        validation_alias="sourceAgentId",
    )
    source_instance_id: str = Field(
        serialization_alias="sourceInstanceId",
        validation_alias="sourceInstanceId",
    )
    cluster_id: str = Field(serialization_alias="clusterId", validation_alias="clusterId")
    environment_id: str | None = Field(
        default=None,
        serialization_alias="environmentId",
        validation_alias="environmentId",
    )
    topology_node_id: str = Field(
        serialization_alias="topologyNodeId",
        validation_alias="topologyNodeId",
    )
    name: str
    description: str
    kind: AgentKind
    card_url: str = Field(serialization_alias="cardUrl", validation_alias="cardUrl")
    card_version: str = Field(
        serialization_alias="cardVersion",
        validation_alias="cardVersion",
    )
    card_hash: str = Field(serialization_alias="cardHash", validation_alias="cardHash")
    signature_verified: bool | None = Field(
        default=None,
        serialization_alias="signatureVerified",
        validation_alias="signatureVerified",
    )
    signature_key_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="signatureKeyIds",
        validation_alias="signatureKeyIds",
    )
    skill_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="skillIds",
        validation_alias="skillIds",
    )
    tags: list[str] = Field(default_factory=list)
    default_input_modes: list[str] = Field(
        default_factory=list,
        serialization_alias="defaultInputModes",
        validation_alias="defaultInputModes",
    )
    default_output_modes: list[str] = Field(
        default_factory=list,
        serialization_alias="defaultOutputModes",
        validation_alias="defaultOutputModes",
    )
    supported_interfaces: list[AgentInterface] = Field(
        default_factory=list,
        serialization_alias="supportedInterfaces",
        validation_alias="supportedInterfaces",
    )
    capabilities: dict[str, Any] = Field(default_factory=dict)
    observed_status: str = Field(
        serialization_alias="observedStatus",
        validation_alias="observedStatus",
    )
    activity: str = ""
    last_seen: str = Field(
        default="",
        serialization_alias="lastSeen",
        validation_alias="lastSeen",
    )
    owner_id: str | None = Field(
        default=None,
        serialization_alias="ownerId",
        validation_alias="ownerId",
    )
    tenant_id: str | None = Field(
        default=None,
        serialization_alias="tenantId",
        validation_alias="tenantId",
    )
    visibility: str
    environment_member_ids: list[str] = Field(
        default_factory=list,
        exclude=True,
        validation_alias="environmentMemberIds",
    )
    provenance: list[AgentProvenance] = Field(default_factory=list)


class AgentDirectoryWarning(BaseModel):
    """A source-scoped problem that did not invalidate the whole response."""

    model_config = ConfigDict(populate_by_name=True)

    source_instance_id: str = Field(
        serialization_alias="sourceInstanceId",
        validation_alias="sourceInstanceId",
    )
    code: str
    message: str
    source_agent_id: str | None = Field(
        default=None,
        serialization_alias="sourceAgentId",
        validation_alias="sourceAgentId",
    )


class AgentDirectorySourceHealth(BaseModel):
    """Per-Observatory health included with partial aggregate results."""

    model_config = ConfigDict(populate_by_name=True)

    instance_id: str = Field(serialization_alias="instanceId", validation_alias="instanceId")
    cluster_id: str = Field(serialization_alias="clusterId", validation_alias="clusterId")
    status: SourceHealthStatus
    revision: str = ""
    message: str = ""


class AgentDirectoryPage(BaseModel):
    """List response shared by local Observatory and Guild aggregation."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[AgentDirectoryEntry] = Field(default_factory=list)
    warnings: list[AgentDirectoryWarning] = Field(default_factory=list)
    sources: list[AgentDirectorySourceHealth] = Field(default_factory=list)
    partial: bool = False
    revision: str = ""


@dataclass(frozen=True)
class AgentDirectoryFilters:
    """Transport-neutral directory filters."""

    skills: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    environment_ids: tuple[str, ...] = ()
    cluster_ids: tuple[str, ...] = ()
    instance_ids: tuple[str, ...] = ()


def is_agent_visible(entry: AgentDirectoryEntry, principal: Principal) -> bool:
    """Apply owner, tenant, Environment membership, and explicit visibility."""
    if entry.tenant_id and entry.tenant_id != principal.tenant_id:
        return False
    if entry.environment_member_ids and principal.user_id not in entry.environment_member_ids:
        return False

    visibility = entry.visibility.strip().lower()
    if visibility in {"system", "public"}:
        return True
    if visibility == "tenant":
        return bool(entry.tenant_id) and entry.tenant_id == principal.tenant_id
    if visibility in {"user", "private"}:
        return bool(entry.owner_id) and entry.owner_id == principal.user_id
    return False


def matches_agent_filters(
    entry: AgentDirectoryEntry,
    filters: AgentDirectoryFilters,
) -> bool:
    """Apply all supported local/aggregate directory filters consistently."""
    normalized_skills = {item.casefold() for item in entry.skill_ids}
    if filters.skills and not all(item.casefold() in normalized_skills for item in filters.skills):
        return False

    normalized_tags = {item.casefold() for item in entry.tags}
    if filters.tags and not all(item.casefold() in normalized_tags for item in filters.tags):
        return False

    if filters.kinds and entry.kind.casefold() not in {item.casefold() for item in filters.kinds}:
        return False
    if filters.statuses and entry.observed_status.casefold() not in {
        item.casefold() for item in filters.statuses
    }:
        return False
    if filters.environment_ids and (entry.environment_id or "").casefold() not in {
        item.casefold() for item in filters.environment_ids
    }:
        return False
    if filters.cluster_ids and entry.cluster_id.casefold() not in {
        item.casefold() for item in filters.cluster_ids
    }:
        return False
    if filters.instance_ids and entry.source_instance_id.casefold() not in {
        item.casefold() for item in filters.instance_ids
    }:
        return False
    return True
