"""Guild-backed aggregation service for Observatory Agent Directories."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from dataclasses import replace

from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryFilters,
    AgentDirectoryPage,
    AgentDirectorySourceHealth,
    AgentDirectoryWarning,
    AgentProvenance,
    is_agent_visible,
    matches_agent_filters,
)
from niuu.domain.models import Principal, RegisteredInstance
from niuu.ports.agent_directory import AgentDirectoryClientPort


def _aggregate_id(canonical_id: str) -> str:
    digest = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:24]
    return f"agent-{digest}"


def _verified_identity(entry: AgentDirectoryEntry) -> str | None:
    fingerprints = sorted(set(entry.signature_key_fingerprints))
    if not entry.signature_verified or not entry.card_hash or not fingerprints:
        return None
    return f"signed:{entry.card_hash}:keys:{','.join(fingerprints)}"


class AgentDirectoryAggregationService:
    """Fan out to visible Observatory instances and reconcile proven identities."""

    def __init__(
        self,
        *,
        client: AgentDirectoryClientPort,
        max_concurrency: int,
    ) -> None:
        self._client = client
        self._max_concurrency = max_concurrency

    async def list_agents(
        self,
        instances: list[RegisteredInstance],
        principal: Principal,
        *,
        headers: Mapping[str, str],
        filters: AgentDirectoryFilters | None = None,
    ) -> AgentDirectoryPage:
        """Aggregate healthy results while retaining source-scoped failures."""
        active_filters = filters or AgentDirectoryFilters()
        local_filters = replace(active_filters, instance_ids=())
        selected_instances = [
            instance
            for instance in instances
            if not active_filters.instance_ids
            or instance.id.casefold() in {item.casefold() for item in active_filters.instance_ids}
        ]
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def load(
            instance: RegisteredInstance,
        ) -> tuple[RegisteredInstance, AgentDirectoryPage | None, Exception | None]:
            async with semaphore:
                try:
                    page = await self._client.list_agents(
                        instance,
                        headers=headers,
                        filters=local_filters,
                    )
                except Exception as exc:
                    return instance, None, exc
                return instance, page, None

        loaded = await asyncio.gather(*(load(instance) for instance in selected_instances))
        warnings: list[AgentDirectoryWarning] = []
        sources: list[AgentDirectorySourceHealth] = []
        entries: list[AgentDirectoryEntry] = []

        for instance, page, error in loaded:
            cluster_id = str(
                instance.config.get("cluster") or instance.config.get("environment") or ""
            )
            if error is not None or page is None:
                message = (
                    "Observatory Agent Directory request failed"
                    if error is not None
                    else "Observatory returned an empty Agent Directory response"
                )
                warnings.append(
                    AgentDirectoryWarning(
                        sourceInstanceId=instance.id,
                        code="observatory-unavailable",
                        message=message,
                    )
                )
                sources.append(
                    AgentDirectorySourceHealth(
                        instanceId=instance.id,
                        clusterId=cluster_id,
                        status="failed",
                        message=message,
                    )
                )
                continue

            normalized_entries = [
                self._normalize_entry(entry, instance=instance, cluster_id=cluster_id)
                for entry in page.items
            ]
            entries.extend(
                entry
                for entry in normalized_entries
                if is_agent_visible(entry, principal)
                and matches_agent_filters(entry, active_filters)
            )
            warnings.extend(
                warning.model_copy(update={"source_instance_id": instance.id})
                for warning in page.warnings
            )
            sources.extend(
                source.model_copy(
                    update={
                        "instance_id": instance.id,
                        "cluster_id": source.cluster_id or cluster_id,
                        "status": (
                            "degraded"
                            if page.partial and source.status == "healthy"
                            else source.status
                        ),
                    }
                )
                for source in page.sources
            )
            if not page.sources:
                sources.append(
                    AgentDirectorySourceHealth(
                        instanceId=instance.id,
                        clusterId=cluster_id,
                        status="degraded" if page.partial else "healthy",
                        revision=page.revision,
                    )
                )

        merged = self._merge_entries(entries)
        revision = hashlib.sha256(
            "|".join(f"{entry.id}:{entry.card_hash}" for entry in merged).encode("utf-8")
        ).hexdigest()
        return AgentDirectoryPage(
            items=merged,
            warnings=warnings,
            sources=sources,
            partial=bool(warnings) or any(source.status != "healthy" for source in sources),
            revision=revision,
        )

    async def get_agent(
        self,
        agent_id: str,
        instances: list[RegisteredInstance],
        principal: Principal,
        *,
        headers: Mapping[str, str],
    ) -> AgentDirectoryEntry | None:
        """Find one aggregate entry without revealing inaccessible local matches."""
        page = await self.list_agents(instances, principal, headers=headers)
        return next((entry for entry in page.items if entry.id == agent_id), None)

    @staticmethod
    def _normalize_entry(
        entry: AgentDirectoryEntry,
        *,
        instance: RegisteredInstance,
        cluster_id: str,
    ) -> AgentDirectoryEntry:
        resolved_cluster = entry.cluster_id or cluster_id
        provenance = [
            AgentProvenance(
                sourceAgentId=entry.source_agent_id,
                sourceInstanceId=instance.id,
                clusterId=resolved_cluster,
                environmentId=entry.environment_id,
                topologyNodeId=entry.topology_node_id,
            )
        ]
        return entry.model_copy(
            update={
                "source_instance_id": instance.id,
                "cluster_id": resolved_cluster,
                "provenance": provenance,
            }
        )

    @staticmethod
    def _merge_entries(entries: list[AgentDirectoryEntry]) -> list[AgentDirectoryEntry]:
        merged: dict[str, AgentDirectoryEntry] = {}
        for entry in sorted(
            entries,
            key=lambda item: (item.source_instance_id, item.source_agent_id),
        ):
            merge_key = _verified_identity(entry) or (
                f"source:{entry.source_instance_id}:{entry.source_agent_id}"
            )
            current = merged.get(merge_key)
            if current is None:
                merged[merge_key] = entry.model_copy(
                    update={
                        "id": _aggregate_id(merge_key),
                        "canonical_id": merge_key,
                    }
                )
                continue

            provenance_by_source = {
                (item.source_instance_id, item.source_agent_id): item
                for item in [*current.provenance, *entry.provenance]
            }
            merged[merge_key] = current.model_copy(
                update={
                    "provenance": list(provenance_by_source.values()),
                }
            )
        return sorted(
            merged.values(),
            key=lambda entry: (
                entry.cluster_id.casefold(),
                entry.name.casefold(),
                entry.source_instance_id,
            ),
        )
