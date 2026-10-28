"""Read-only Kubernetes inspection tools for resident Valkyries."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ravn.adapters.kubernetes_pod_logs import read_pod_log
from ravn.cap_telemetry import record_cap_hit, record_truncation
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort


@dataclass(frozen=True)
class KubernetesClients:
    """The read-only API groups the tool uses, bound to one cluster.

    ``aclose`` closes the ``kubernetes_asyncio`` ``ApiClient`` (and the aiohttp
    session it owns) behind these API groups. It is set by whoever created that
    client; the tool awaits it once the call that opened the clients is done.
    ``None`` means the clients are owned by the caller (injected), not the tool.
    """

    core_v1: Any
    apps_v1: Any
    batch_v1: Any
    aclose: Callable[[], Awaitable[None]] | None = None


#: Builds the clients for one kubeconfig context (tests inject their own).
ClientFactory = Callable[[str, str], Awaitable[KubernetesClients]]


class KubernetesInspectTool(ToolPort):
    """Inspect Kubernetes objects with the resident service account.

    This intentionally exposes only read-only calls. It gives k8s Valkyries a
    real container-safe inspection capability without depending on a kubectl
    binary in the image.

    With a kubeconfig that names several clusters (a Teleport Machine ID
    kubeconfig, for example) every call names its ``cluster``; the tool never
    guesses one. What the tool may read on each cluster is whatever that
    identity's RBAC grants, so read-only stays enforced by the cluster too.
    """

    def __init__(
        self,
        *,
        in_cluster: bool = True,
        kubeconfig_path: str = "",
        kubeconfig_env: str = "KUBECONFIG",
        core_v1: Any | None = None,
        apps_v1: Any | None = None,
        batch_v1: Any | None = None,
        max_log_lines: int = 120,
        max_configmap_value_chars: int = 4000,
        max_list_items: int = 100,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._in_cluster = in_cluster
        self._kubeconfig_path = kubeconfig_path
        self._kubeconfig_env = kubeconfig_env
        self._injected = (
            KubernetesClients(core_v1=core_v1, apps_v1=apps_v1, batch_v1=batch_v1)
            if core_v1 is not None
            else None
        )
        self._max_log_lines = max(1, max_log_lines)
        self._max_configmap_value_chars = max(1, max_configmap_value_chars)
        self._max_list_items = max(1, max_list_items)
        self._client_factory = client_factory or _clients_for_context

    @property
    def name(self) -> str:
        return "kubernetes_inspect"

    @property
    def description(self) -> str:
        return (
            "Read-only Kubernetes inspection for resident Valkyries. Supports "
            "pod status, object events, pod logs, workload status (images, rollout "
            "progress), ConfigMap contents, and node status using the in-cluster "
            "service account or a kubeconfig. When the kubeconfig spans several "
            "clusters, `clusters` lists them and every other action names one."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "clusters",
                        "pods",
                        "inspect_pod",
                        "object_events",
                        "pod_logs",
                        "workload",
                        "configmap",
                        "node",
                    ],
                    "description": "Read-only inspection action to perform.",
                },
                "cluster": {
                    "type": "string",
                    "description": (
                        "Cluster to inspect, as listed by the `clusters` action. "
                        "Required when the kubeconfig spans more than one cluster."
                    ),
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace for namespaced objects.",
                },
                "kind": {
                    "type": "string",
                    "description": "Object kind for events/workloads.",
                },
                "label_selector": {
                    "type": "string",
                    "description": (
                        "pods only: Kubernetes label selector, e.g. "
                        "'app.kubernetes.io/instance=regin'."
                    ),
                },
                "name_prefix": {
                    "type": "string",
                    "description": "pods only: keep pods whose name starts with this.",
                },
                "name": {
                    "type": "string",
                    "description": "Object name.",
                },
                "container": {
                    "type": "string",
                    "description": "Optional pod container for logs.",
                },
                "tail_lines": {
                    "type": "integer",
                    "description": "Maximum pod log lines to return.",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return "kubernetes:read"

    @property
    def parallelisable(self) -> bool:
        return True

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        action = str(input.get("action") or "").strip()
        try:
            if action == "clusters":
                return ToolResult(
                    tool_call_id="",
                    content=json.dumps(
                        {"action": "clusters", "clusters": self._clusters()},
                        indent=2,
                        sort_keys=True,
                    ),
                )
            handler = self._handlers().get(action)
            if handler is None:
                return ToolResult(
                    tool_call_id="",
                    content=f"Error: unsupported kubernetes_inspect action {action!r}.",
                    is_error=True,
                )
            clients = await self._open_clients(str(input.get("cluster") or "").strip())
            try:
                payload = await handler(clients, input)
            finally:
                # The ApiClient owns an aiohttp session; leaving it to the
                # garbage collector logs "Unclosed client session" per call.
                if clients.aclose is not None:
                    await clients.aclose()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id="",
                content=f"kubernetes_inspect failed: {exc}",
                is_error=True,
            )
        return ToolResult(tool_call_id="", content=json.dumps(payload, indent=2, sort_keys=True))

    def _handlers(self) -> dict[str, Callable[[KubernetesClients, dict], Awaitable[dict]]]:
        return {
            "pods": self._pods,
            "inspect_pod": self._inspect_pod,
            "object_events": self._object_events,
            "pod_logs": self._pod_logs,
            "workload": self._workload,
            "configmap": self._configmap,
            "node": self._node,
        }

    def _kubeconfig(self) -> str:
        return self._kubeconfig_path or os.environ.get(self._kubeconfig_env, "")

    def _contexts(self) -> list[str]:
        kubeconfig = self._kubeconfig()
        if not kubeconfig:
            raise RuntimeError(
                "kubernetes_inspect has no kubeconfig: set tools.kubernetes.kubeconfig_path "
                f"or {self._kubeconfig_env}, or use in_cluster"
            )
        document = yaml.safe_load(Path(kubeconfig).read_text(encoding="utf-8")) or {}
        return sorted(str(item["name"]) for item in document.get("contexts") or [])

    def _clusters(self) -> list[str]:
        if self._in_cluster or self._injected is not None:
            return ["(this cluster)"]
        return self._contexts()

    def _resolve_context(self, cluster: str) -> str:
        contexts = self._contexts()
        if not cluster:
            if len(contexts) == 1:
                return contexts[0]
            raise ValueError(f"cluster is required; available clusters: {', '.join(contexts)}")
        matches = [
            context for context in contexts if context == cluster or context.endswith(f"-{cluster}")
        ]
        if len(matches) != 1:
            raise ValueError(
                f"unknown cluster {cluster!r}; available clusters: {', '.join(contexts)}"
            )
        return matches[0]

    async def _open_clients(self, cluster: str) -> KubernetesClients:
        """Open the clients for one call; the caller closes them via ``aclose``."""
        if self._injected is not None or self._in_cluster:
            if cluster:
                raise ValueError(
                    "this resident inspects only its own cluster; cluster must be empty"
                )
            if self._injected is not None:
                return self._injected
            return _in_cluster_clients()
        return await self._client_factory(self._kubeconfig(), self._resolve_context(cluster))

    async def _pods(
        self,
        clients: KubernetesClients,
        input: dict,  # noqa: A002
    ) -> dict[str, Any]:
        namespace = str(input.get("namespace") or "").strip()
        if not namespace:
            raise ValueError("namespace is required for pods")
        selector = str(input.get("label_selector") or "").strip()
        prefix = str(input.get("name_prefix") or "").strip()
        kwargs: dict[str, Any] = {"label_selector": selector} if selector else {}
        listed = await _maybe_await(clients.core_v1.list_namespaced_pod(namespace, **kwargs))
        pods = [
            pod
            for pod in list(getattr(listed, "items", listed) or [])
            if _field(_field(pod, "metadata", default={}), "name", default="").startswith(prefix)
        ]
        shown = pods[: self._max_list_items]
        if len(pods) > len(shown):
            record_cap_hit(
                "tools.kubernetes.max_list_items",
                f"kubernetes_inspect listed {len(shown)} of {len(pods)} pods in {namespace}; "
                "raise tools.kubernetes.max_list_items, or narrow with a selector or prefix",
            )
        return {
            "action": "pods",
            "namespace": namespace,
            "label_selector": selector,
            "name_prefix": prefix,
            "total": len(pods),
            "truncated": len(pods) > len(shown),
            "pods": [_pod_summary(pod) for pod in shown],
        }

    async def _inspect_pod(
        self,
        clients: KubernetesClients,
        input: dict,  # noqa: A002
    ) -> dict[str, Any]:
        namespace, name = _namespace_name(input)
        pod = await _maybe_await(clients.core_v1.read_namespaced_pod(name, namespace))
        return {"action": "inspect_pod", "namespace": namespace, "name": name, "pod": _pod(pod)}

    async def _object_events(
        self,
        clients: KubernetesClients,
        input: dict,  # noqa: A002
    ) -> dict[str, Any]:
        namespace, name = _namespace_name(input)
        kind = str(input.get("kind") or "Pod").strip() or "Pod"
        selector = f"involvedObject.name={name},involvedObject.kind={kind}"
        events = await _maybe_await(
            clients.core_v1.list_namespaced_event(namespace, field_selector=selector)
        )
        return {
            "action": "object_events",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "events": [_event(item) for item in list(getattr(events, "items", events) or [])],
        }

    async def _pod_logs(
        self,
        clients: KubernetesClients,
        input: dict,  # noqa: A002
    ) -> dict[str, Any]:
        namespace, name = _namespace_name(input)
        tail_lines = int(input.get("tail_lines") or self._max_log_lines)
        tail_lines = max(1, min(tail_lines, self._max_log_lines))
        kwargs: dict[str, Any] = {"tail_lines": tail_lines}
        container = str(input.get("container") or "").strip()
        if container:
            kwargs["container"] = container
        logs = await read_pod_log(clients.core_v1, name, namespace, **kwargs)
        return {
            "action": "pod_logs",
            "namespace": namespace,
            "name": name,
            "container": container,
            "tail_lines": tail_lines,
            "logs": logs,
        }

    async def _workload(
        self,
        clients: KubernetesClients,
        input: dict,  # noqa: A002
    ) -> dict[str, Any]:
        namespace, name = _namespace_name(input)
        kind = str(input.get("kind") or "Deployment").strip()
        method = _workload_reader(clients.apps_v1, clients.batch_v1, kind)
        obj = await _maybe_await(method(name, namespace))
        return {
            "action": "workload",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "workload": _workload(obj),
        }

    async def _configmap(
        self,
        clients: KubernetesClients,
        input: dict,  # noqa: A002
    ) -> dict[str, Any]:
        namespace, name = _namespace_name(input)
        configmap = await _maybe_await(clients.core_v1.read_namespaced_config_map(name, namespace))
        data = dict(_field(configmap, "data", default={}) or {})
        limit = self._max_configmap_value_chars
        for key, value in data.items():
            if len(str(value)) > limit:
                record_truncation(
                    "tools.kubernetes.max_configmap_value_chars",
                    what=f"configmap {namespace}/{name} key {key!r}",
                    kept=limit,
                    total=len(str(value)),
                )
        return {
            "action": "configmap",
            "namespace": namespace,
            "name": name,
            "metadata": _metadata(configmap),
            "data": {key: str(value)[:limit] for key, value in sorted(data.items())},
            "truncated_keys": sorted(key for key, value in data.items() if len(str(value)) > limit),
        }

    async def _node(
        self,
        clients: KubernetesClients,
        input: dict,  # noqa: A002
    ) -> dict[str, Any]:
        name = str(input.get("name") or "").strip()
        if not name:
            raise ValueError("name is required for node")
        node = await _maybe_await(clients.core_v1.read_node(name))
        return {"action": "node", "name": name, "node": _node(node)}


async def _clients_for_context(kubeconfig: str, context: str) -> KubernetesClients:
    try:
        from kubernetes_asyncio import client, config
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        raise RuntimeError("kubernetes_asyncio is required for kubernetes_inspect") from exc
    api_client = await config.new_client_from_config(config_file=kubeconfig, context=context)
    return _clients_over(client, api_client)


def _in_cluster_clients() -> KubernetesClients:
    try:
        from kubernetes_asyncio import client, config
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        raise RuntimeError("kubernetes_asyncio is required for kubernetes_inspect") from exc
    config.load_incluster_config()
    return _clients_over(client, client.ApiClient())


def _clients_over(client: Any, api_client: Any) -> KubernetesClients:
    """Bind the three API groups to one shared ApiClient, so one close ends its session."""
    return KubernetesClients(
        core_v1=client.CoreV1Api(api_client),
        apps_v1=client.AppsV1Api(api_client),
        batch_v1=client.BatchV1Api(api_client),
        aclose=api_client.close,
    )


def _namespace_name(input: dict) -> tuple[str, str]:  # noqa: A002
    namespace = str(input.get("namespace") or "default").strip() or "default"
    name = str(input.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    return namespace, name


def _workload_reader(apps_v1: Any, batch_v1: Any, kind: str) -> Any:
    normalized = kind.lower()
    if normalized == "deployment":
        return apps_v1.read_namespaced_deployment
    if normalized == "replicaset":
        return apps_v1.read_namespaced_replica_set
    if normalized == "statefulset":
        return apps_v1.read_namespaced_stateful_set
    if normalized == "daemonset":
        return apps_v1.read_namespaced_daemon_set
    if normalized == "job":
        return batch_v1.read_namespaced_job
    raise ValueError(f"unsupported workload kind: {kind}")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _metadata(obj: Any) -> dict[str, Any]:
    meta = _field(obj, "metadata", default={}) or {}
    return {
        "name": _field(meta, "name", default=""),
        "namespace": _field(meta, "namespace", default=""),
        "uid": _field(meta, "uid", default=""),
        "labels": dict(_field(meta, "labels", default={}) or {}),
        "owner_references": [
            {
                "kind": _field(ref, "kind", default=""),
                "name": _field(ref, "name", default=""),
                "uid": _field(ref, "uid", default=""),
                "controller": bool(_field(ref, "controller", default=False)),
            }
            for ref in list(_field(meta, "owner_references", "ownerReferences", default=[]) or [])
        ],
    }


def _pod(pod: Any) -> dict[str, Any]:
    status = _field(pod, "status", default={}) or {}
    spec = _field(pod, "spec", default={}) or {}
    return {
        "metadata": _metadata(pod),
        "phase": _field(status, "phase", default=""),
        "reason": _field(status, "reason", default=""),
        "message": _field(status, "message", default=""),
        "node_name": _field(spec, "node_name", "nodeName", default=""),
        "conditions": [
            {
                "type": _field(cond, "type", default=""),
                "status": _field(cond, "status", default=""),
                "reason": _field(cond, "reason", default=""),
                "message": _field(cond, "message", default=""),
            }
            for cond in list(_field(status, "conditions", default=[]) or [])
        ],
        "containers": [
            {
                "name": _field(state, "name", default=""),
                "ready": bool(_field(state, "ready", default=False)),
                "restart_count": int(
                    _field(state, "restart_count", "restartCount", default=0) or 0
                ),
                "state": _container_state(_field(state, "state", default={})),
                "last_state": _container_state(
                    _field(state, "last_state", "lastState", default={})
                ),
            }
            for state in list(
                _field(status, "container_statuses", "containerStatuses", default=[]) or []
            )
        ],
    }


def _pod_summary(pod: Any) -> dict[str, Any]:
    status = _field(pod, "status", default={}) or {}
    spec = _field(pod, "spec", default={}) or {}
    meta = _field(pod, "metadata", default={}) or {}
    statuses = list(_field(status, "container_statuses", "containerStatuses", default=[]) or [])
    ready = sum(bool(_field(s, "ready", default=False)) for s in statuses)
    return {
        "name": _field(meta, "name", default=""),
        "phase": _field(status, "phase", default=""),
        "ready": f"{ready}/{len(statuses)}",
        "restarts": sum(
            int(_field(s, "restart_count", "restartCount", default=0) or 0) for s in statuses
        ),
        "node_name": _field(spec, "node_name", "nodeName", default=""),
        "started_at": _iso(_field(status, "start_time", "startTime")),
        "owner": next(
            (
                f"{_field(ref, 'kind', default='')}/{_field(ref, 'name', default='')}"
                for ref in list(
                    _field(meta, "owner_references", "ownerReferences", default=[]) or []
                )
            ),
            "",
        ),
    }


def _container_state(state: Any) -> dict[str, Any]:
    for key in ("waiting", "running", "terminated"):
        value = _field(state, key)
        if value is not None:
            return {
                "kind": key,
                "reason": _field(value, "reason", default=""),
                "message": _field(value, "message", default=""),
                "exit_code": _field(value, "exit_code", "exitCode", default=None),
                "started_at": _iso(_field(value, "started_at", "startedAt")),
                "finished_at": _iso(_field(value, "finished_at", "finishedAt")),
            }
    return {}


def _event(event: Any) -> dict[str, Any]:
    involved = _field(event, "involved_object", "involvedObject", default={}) or {}
    return {
        "metadata": _metadata(event),
        "type": _field(event, "type", default=""),
        "reason": _field(event, "reason", default=""),
        "message": _field(event, "message", default=""),
        "count": int(_field(event, "count", default=1) or 1),
        "first_timestamp": _iso(_field(event, "first_timestamp", "firstTimestamp")),
        "last_timestamp": _iso(_field(event, "last_timestamp", "lastTimestamp")),
        "involved_object": {
            "kind": _field(involved, "kind", default=""),
            "namespace": _field(involved, "namespace", default=""),
            "name": _field(involved, "name", default=""),
            "uid": _field(involved, "uid", default=""),
        },
    }


def _workload(obj: Any) -> dict[str, Any]:
    status = _field(obj, "status", default={}) or {}
    spec = _field(obj, "spec", default={}) or {}
    template = _field(spec, "template", default={}) or {}
    pod_spec = _field(template, "spec", default={}) or {}
    meta = _field(obj, "metadata", default={}) or {}
    return {
        "metadata": _metadata(obj),
        "images": {
            _field(container, "name", default=""): _field(container, "image", default="")
            for container in list(_field(pod_spec, "containers", default=[]) or [])
        },
        # observed == generation and updated == replicas means the rollout of
        # the current spec has finished, not just started.
        "generation": _field(meta, "generation", default=None),
        "observed_generation": _field(
            status, "observed_generation", "observedGeneration", default=None
        ),
        "updated_replicas": _field(status, "updated_replicas", "updatedReplicas", default=0),
        "replicas": _field(status, "replicas", default=_field(spec, "replicas", default=None)),
        "ready_replicas": _field(status, "ready_replicas", "readyReplicas", default=0),
        "available_replicas": _field(status, "available_replicas", "availableReplicas", default=0),
        "unavailable_replicas": _field(
            status,
            "unavailable_replicas",
            "unavailableReplicas",
            default=0,
        ),
        "conditions": [
            {
                "type": _field(cond, "type", default=""),
                "status": _field(cond, "status", default=""),
                "reason": _field(cond, "reason", default=""),
                "message": _field(cond, "message", default=""),
            }
            for cond in list(_field(status, "conditions", default=[]) or [])
        ],
    }


def _node(node: Any) -> dict[str, Any]:
    status = _field(node, "status", default={}) or {}
    return {
        "metadata": _metadata(node),
        "conditions": [
            {
                "type": _field(cond, "type", default=""),
                "status": _field(cond, "status", default=""),
                "reason": _field(cond, "reason", default=""),
                "message": _field(cond, "message", default=""),
            }
            for cond in list(_field(status, "conditions", default=[]) or [])
        ],
    }
