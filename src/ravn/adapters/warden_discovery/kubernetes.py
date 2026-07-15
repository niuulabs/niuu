"""Discover GitOps-managed wardens from Kubernetes deployments."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ravn.adapters.kubernetes_deployments import (
    KubernetesDeploymentDiscovery,
    _objectify,
)
from ravn.warden.models import (
    WardenFeatures,
    WardenMimirBinding,
    WardenObservation,
    WardenObservedField,
    WardenRuntime,
    WardenScheduleConfig,
    WardenSpec,
    WardenSupervisor,
)

__all__ = ["KubernetesWardenDiscoveryAdapter", "_objectify"]

logger = logging.getLogger(__name__)


class KubernetesWardenDiscoveryAdapter(KubernetesDeploymentDiscovery):
    """Read Warden deployments from the Kubernetes API."""

    _discovery_kind = "wardens"

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
            required_labels=required_labels or {"niuu.world/kind": "warden"},
            kubeconfig=kubeconfig,
            context=context,
            in_cluster=in_cluster,
            apps_api=apps_api,
        )

    async def list_wardens(self) -> list[WardenSpec]:
        """Return wardens represented by Kubernetes deployments."""
        items = await self._list_matching_deployments()
        wardens = [self._deployment_to_warden(item) for item in items]
        return [warden for warden in wardens if warden is not None]

    def _deployment_to_warden(self, deployment: Any) -> WardenSpec | None:
        metadata = getattr(deployment, "metadata", None)
        name = str(getattr(metadata, "name", "") or "")
        namespace = str(getattr(metadata, "namespace", "") or self._namespace)
        if not name:
            return None

        pod_template = getattr(getattr(deployment, "spec", None), "template", None)
        merged_labels = self._merged_labels(deployment)
        merged_annotations = self._merged_annotations(deployment)
        env = self._pod_template_env(pod_template)
        status = getattr(deployment, "status", None)
        replicas = int(getattr(status, "replicas", None) or 0)
        ready_replicas = int(getattr(status, "ready_replicas", None) or 0)
        available_replicas = int(getattr(status, "available_replicas", None) or 0)
        observed_status = self._observed_status(replicas, ready_replicas)
        warden_id = self._value(merged_labels, "niuu.world/warden-id") or name
        mimir_mount = self._value(merged_labels, "niuu.world/mimir-mount")
        mimir_instance = self._value(merged_labels, "niuu.world/mimir-instance")

        return WardenSpec(
            id=warden_id,
            name=self._value(merged_annotations, "niuu.world/warden-name")
            or self._value(merged_labels, "niuu.world/warden-name")
            or warden_id,
            persona=self._value(merged_labels, "niuu.world/warden-persona")
            or env.get("RAVN_PERSONA")
            or "mimir-warden",
            profile=env.get("RAVN_PROFILE", ""),
            model=self._value(merged_annotations, "niuu.world/model")
            or env.get("RAVN_LLM__MODEL", "")
            or "claude-sonnet-4-6",
            deployment="kubernetes",
            deployment_adapter=(
                "ravn.adapters.warden_discovery.kubernetes.KubernetesWardenDiscoveryAdapter"
            ),
            deployment_kwargs={
                "deployment_name": name,
                "namespace": namespace,
                "discovery_source": "kubernetes",
                "label_selector": self._label_selector,
                "required_labels": self._required_labels,
                "ravn_instance": self._value(merged_labels, "niuu.world/ravn-instance"),
                "mimir_instance": mimir_instance,
                "mimir_mount": mimir_mount,
                "image": self._image(pod_template),
            },
            mimir=WardenMimirBinding(
                mount_names=[mimir_mount] if mimir_mount else [],
                write_mount=mimir_mount,
                read_mount_names=[mimir_mount] if mimir_mount else [],
                write_mount_names=[mimir_mount] if mimir_mount else [],
                category_scope=self._csv(
                    self._value(merged_annotations, "niuu.world/category-scope")
                ),
            ),
            features=WardenFeatures(
                dream_cycle_enabled=self._bool_annotation(
                    merged_annotations,
                    "niuu.world/dream-cycle-enabled",
                    default=True,
                ),
                source_trigger_enabled=self._bool_annotation(
                    merged_annotations,
                    "niuu.world/source-trigger-enabled",
                    default=True,
                ),
            ),
            schedules=WardenScheduleConfig(
                dream_cycle_cron_expression=self._value(
                    merged_annotations,
                    "niuu.world/dream-cycle-cron",
                )
                or env.get("RAVN_DREAM_CYCLE__SCHEDULE", "")
                or "*/15 * * * *",
                source_trigger_poll_interval_seconds=int(
                    self._value(
                        merged_annotations,
                        "niuu.world/source-trigger-poll-seconds",
                    )
                    or 60
                ),
            ),
            runtime=WardenRuntime(
                state="active" if ready_replicas > 0 else "offline",
                last_started_at=getattr(status, "available_replicas", None)
                and datetime.now(UTC)
                or None,
            ),
            supervisor=WardenSupervisor(
                installed=True,
                service_label=name,
                observation=WardenObservation(
                    status=observed_status,
                    detail=(f"{ready_replicas}/{replicas} ready replicas in namespace {namespace}"),
                    source="kubernetes",
                    checked_at=datetime.now(UTC),
                    fields=[
                        WardenObservedField(label="namespace", value=namespace),
                        WardenObservedField(label="deployment", value=name),
                        WardenObservedField(label="replicas", value=str(replicas)),
                        WardenObservedField(
                            label="ready_replicas",
                            value=str(ready_replicas),
                        ),
                        WardenObservedField(
                            label="available_replicas",
                            value=str(available_replicas),
                        ),
                    ],
                ),
            ),
            broker={
                "kind": "kubernetes",
                "namespace": namespace,
                "deployment": name,
            },
            autostart=True,
            created_by="gitops",
        )

    def _pod_template_env(self, pod_template: Any) -> dict[str, str]:
        pod_spec = getattr(pod_template, "spec", None)
        containers = getattr(pod_spec, "containers", None) or []
        env: dict[str, str] = {}
        for container in containers:
            for item in getattr(container, "env", None) or []:
                name = str(getattr(item, "name", "") or "")
                value = getattr(item, "value", None)
                if name and value is not None:
                    env[name] = str(value)
        return env

    def _image(self, pod_template: Any) -> str:
        pod_spec = getattr(pod_template, "spec", None)
        containers = getattr(pod_spec, "containers", None) or []
        if not containers:
            return ""
        return str(getattr(containers[0], "image", "") or "")

    def _observed_status(self, replicas: int, ready_replicas: int) -> str:
        if replicas <= 0:
            return "missing"
        if ready_replicas >= replicas:
            return "running"
        if ready_replicas > 0:
            return "degraded"
        return "idle"

    def _csv(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    def _bool_annotation(
        self,
        annotations: dict[str, str],
        key: str,
        *,
        default: bool,
    ) -> bool:
        value = self._value(annotations, key).lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
        return default
