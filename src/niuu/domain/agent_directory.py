"""Shared contracts for local and Guild-backed A2A agent directories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from niuu.domain.models import Principal

AgentKind = Literal["steward", "resident", "workflow-session"]
SourceHealthStatus = Literal["healthy", "degraded", "failed"]


class _AgentDirectoryModel(BaseModel):
    """Directory model with one consistent snake_case/camelCase boundary."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AgentInterface(_AgentDirectoryModel):
    """One protocol binding declared by an A2A Agent Card."""

    url: str
    protocol_binding: str
    protocol_version: str
    tenant: str = ""


class AgentSkill(_AgentDirectoryModel):
    """One callable skill published by an A2A Agent Card."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=list)
    output_modes: list[str] = Field(default_factory=list)
    security_requirements: list[dict[str, Any]] = Field(default_factory=list)


class AgentProvenance(_AgentDirectoryModel):
    """Source coordinates retained while aggregating equivalent agents."""

    source_agent_id: str
    source_instance_id: str
    cluster_id: str
    environment_id: str | None = None
    topology_node_id: str


class AgentDirectoryEntry(_AgentDirectoryModel):
    """Searchable projection of one addressable A2A agent."""

    id: str
    canonical_id: str
    source_agent_id: str
    source_instance_id: str
    cluster_id: str
    environment_id: str | None = None
    topology_node_id: str
    name: str
    description: str
    kind: AgentKind
    card_url: str
    card_version: str
    card_hash: str
    signature_verified: bool | None = None
    signature_key_ids: list[str] = Field(default_factory=list)
    signature_key_fingerprints: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    skills: list[AgentSkill] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    default_input_modes: list[str] = Field(default_factory=list)
    default_output_modes: list[str] = Field(default_factory=list)
    supported_interfaces: list[AgentInterface] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    security_schemes: dict[str, Any] = Field(default_factory=dict)
    security_requirements: list[dict[str, Any]] = Field(default_factory=list)
    observed_status: str
    activity: str = ""
    last_seen: str = ""
    owner_id: str | None = None
    tenant_id: str | None = None
    visibility: str
    environment_member_ids: list[str] = Field(
        default_factory=list,
        exclude=True,
    )
    provenance: list[AgentProvenance] = Field(default_factory=list)


class AgentDirectoryWarning(_AgentDirectoryModel):
    """A source-scoped problem that did not invalidate the whole response."""

    source_instance_id: str
    code: str
    message: str
    source_agent_id: str | None = None


class AgentDirectorySourceHealth(_AgentDirectoryModel):
    """Per-Observatory health included with partial aggregate results."""

    instance_id: str
    cluster_id: str
    status: SourceHealthStatus
    revision: str = ""
    message: str = ""


class AgentDirectoryPage(_AgentDirectoryModel):
    """List response shared by local Observatory and Guild aggregation."""

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

    @classmethod
    def from_values(
        cls,
        *,
        skills: list[str] | None = None,
        tags: list[str] | None = None,
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
        environment_ids: list[str] | None = None,
        cluster_ids: list[str] | None = None,
        instance_ids: list[str] | None = None,
    ) -> AgentDirectoryFilters:
        """Build immutable filters from transport-level optional lists."""
        return cls(
            skills=tuple(skills or ()),
            tags=tuple(tags or ()),
            kinds=tuple(kinds or ()),
            statuses=tuple(statuses or ()),
            environment_ids=tuple(environment_ids or ()),
            cluster_ids=tuple(cluster_ids or ()),
            instance_ids=tuple(instance_ids or ()),
        )


def is_agent_visible(entry: AgentDirectoryEntry, principal: Principal) -> bool:
    """Apply owner, tenant, Environment membership, and explicit visibility."""
    return is_agent_scope_visible(
        owner_id=entry.owner_id,
        tenant_id=entry.tenant_id,
        visibility=entry.visibility,
        environment_member_ids=entry.environment_member_ids,
        principal=principal,
    )


def is_agent_scope_visible(
    *,
    owner_id: str | None,
    tenant_id: str | None,
    visibility: str,
    environment_member_ids: list[str],
    principal: Principal,
) -> bool:
    """Evaluate visibility without constructing a complete directory entry."""
    if tenant_id and tenant_id != principal.tenant_id:
        return False
    if environment_member_ids and principal.user_id not in environment_member_ids:
        return False

    normalized_visibility = visibility.strip().lower()
    if normalized_visibility in {"system", "public"}:
        return True
    if normalized_visibility == "tenant":
        return bool(tenant_id) and tenant_id == principal.tenant_id
    if normalized_visibility in {"user", "private"}:
        return bool(owner_id) and owner_id == principal.user_id
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
