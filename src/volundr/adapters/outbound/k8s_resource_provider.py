"""Kubernetes resource provider — discovers cluster resources via K8s API."""

import logging
from collections import defaultdict
from inspect import isawaitable

from volundr.domain.models import (
    ClusterResourceInfo,
    NodeResourceSummary,
    ResourceCategory,
    ResourceType,
    TranslatedResources,
)
from volundr.domain.ports import ResourceProvider

from .static_resource_provider import (
    STANDARD_RESOURCE_TYPES,
    translate_resource_config,
    validate_resource_config,
)

logger = logging.getLogger(__name__)

# Mapping of well-known K8s resource keys to display metadata
_WELL_KNOWN_RESOURCES: dict[str, tuple[str, str, ResourceCategory]] = {
    "ephemeral-storage": ("Ephemeral Storage", "bytes", ResourceCategory.COMPUTE),
    "pods": ("Pods", "pods", ResourceCategory.COMPUTE),
}

_MEMORY_MULTIPLIERS = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
}


def _add_resource(total: dict[str, int], key: str, value: object) -> None:
    raw = str(value)
    if key == "cpu":
        total[key] += int(float(raw[:-1]) if raw.endswith("m") else float(raw) * 1000)
        return
    if key == "memory":
        for suffix, multiplier in _MEMORY_MULTIPLIERS.items():
            if raw.endswith(suffix):
                total[key] += int(float(raw[: -len(suffix)]) * multiplier)
                return
        total[key] += int(float(raw))
        return
    total[key] += int(float(raw))


def _format_allocated(values: dict[str, int]) -> dict[str, str]:
    formatted = {key: str(value) for key, value in values.items() if key not in {"cpu", "memory"}}
    if values.get("cpu"):
        formatted["cpu"] = f"{values['cpu']}m"
    if values.get("memory"):
        formatted["memory"] = f"{values['memory'] // (1024**2)}Mi"
    return formatted


async def _maybe_await(value: object) -> None:
    if isawaitable(value):
        await value


def _resource_type_from_key(key: str) -> ResourceType:
    """Build a ResourceType from an allocatable key."""
    if key in _WELL_KNOWN_RESOURCES:
        display, unit, category = _WELL_KNOWN_RESOURCES[key]
        return ResourceType(
            name=key.replace("/", "_").replace(".", "_"),
            resource_key=key,
            display_name=display,
            unit=unit,
            category=category,
        )

    # Vendor-prefixed resources (e.g. "nvidia.com/gpu") → accelerator
    if "/" in key:
        category = ResourceCategory.ACCELERATOR
    else:
        category = ResourceCategory.COMPUTE

    # Derive a human-readable display name from the key
    short = key.rsplit("/", 1)[-1]
    display_name = short.replace("-", " ").replace("_", " ").title()

    # Guess unit from name
    if "storage" in key or "memory" in key:
        unit = "bytes"
    elif "gpu" in key:
        unit = "devices"
    else:
        unit = ""

    return ResourceType(
        name=key.replace("/", "_").replace(".", "_"),
        resource_key=key,
        display_name=display_name,
        unit=unit,
        category=category,
    )


class K8sResourceProvider(ResourceProvider):
    """Resource provider that queries the Kubernetes API for node capacity.

    Uses the device-plugin model (v1.30). The interface is designed so a
    DRA-backed adapter can be swapped in for v1.31+ without changing callers.
    """

    def __init__(
        self, *, namespace: str = "volundr-sessions", kubeconfig: str = "", **_extra: object
    ) -> None:
        self._namespace = namespace
        self._kubeconfig = kubeconfig

    async def discover(self) -> ClusterResourceInfo:
        """List nodes, extract GPU labels/allocatable, build ClusterResourceInfo."""
        try:
            from kubernetes_asyncio import client, config

            if self._kubeconfig:
                await _maybe_await(config.load_kube_config(config_file=self._kubeconfig))
            else:
                try:
                    await _maybe_await(config.load_incluster_config())
                except config.ConfigException:
                    await _maybe_await(config.load_kube_config())
            v1 = client.CoreV1Api()
            nodes_resp = await v1.list_node()
        except Exception:
            logger.warning(
                "Failed to query K8s API for node resources, falling back to static types",
                exc_info=True,
            )
            return ClusterResourceInfo(
                resource_types=list(STANDARD_RESOURCE_TYPES),
                nodes=[],
            )

        allocated_by_node = await self._allocated_by_node(v1)
        seen_gpu_products: set[str] = set()
        seen_resource_keys: set[str] = set()
        nodes: list[NodeResourceSummary] = []

        for node in nodes_resp.items:
            labels = node.metadata.labels or {}
            allocatable = {k: str(v) for k, v in (node.status.allocatable or {}).items()}

            # Track all resource keys across nodes
            seen_resource_keys.update(allocatable.keys())

            # Discover GPU product types from node labels
            gpu_product = labels.get("nvidia.com/gpu.product")
            if gpu_product and gpu_product not in seen_gpu_products:
                seen_gpu_products.add(gpu_product)

            nodes.append(
                NodeResourceSummary(
                    name=node.metadata.name,
                    labels=labels,
                    allocatable=allocatable,
                    allocated=allocated_by_node.get(node.metadata.name, {}),
                    available=allocatable,  # Approximate
                )
            )

        # Build resource types dynamically from what nodes actually report
        resource_types = list(STANDARD_RESOURCE_TYPES)
        standard_keys = {rt.resource_key for rt in STANDARD_RESOURCE_TYPES}

        # Add discovered GPU product variants
        for product in sorted(seen_gpu_products):
            resource_types.append(
                ResourceType(
                    name=f"gpu_{product.lower().replace(' ', '_')}",
                    resource_key="nvidia.com/gpu",
                    display_name=f"GPU ({product})",
                    unit="devices",
                    category=ResourceCategory.ACCELERATOR,
                )
            )

        # Add any node-reported resources not covered by standard types
        # Skip keys that are not useful as requestable session resources
        ignored_keys = {"pods"}
        for key in sorted(seen_resource_keys - standard_keys - ignored_keys):
            # Skip hugepages with zero value across all nodes
            if key.startswith("hugepages-"):
                all_zero = all((int(node.allocatable.get(key, "0") or "0") == 0) for node in nodes)
                if all_zero:
                    continue

            resource_types.append(_resource_type_from_key(key))

        return ClusterResourceInfo(resource_types=resource_types, nodes=nodes)

    async def _allocated_by_node(self, v1: object) -> dict[str, dict[str, str]]:
        try:
            pods_resp = await v1.list_namespaced_pod(namespace=self._namespace)
        except Exception:
            logger.warning("Failed to query K8s API for pod resource requests", exc_info=True)
            return {}

        totals: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
        for pod in pods_resp.items:
            if pod.status.phase in {"Failed", "Succeeded"} or not pod.spec.node_name:
                continue
            for container in pod.spec.containers or []:
                for key, value in (container.resources.requests or {}).items():
                    try:
                        _add_resource(totals[pod.spec.node_name], key, value)
                    except (TypeError, ValueError):
                        logger.debug("Ignoring unparsable pod request %s=%r", key, value)

        return {node: _format_allocated(values) for node, values in totals.items()}

    def translate(self, resource_config: dict) -> TranslatedResources:
        return translate_resource_config(resource_config)

    def validate(
        self,
        resource_config: dict,
        cluster_info: ClusterResourceInfo | None = None,
    ) -> list[str]:
        errors = validate_resource_config(resource_config)

        if not cluster_info or not resource_config.get("gpu"):
            return errors

        # Check GPU availability across nodes
        gpu_count = int(resource_config.get("gpu", 0))
        gpu_type = resource_config.get("gpu_type")

        any_gpu_node = False
        for node in cluster_info.nodes:
            node_gpus = int(node.allocatable.get("nvidia.com/gpu", "0"))
            if node_gpus == 0:
                continue
            any_gpu_node = True
            if gpu_type:
                node_product = node.labels.get("nvidia.com/gpu.product", "")
                if node_product != gpu_type:
                    continue
            if node_gpus >= gpu_count:
                return errors  # Found a suitable node

        if not any_gpu_node:
            errors.append("no GPU nodes available in the cluster")
        elif gpu_type:
            errors.append(f"no nodes with GPU type '{gpu_type}' have {gpu_count} available GPUs")
        else:
            errors.append(f"no single node has {gpu_count} available GPUs")

        return errors
