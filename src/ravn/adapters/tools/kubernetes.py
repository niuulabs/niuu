"""Read-only Kubernetes inspection tools for resident Valkyries."""

from __future__ import annotations

import inspect
import json
import os
from typing import Any

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort


class KubernetesInspectTool(ToolPort):
    """Inspect Kubernetes objects with the resident service account.

    This intentionally exposes only read-only calls. It gives k8s Valkyries a
    real container-safe inspection capability without depending on a kubectl
    binary in the image.
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
    ) -> None:
        self._in_cluster = in_cluster
        self._kubeconfig_path = kubeconfig_path
        self._kubeconfig_env = kubeconfig_env
        self._core_v1 = core_v1
        self._apps_v1 = apps_v1
        self._batch_v1 = batch_v1
        self._loaded = core_v1 is not None
        self._max_log_lines = max(1, max_log_lines)

    @property
    def name(self) -> str:
        return "kubernetes_inspect"

    @property
    def description(self) -> str:
        return (
            "Read-only Kubernetes inspection for resident Valkyries. Supports "
            "pod status, object events, pod logs, workload status, and node status "
            "using the in-cluster service account or a kubeconfig."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "inspect_pod",
                        "object_events",
                        "pod_logs",
                        "workload",
                        "node",
                    ],
                    "description": "Read-only inspection action to perform.",
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace for namespaced objects.",
                },
                "kind": {
                    "type": "string",
                    "description": "Object kind for events/workloads.",
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
            await self._ensure_clients()
            match action:
                case "inspect_pod":
                    payload = await self._inspect_pod(input)
                case "object_events":
                    payload = await self._object_events(input)
                case "pod_logs":
                    payload = await self._pod_logs(input)
                case "workload":
                    payload = await self._workload(input)
                case "node":
                    payload = await self._node(input)
                case _:
                    return ToolResult(
                        tool_call_id="",
                        content=f"Error: unsupported kubernetes_inspect action {action!r}.",
                        is_error=True,
                    )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id="",
                content=f"kubernetes_inspect failed: {exc}",
                is_error=True,
            )
        return ToolResult(tool_call_id="", content=json.dumps(payload, indent=2, sort_keys=True))

    async def _ensure_clients(self) -> None:
        if self._loaded:
            return
        try:
            from kubernetes_asyncio import client, config
        except ImportError as exc:  # pragma: no cover - depends on installed extras
            raise RuntimeError("kubernetes_asyncio is required for kubernetes_inspect") from exc

        if self._in_cluster:
            config.load_incluster_config()
        else:
            kubeconfig = self._kubeconfig_path or os.environ.get(self._kubeconfig_env, "")
            maybe_loaded = config.load_kube_config(config_file=kubeconfig or None)
            if inspect.isawaitable(maybe_loaded):
                _ = await maybe_loaded
        self._core_v1 = client.CoreV1Api()
        self._apps_v1 = client.AppsV1Api()
        self._batch_v1 = client.BatchV1Api()
        self._loaded = True

    async def _inspect_pod(self, input: dict) -> dict[str, Any]:  # noqa: A002
        namespace, name = _namespace_name(input)
        pod = await _maybe_await(self._core_v1.read_namespaced_pod(name, namespace))
        return {"action": "inspect_pod", "namespace": namespace, "name": name, "pod": _pod(pod)}

    async def _object_events(self, input: dict) -> dict[str, Any]:  # noqa: A002
        namespace, name = _namespace_name(input)
        kind = str(input.get("kind") or "Pod").strip() or "Pod"
        selector = f"involvedObject.name={name},involvedObject.kind={kind}"
        events = await _maybe_await(
            self._core_v1.list_namespaced_event(namespace, field_selector=selector)
        )
        return {
            "action": "object_events",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "events": [_event(item) for item in list(getattr(events, "items", events) or [])],
        }

    async def _pod_logs(self, input: dict) -> dict[str, Any]:  # noqa: A002
        namespace, name = _namespace_name(input)
        tail_lines = int(input.get("tail_lines") or self._max_log_lines)
        tail_lines = max(1, min(tail_lines, self._max_log_lines))
        kwargs: dict[str, Any] = {"tail_lines": tail_lines}
        container = str(input.get("container") or "").strip()
        if container:
            kwargs["container"] = container
        logs = await _maybe_await(self._core_v1.read_namespaced_pod_log(name, namespace, **kwargs))
        return {
            "action": "pod_logs",
            "namespace": namespace,
            "name": name,
            "container": container,
            "tail_lines": tail_lines,
            "logs": str(logs),
        }

    async def _workload(self, input: dict) -> dict[str, Any]:  # noqa: A002
        namespace, name = _namespace_name(input)
        kind = str(input.get("kind") or "Deployment").strip()
        method = _workload_reader(self._apps_v1, self._batch_v1, kind)
        obj = await _maybe_await(method(name, namespace))
        return {
            "action": "workload",
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "workload": _workload(obj),
        }

    async def _node(self, input: dict) -> dict[str, Any]:  # noqa: A002
        name = str(input.get("name") or "").strip()
        if not name:
            raise ValueError("name is required for node")
        node = await _maybe_await(self._core_v1.read_node(name))
        return {"action": "node", "name": name, "node": _node(node)}


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
    return {
        "metadata": _metadata(obj),
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
