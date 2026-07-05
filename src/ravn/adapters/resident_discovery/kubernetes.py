"""Discover standalone residents from Kubernetes deployments.

Residents deployed via the agent Helm chart (labeled
``niuu.world/kind: resident``) are not Forge sessions, so the Forge sessions
API never reports them. This adapter reads their deployments straight from
the Kubernetes API and maps each one to a :class:`StandaloneResident`.
"""

from __future__ import annotations

import logging
from typing import Any

from ravn.adapters.kubernetes_deployments import KubernetesDeploymentDiscovery
from ravn.ports.resident_discovery import StandaloneResident

logger = logging.getLogger(__name__)


class KubernetesResidentDiscoveryAdapter(KubernetesDeploymentDiscovery):
    """Read standalone resident deployments from the Kubernetes API."""

    _discovery_kind = "residents"

    def __init__(
        self,
        namespace: str = "",
        label_selector: str = "",
        required_labels: dict[str, str] | None = None,
        kubeconfig: str = "",
        context: str = "",
        in_cluster: bool | None = None,
        apps_api: Any | None = None,
    ) -> None:
        super().__init__(
            namespace=namespace,
            label_selector=label_selector,
            required_labels=required_labels or {"niuu.world/kind": "resident"},
            kubeconfig=kubeconfig,
            context=context,
            in_cluster=in_cluster,
            apps_api=apps_api,
        )

    async def list_residents(self) -> list[StandaloneResident]:
        """Return standalone residents represented by Kubernetes deployments."""
        items = await self._list_matching_deployments()
        residents = [self._deployment_to_resident(item) for item in items]
        return [resident for resident in residents if resident is not None]

    def _deployment_to_resident(self, deployment: Any) -> StandaloneResident | None:
        metadata = getattr(deployment, "metadata", None)
        name = str(getattr(metadata, "name", "") or "")
        if not name:
            return None

        namespace = str(getattr(metadata, "namespace", "") or self._namespace)
        labels = self._merged_labels(deployment)
        annotations = self._merged_annotations(deployment)
        return StandaloneResident(
            id=name,
            resident_name=self._value(annotations, "niuu.world/resident-name")
            or self._value(labels, "niuu.world/resident-name")
            or name,
            persona_name=self._value(labels, "niuu.world/persona")
            or self._value(annotations, "niuu.world/persona"),
            status=self._resident_status(deployment),
            model=self._value(annotations, "niuu.world/model"),
            chat_endpoint=self._value(annotations, "niuu.world/chat-endpoint") or None,
            location=f"{namespace}/{name}",
            created_at=self._creation_timestamp(metadata),
        )

    def _resident_status(self, deployment: Any) -> str:
        status = getattr(deployment, "status", None)
        ready_replicas = int(getattr(status, "ready_replicas", None) or 0)
        if ready_replicas > 0:
            return "active"
        desired_replicas = getattr(getattr(deployment, "spec", None), "replicas", None)
        if desired_replicas is not None and int(desired_replicas) == 0:
            return "suspended"
        return "idle"

    def _creation_timestamp(self, metadata: Any) -> str | None:
        value = getattr(metadata, "creation_timestamp", None)
        if value is None:
            return None
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            return str(isoformat())
        return str(value) or None
