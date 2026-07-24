"""Principal-aware Agent Directory projection over Observatory discovery."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryFilters,
    AgentDirectoryPage,
    AgentDirectorySourceHealth,
    AgentDirectoryWarning,
    AgentProvenance,
    is_agent_scope_visible,
    matches_agent_filters,
)
from niuu.domain.models import Principal
from niuu.ports.agent_cards import (
    AgentCardResolutionError,
    AgentCardResolverPort,
    ResolvedAgentCard,
)
from observatory.entity_discovery import DiscoveredEntity, DiscoveryResult

_AGENT_KINDS = {"steward", "resident", "workflow-session"}


class DiscoveryResultProvider(Protocol):
    """Port exposing unfiltered discovery source truth to the directory projection."""

    async def get_discovery_result(self) -> DiscoveryResult: ...


def _string_metadata(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _string_list_metadata(metadata: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _stable_agent_id(canonical_id: str) -> str:
    digest = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:24]
    return f"agent-{digest}"


class AgentDirectoryService:
    """Project validated A2A metadata without creating parallel topology nodes."""

    def __init__(
        self,
        *,
        discovery: DiscoveryResultProvider,
        card_resolver: AgentCardResolverPort,
        instance_id: str,
        cluster_id: str,
        max_concurrency: int,
    ) -> None:
        self._discovery = discovery
        self._card_resolver = card_resolver
        self._instance_id = instance_id
        self._cluster_id = cluster_id
        self._max_concurrency = max_concurrency

    async def list_agents(
        self,
        principal: Principal,
        *,
        headers: Mapping[str, str],
        filters: AgentDirectoryFilters | None = None,
    ) -> AgentDirectoryPage:
        """Return visible entries and bounded per-card warnings."""
        active_filters = filters or AgentDirectoryFilters()
        result = await self._discovery.get_discovery_result()
        candidates = [
            entity
            for entity in result.entities
            if entity.endpoints.get("a2aCard") and self._entity_may_match(entity, active_filters)
        ]
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def resolve(
            entity: DiscoveredEntity,
        ) -> tuple[AgentDirectoryEntry | None, AgentDirectoryWarning | None]:
            async with semaphore:
                return await self._project_entity(
                    entity,
                    principal=principal,
                    headers=headers,
                    filters=active_filters,
                )

        resolved = await asyncio.gather(*(resolve(entity) for entity in candidates))
        items = sorted(
            (entry for entry, _ in resolved if entry is not None),
            key=lambda entry: (
                entry.cluster_id.casefold(),
                entry.name.casefold(),
                entry.source_agent_id,
            ),
        )
        warnings = [warning for _, warning in resolved if warning is not None]
        revision = hashlib.sha256(
            "|".join(f"{item.id}:{item.card_hash}" for item in items).encode("utf-8")
        ).hexdigest()
        cluster_id = self._cluster_id or next(
            (entity.cluster for entity in candidates if entity.cluster),
            "",
        )
        return AgentDirectoryPage(
            items=items,
            warnings=warnings,
            sources=[
                AgentDirectorySourceHealth(
                    instanceId=self._instance_id,
                    clusterId=cluster_id,
                    status="degraded" if warnings else "healthy",
                    revision=revision,
                    message=(
                        f"{len(warnings)} Agent Card resolution warning(s)" if warnings else ""
                    ),
                )
            ],
            partial=bool(warnings),
            revision=revision,
        )

    async def get_agent(
        self,
        agent_id: str,
        principal: Principal,
        *,
        headers: Mapping[str, str],
    ) -> AgentDirectoryEntry | None:
        """Resolve a visible detail without disclosing inaccessible identifiers."""
        page = await self.list_agents(principal, headers=headers)
        return next(
            (entry for entry in page.items if entry.id == agent_id),
            None,
        )

    def _entity_may_match(
        self,
        entity: DiscoveredEntity,
        filters: AgentDirectoryFilters,
    ) -> bool:
        metadata = entity.metadata
        kind = self._agent_kind(entity)
        cluster_id = entity.cluster or self._cluster_id
        environment_id = _string_metadata(metadata, "environmentId", "environment_id")
        if filters.kinds and kind.casefold() not in {item.casefold() for item in filters.kinds}:
            return False
        if filters.statuses and entity.status.casefold() not in {
            item.casefold() for item in filters.statuses
        }:
            return False
        if filters.environment_ids and environment_id.casefold() not in {
            item.casefold() for item in filters.environment_ids
        }:
            return False
        if filters.cluster_ids and cluster_id.casefold() not in {
            item.casefold() for item in filters.cluster_ids
        }:
            return False
        if filters.instance_ids and self._instance_id.casefold() not in {
            item.casefold() for item in filters.instance_ids
        }:
            return False
        return True

    async def _project_entity(
        self,
        entity: DiscoveredEntity,
        *,
        principal: Principal,
        headers: Mapping[str, str],
        filters: AgentDirectoryFilters,
    ) -> tuple[AgentDirectoryEntry | None, AgentDirectoryWarning | None]:
        metadata = entity.metadata
        source_agent_id = _string_metadata(metadata, "sessionId") or entity.source_uid or entity.id
        visibility = _string_metadata(metadata, "visibility", "a2aVisibility")
        if not visibility and "volundr-session" in entity.source_kind:
            visibility = "user"
        owner_id = _string_metadata(metadata, "ownerId", "owner_id") or None
        tenant_id = _string_metadata(metadata, "tenantId", "tenant_id") or None
        environment_id = _string_metadata(metadata, "environmentId", "environment_id") or None
        environment_member_ids = _string_list_metadata(
            metadata,
            "environmentMemberIds",
            "environment_member_ids",
        )
        if environment_id and not environment_member_ids and principal.user_id != owner_id:
            return None, None
        if not is_agent_scope_visible(
            owner_id=owner_id,
            tenant_id=tenant_id,
            visibility=visibility,
            environment_member_ids=environment_member_ids,
            principal=principal,
        ):
            return None, None

        card_url = entity.endpoints["a2aCard"]
        principal_key = f"{principal.tenant_id}\0{principal.user_id}"
        try:
            card = await self._card_resolver.resolve(
                card_url,
                principal_key=principal_key,
                headers=headers,
            )
        except AgentCardResolutionError as exc:
            return None, AgentDirectoryWarning(
                sourceInstanceId=self._instance_id,
                sourceAgentId=source_agent_id,
                code="agent-card-unavailable",
                message=str(exc),
            )

        entry = self._entry_from_card(
            entity,
            card=card,
            card_url=card_url,
            source_agent_id=source_agent_id,
            visibility=visibility,
        )
        if not matches_agent_filters(entry, filters):
            return None, None
        return entry, None

    def _entry_from_card(
        self,
        entity: DiscoveredEntity,
        *,
        card: ResolvedAgentCard,
        card_url: str,
        source_agent_id: str,
        visibility: str,
    ) -> AgentDirectoryEntry:
        metadata = entity.metadata
        cluster_id = entity.cluster or self._cluster_id
        environment_id = _string_metadata(metadata, "environmentId", "environment_id") or None
        fingerprint_identity = ",".join(card.signature_key_fingerprints)
        canonical_id = (
            f"a2a-card:{card.card_hash}:keys:{fingerprint_identity}"
            if card.signature_verified and card.card_hash and fingerprint_identity
            else f"a2a-source:{self._instance_id}:{entity.id}"
        )
        provenance = AgentProvenance(
            sourceAgentId=source_agent_id,
            sourceInstanceId=self._instance_id,
            clusterId=cluster_id,
            environmentId=environment_id,
            topologyNodeId=entity.id,
        )
        return AgentDirectoryEntry(
            id=_stable_agent_id(canonical_id),
            canonicalId=canonical_id,
            sourceAgentId=source_agent_id,
            sourceInstanceId=self._instance_id,
            clusterId=cluster_id,
            environmentId=environment_id,
            topologyNodeId=entity.id,
            name=card.name,
            description=card.description,
            kind=self._agent_kind(entity),
            cardUrl=card_url,
            cardVersion=card.version,
            cardHash=card.card_hash,
            signatureVerified=card.signature_verified,
            signatureKeyIds=list(card.signature_key_ids),
            signatureKeyFingerprints=list(card.signature_key_fingerprints),
            skillIds=list(card.skills),
            skills=list(card.skill_details),
            tags=list(card.tags),
            defaultInputModes=list(card.default_input_modes),
            defaultOutputModes=list(card.default_output_modes),
            supportedInterfaces=list(card.supported_interfaces),
            capabilities=card.capabilities,
            securitySchemes=card.security_schemes,
            securityRequirements=list(card.security_requirements),
            observedStatus=entity.status,
            activity=_string_metadata(metadata, "activity", "activityState", "activity_state"),
            lastSeen=_string_metadata(metadata, "lastSeen", "lastActive", "last_active"),
            ownerId=_string_metadata(metadata, "ownerId", "owner_id") or None,
            tenantId=_string_metadata(metadata, "tenantId", "tenant_id") or None,
            visibility=visibility,
            environmentMemberIds=_string_list_metadata(
                metadata,
                "environmentMemberIds",
                "environment_member_ids",
            ),
            provenance=[provenance],
        )

    @staticmethod
    def _agent_kind(entity: DiscoveredEntity) -> str:
        kind = _string_metadata(entity.metadata, "agentKind", "agent_kind")
        if not kind and "volundr-session" in entity.source_kind:
            kind = "workflow-session"
        return kind if kind in _AGENT_KINDS else "workflow-session"
