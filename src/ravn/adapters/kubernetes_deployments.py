"""Shared Kubernetes deployment-listing plumbing for discovery adapters.

Both warden discovery and standalone-resident discovery read labeled
Deployments from the Kubernetes API. This module owns the low-level listing
mechanics — client construction, the in-cluster REST fallback when no
``kubernetes`` client library is installed, and label matching — so each
discovery adapter only maps deployments to its own domain model.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SERVICE_ACCOUNT_ROOT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
_INCLUSTER_REQUEST_TIMEOUT_SECONDS = 10.0


class KubernetesDeploymentDiscovery:
    """List Kubernetes deployments matching a set of required labels."""

    # Human word used in discovery log messages ("wardens", "residents", ...).
    _discovery_kind = "deployments"

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
        self._namespace = namespace
        self._label_selector = label_selector
        self._required_labels = required_labels or {}
        self._kubeconfig = kubeconfig
        self._context = context
        self._in_cluster = in_cluster
        self._apps_api = apps_api

    async def _list_matching_deployments(self) -> list[Any]:
        """Return deployments visible to this adapter that match required labels."""
        apps_api = self._apps_api or self._build_apps_api()
        if apps_api is None:
            items = await self._list_incluster_deployments()
        else:
            try:
                response = self._list_deployments(apps_api)
            except Exception as exc:  # pragma: no cover - depends on cluster client
                logger.warning(
                    "Unable to discover Kubernetes %s: %s",
                    self._discovery_kind,
                    exc,
                )
                return []
            items = getattr(response, "items", response if isinstance(response, list) else [])

        return [item for item in items if self._deployment_matches(item)]

    def _build_apps_api(self) -> Any | None:
        try:
            from kubernetes import client, config  # noqa: PLC0415
        except ImportError:
            return None

        try:
            self._load_config(config)
        except Exception as exc:  # pragma: no cover - depends on local cluster config
            logger.warning("Unable to configure Kubernetes client: %s", exc)
            return None
        return client.AppsV1Api()

    async def _list_incluster_deployments(self) -> list[Any]:
        token_path = _SERVICE_ACCOUNT_ROOT / "token"
        ca_path = _SERVICE_ACCOUNT_ROOT / "ca.crt"
        if not token_path.exists():
            logger.info("Kubernetes service-account token is not mounted")
            return []

        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if self._namespace:
            path = f"/apis/apps/v1/namespaces/{self._namespace}/deployments"
        else:
            path = "/apis/apps/v1/deployments"
        url = f"https://{host}:{port}{path}"
        params = {"labelSelector": self._label_selector} if self._label_selector else None
        headers = {"Authorization": f"Bearer {token_path.read_text(encoding='utf-8').strip()}"}

        try:
            async with httpx.AsyncClient(
                timeout=_INCLUSTER_REQUEST_TIMEOUT_SECONDS,
                verify=str(ca_path) if ca_path.exists() else True,
            ) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning(
                "Unable to discover Kubernetes %s through REST API: %s",
                self._discovery_kind,
                exc,
            )
            return []
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [_objectify(item) for item in items if isinstance(item, dict)]

    def _load_config(self, config: Any) -> None:
        if self._in_cluster is True:
            config.load_incluster_config()
            return
        if self._in_cluster is False:
            config.load_kube_config(
                config_file=self._kubeconfig or None,
                context=self._context or None,
            )
            return
        try:
            config.load_incluster_config()
            return
        except Exception:
            config.load_kube_config(
                config_file=self._kubeconfig or None,
                context=self._context or None,
            )

    def _list_deployments(self, apps_api: Any) -> Any:
        if self._namespace:
            return apps_api.list_namespaced_deployment(
                namespace=self._namespace,
                label_selector=self._label_selector,
            )
        return apps_api.list_deployment_for_all_namespaces(
            label_selector=self._label_selector,
        )

    def _deployment_matches(self, deployment: Any) -> bool:
        labels = self._merged_labels(deployment)
        return all(labels.get(key) == value for key, value in self._required_labels.items())

    def _merged_labels(self, deployment: Any) -> dict[str, str]:
        """Deployment labels layered over pod-template labels."""
        metadata = getattr(deployment, "metadata", None)
        labels = getattr(metadata, "labels", None) or {}
        pod_template = getattr(getattr(deployment, "spec", None), "template", None)
        pod_labels = getattr(getattr(pod_template, "metadata", None), "labels", None) or {}
        return {**pod_labels, **labels}

    def _merged_annotations(self, deployment: Any) -> dict[str, str]:
        """Deployment annotations layered over pod-template annotations."""
        metadata = getattr(deployment, "metadata", None)
        annotations = getattr(metadata, "annotations", None) or {}
        pod_template = getattr(getattr(deployment, "spec", None), "template", None)
        pod_annotations = (
            getattr(getattr(pod_template, "metadata", None), "annotations", None) or {}
        )
        return {**pod_annotations, **annotations}

    def _value(self, data: dict[str, str], key: str) -> str:
        return str(data.get(key) or "").strip()


def _objectify(value: Any) -> Any:
    if isinstance(value, list):
        return [_objectify(item) for item in value]
    if isinstance(value, dict):
        if _is_label_map(value):
            return value
        return SimpleNamespace(
            **{_snake_key(str(key)): _objectify(item) for key, item in value.items()}
        )
    return value


def _is_label_map(value: dict[Any, Any]) -> bool:
    return all(not isinstance(item, (dict, list)) for item in value.values()) and any(
        "/" in str(key) or "." in str(key) for key in value
    )


def _snake_key(value: str) -> str:
    return _CAMEL_RE.sub("_", value).lower()
