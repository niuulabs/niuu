"""Kubernetes ConfigMap-backed flock flow provider.

Reads and writes flock flow definitions through the Kubernetes API,
storing them in a ConfigMap. Same RBAC approach as the persona ConfigMap.
"""

from __future__ import annotations

import logging

import yaml

from tyr.domain.flock_flow import FlockFlowConfig
from tyr.ports.flock_flow import FlockFlowProvider

logger = logging.getLogger(__name__)

_DATA_KEY = "flows.yaml"


class KubernetesConfigMapFlockFlowProvider(FlockFlowProvider):
    """Read/write flock flows through a Kubernetes ConfigMap."""

    def __init__(
        self,
        namespace: str = "tyr",
        configmap_name: str = "flock-flows",
        kube_client: object | None = None,
    ) -> None:
        self._namespace = namespace
        self._configmap_name = configmap_name
        self._client = kube_client or self._default_client()

    @staticmethod
    def _default_client() -> object:
        """Create a default Kubernetes client (in-cluster config)."""
        try:
            from kubernetes import client, config  # type: ignore[import-untyped]

            config.load_incluster_config()
            return client.CoreV1Api()
        except Exception:
            logger.warning("Failed to create in-cluster k8s client; operations will fail")
            return None

    def _read_configmap(self) -> list[dict]:
        """Read and parse the flows YAML from the ConfigMap."""
        if self._client is None:
            return []
        try:
            cm = self._client.read_namespaced_config_map(
                name=self._configmap_name,
                namespace=self._namespace,
            )
            raw = (cm.data or {}).get(_DATA_KEY, "")
            if not raw:
                return []
            data = yaml.safe_load(raw)
            return data if isinstance(data, list) else []
        except Exception:
            logger.warning(
                "Failed to read ConfigMap %s/%s",
                self._namespace,
                self._configmap_name,
                exc_info=True,
            )
            return []

    def _write_configmap(self, flows: list[dict]) -> None:
        """Write flows YAML back to the ConfigMap.

        Raises on failure so callers know the write did not persist.
        """
        if self._client is None:
            raise RuntimeError("No k8s client available; cannot write ConfigMap")
        body = {"data": {_DATA_KEY: yaml.dump(flows, default_flow_style=False)}}
        self._client.patch_namespaced_config_map(
            name=self._configmap_name,
            namespace=self._namespace,
            body=body,
        )

    def get(self, name: str) -> FlockFlowConfig | None:
        for entry in self._read_configmap():
            if entry.get("name") == name:
                return FlockFlowConfig.from_dict(entry)
        return None

    def list(self) -> list[FlockFlowConfig]:
        result: list[FlockFlowConfig] = []
        for entry in self._read_configmap():
            try:
                result.append(FlockFlowConfig.from_dict(entry))
            except (KeyError, TypeError):
                logger.warning("Skipping invalid flow entry in ConfigMap: %s", entry)
        return result

    def save(self, flow: FlockFlowConfig) -> None:
        entries = self._read_configmap()
        replaced = False
        for i, entry in enumerate(entries):
            if entry.get("name") == flow.name:
                entries[i] = flow.to_dict()
                replaced = True
                break
        if not replaced:
            entries.append(flow.to_dict())
        self._write_configmap(entries)

    def delete(self, name: str) -> bool:
        entries = self._read_configmap()
        new_entries = [e for e in entries if e.get("name") != name]
        if len(new_entries) == len(entries):
            return False
        self._write_configmap(new_entries)
        return True
