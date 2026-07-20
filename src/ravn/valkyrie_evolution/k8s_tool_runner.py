"""One Kubernetes Job per learned-tool invocation, with verified reach policy."""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ravn.valkyrie_evolution.learned_tools import (
    DEFAULT_CONTAINED_TOOL_IMAGE,
    NETWORK_REACH_KINDS,
    REACH_ENFORCEMENT_ENFORCED,
    REACH_ENFORCEMENT_UNAVAILABLE,
    LearnedToolError,
)
from ravn.valkyrie_evolution.models import ToolReachGrant
from ravn.valkyrie_evolution.tool_runtime import ToolRunResult

NETWORK_DENIED_LABEL = "denied"
NETWORK_ALLOWED_LABEL = "allowed"
DEFAULT_TOOL_RUN_IMAGE = DEFAULT_CONTAINED_TOOL_IMAGE
_MAX_BUNDLE_BYTES = 768 * 1024
_MAX_OUTPUT_BYTES = 256 * 1024


@dataclass
class JobRunResult:
    """Raw outcome of one isolated Job."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    output_exceeded: bool = False
    network_enforced: bool = False


class JobExecutor(Protocol):
    """Execute one tool in a separate runtime boundary."""

    enforces_reach: bool

    async def execute(
        self,
        *,
        run_name: str,
        image: str,
        code: str,
        payload: dict[str, Any],
        entry_point: str,
        requirements: Sequence[str],
        timeout_seconds: float,
        network_allowed: bool,
    ) -> JobRunResult: ...


class KubernetesJobLearnedToolRunner:
    """Translate a learned-tool invocation into one verified Kubernetes Job."""

    def __init__(self, *, executor: JobExecutor, image: str = DEFAULT_TOOL_RUN_IMAGE) -> None:
        self._executor = executor
        self._image = image

    @property
    def enforces_reach(self) -> bool:
        return bool(self._executor.enforces_reach)

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
        requirements: Sequence[str] = (),
        declared_reach: Sequence[ToolReachGrant] = (),
    ) -> ToolRunResult:
        try:
            network_allowed = _network_reach(declared_reach)
            if requirements:
                raise LearnedToolError(
                    "k8s_job does not install packages at invocation time; use a reviewed "
                    "immutable runner image containing the dependency and declare no runtime "
                    "requirements"
                )
            code = Path(tool_path).read_text(encoding="utf-8")
            bundle_size = len(code.encode()) + len(json.dumps(payload, default=str).encode())
            if bundle_size > _MAX_BUNDLE_BYTES:
                raise LearnedToolError(
                    f"learned-tool bundle exceeds {_MAX_BUNDLE_BYTES} byte Kubernetes limit"
                )
        except (OSError, LearnedToolError, TypeError, ValueError) as exc:
            return ToolRunResult(
                ok=False,
                error=str(exc),
                enforcement=REACH_ENFORCEMENT_UNAVAILABLE,
            )

        try:
            run = await self._executor.execute(
                run_name=_run_name(Path(tool_path).stem),
                image=self._image,
                code=code,
                payload=payload,
                entry_point=entry_point,
                requirements=(),
                timeout_seconds=timeout_seconds,
                network_allowed=network_allowed,
            )
        except Exception as exc:  # noqa: BLE001 - infrastructure failure is evidence
            return ToolRunResult(
                ok=False,
                error=f"pod-per-run execution failed: {exc}",
                enforcement=REACH_ENFORCEMENT_UNAVAILABLE,
            )

        enforcement = (
            REACH_ENFORCEMENT_ENFORCED if run.network_enforced else REACH_ENFORCEMENT_UNAVAILABLE
        )
        if run.timed_out:
            return ToolRunResult(
                ok=False,
                error=f"learned tool timed out after {timeout_seconds}s",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        if run.output_exceeded:
            return ToolRunResult(
                ok=False,
                error=f"learned tool output exceeded {_MAX_OUTPUT_BYTES} bytes",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        if run.exit_code != 0:
            return ToolRunResult(
                ok=False,
                error=f"learned tool exited with status {run.exit_code}",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        try:
            result = json.loads(run.stdout)
        except json.JSONDecodeError as exc:
            return ToolRunResult(
                ok=False,
                error=f"learned tool produced non-JSON output: {exc}",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        if not isinstance(result, dict):
            return ToolRunResult(
                ok=False,
                error=f"learned tool must return a JSON object, got {type(result).__name__}",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        return ToolRunResult(
            ok=True,
            result=result,
            stderr=run.stderr,
            enforcement=enforcement,
        )


def _network_reach(declared_reach: Sequence[ToolReachGrant]) -> bool:
    network = False
    for grant in declared_reach:
        kind = grant.kind.casefold()
        if kind in {"", "pure_compute"} or grant.access == "none":
            continue
        is_network = kind in NETWORK_REACH_KINDS or kind.startswith("http")
        if not is_network:
            raise LearnedToolError(
                f"k8s_job cannot enforce declared reach kind {grant.kind!r}; refusing to run"
            )
        if grant.target.strip():
            raise LearnedToolError(
                "k8s_job cannot enforce target-specific network reach "
                f"({grant.target}); refusing to widen it to unrestricted egress"
            )
        if grant.access != "read_write":
            raise LearnedToolError(
                "k8s_job can enforce only broad network/read_write reach; "
                f"refusing to treat sockets as {grant.access!r}"
            )
        network = True
    return network


def _run_name(stem: str) -> str:
    safe = "".join(char if char.isalnum() or char == "-" else "-" for char in stem.casefold())
    return f"ravn-tool-{safe.strip('-')[:40] or 'tool'}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class _ExecutorConfig:
    namespace: str
    image: str
    deny_policy_name: str
    allow_policy_name: str
    network_policy_label_key: str
    poll_interval_seconds: float = 1.0
    output_limit_bytes: int = _MAX_OUTPUT_BYTES


class KubernetesJobExecutor:
    """Create, observe, and remove locked-down Jobs using ``kubernetes_asyncio``.

    The executor verifies both configured NetworkPolicies through the live API
    immediately before every Job. A pod label alone is never treated as
    enforcement evidence.
    """

    def __init__(
        self,
        *,
        namespace: str,
        deny_policy_name: str,
        allow_policy_name: str,
        image: str = DEFAULT_TOOL_RUN_IMAGE,
        network_policy_label_key: str = "niuu.world/tool-network",
        batch_v1: Any | None = None,
        core_v1: Any | None = None,
        networking_v1: Any | None = None,
        in_cluster: bool = True,
    ) -> None:
        if not namespace.strip():
            raise LearnedToolError("k8s_job requires an explicit namespace")
        if not deny_policy_name.strip() or not allow_policy_name.strip():
            raise LearnedToolError("k8s_job requires explicit deny and allow policy names")
        if "@sha256:" not in image:
            raise LearnedToolError("k8s_job runner image must be pinned by sha256 digest")
        self._config = _ExecutorConfig(
            namespace=namespace,
            image=image,
            deny_policy_name=deny_policy_name,
            allow_policy_name=allow_policy_name,
            network_policy_label_key=network_policy_label_key,
        )
        self._batch_v1 = batch_v1
        self._core_v1 = core_v1
        self._networking_v1 = networking_v1
        self._in_cluster = in_cluster
        self._clients_loaded = all(
            client is not None for client in (batch_v1, core_v1, networking_v1)
        )
        self._policies_verified = False

    @property
    def enforces_reach(self) -> bool:
        """Whether a live policy check has succeeded for this executor."""
        return self._policies_verified

    async def execute(
        self,
        *,
        run_name: str,
        image: str,
        code: str,
        payload: dict[str, Any],
        entry_point: str,
        requirements: Sequence[str],
        timeout_seconds: float,
        network_allowed: bool,
    ) -> JobRunResult:
        if requirements:
            raise LearnedToolError("k8s_job runtime requirements must be empty")
        effective_image = image or self._config.image
        if "@sha256:" not in effective_image:
            raise LearnedToolError("k8s_job runner image must be pinned by sha256 digest")
        batch, core, networking = await self._load_clients()
        await self._verify_network_policies(networking)
        label = NETWORK_ALLOWED_LABEL if network_allowed else NETWORK_DENIED_LABEL
        await self._create_secret(core, run_name, code, payload)
        try:
            await self._create_job(
                batch,
                run_name,
                image=effective_image,
                entry_point=entry_point,
                network_label=label,
                timeout_seconds=timeout_seconds,
            )
            timed_out = not await self._wait_for_completion(batch, run_name, timeout_seconds)
            logs = "" if timed_out else await self._read_pod_logs(core, run_name)
            output_exceeded = len(logs.encode()) > self._config.output_limit_bytes
            if output_exceeded:
                logs = logs.encode()[: self._config.output_limit_bytes].decode(errors="replace")
            exit_code = 124 if timed_out else await self._exit_code(batch, run_name)
            return JobRunResult(
                stdout=logs,
                stderr=logs if exit_code else "",
                exit_code=exit_code,
                timed_out=timed_out,
                output_exceeded=output_exceeded,
                network_enforced=self._policies_verified,
            )
        finally:
            await asyncio.gather(
                self._delete_job(batch, run_name),
                self._delete_secret(core, run_name),
                return_exceptions=True,
            )

    async def _load_clients(self) -> tuple[Any, Any, Any]:
        if self._clients_loaded:
            return self._batch_v1, self._core_v1, self._networking_v1
        from kubernetes_asyncio import client, config  # noqa: PLC0415

        if self._in_cluster:
            config.load_incluster_config()
        else:
            await config.load_kube_config()
        self._batch_v1 = client.BatchV1Api()
        self._core_v1 = client.CoreV1Api()
        self._networking_v1 = client.NetworkingV1Api()
        self._clients_loaded = True
        return self._batch_v1, self._core_v1, self._networking_v1

    async def _verify_network_policies(self, networking: Any) -> None:
        self._policies_verified = False
        deny = await networking.read_namespaced_network_policy(
            self._config.deny_policy_name, self._config.namespace
        )
        allow = await networking.read_namespaced_network_policy(
            self._config.allow_policy_name, self._config.namespace
        )
        if not _policy_matches(
            deny,
            label_key=self._config.network_policy_label_key,
            label_value=NETWORK_DENIED_LABEL,
            allow_egress=False,
        ):
            raise LearnedToolError(
                f"NetworkPolicy {self._config.deny_policy_name!r} does not deny all egress "
                "for learned-tool denied pods"
            )
        if not _policy_matches(
            allow,
            label_key=self._config.network_policy_label_key,
            label_value=NETWORK_ALLOWED_LABEL,
            allow_egress=True,
        ):
            raise LearnedToolError(
                f"NetworkPolicy {self._config.allow_policy_name!r} does not allow egress "
                "for learned-tool allowed pods"
            )
        self._policies_verified = True

    async def _create_secret(
        self, core: Any, run_name: str, code: str, payload: dict[str, Any]
    ) -> None:
        await core.create_namespaced_secret(
            self._config.namespace,
            {
                "metadata": {"name": run_name},
                "stringData": {"tool.py": code, "payload.json": json.dumps(payload)},
                "type": "Opaque",
            },
        )

    async def _create_job(
        self,
        batch: Any,
        run_name: str,
        *,
        image: str,
        entry_point: str,
        network_label: str,
        timeout_seconds: float,
    ) -> None:
        labels = {self._config.network_policy_label_key: network_label}
        body = {
            "metadata": {"name": run_name, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": max(1, math.ceil(timeout_seconds)),
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "tool",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python", "-I", "-B", "-c", _BOOTSTRAP],
                                "env": [
                                    {"name": "RAVN_TOOL_ENTRY", "value": entry_point},
                                    {"name": "HOME", "value": "/tmp"},
                                ],
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "runAsNonRoot": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "50m", "memory": "64Mi"},
                                    "limits": {"cpu": "1", "memory": "512Mi"},
                                },
                                "volumeMounts": [
                                    {"name": "tool", "mountPath": "/tool", "readOnly": True},
                                    {"name": "tmp", "mountPath": "/tmp"},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "tool", "secret": {"secretName": run_name}},
                            {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                        ],
                    },
                },
            },
        }
        await batch.create_namespaced_job(self._config.namespace, body)

    async def _wait_for_completion(self, batch: Any, run_name: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = await batch.read_namespaced_job(run_name, self._config.namespace)
            status = _job_status(job)
            if status.get("succeeded") or status.get("failed"):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(self._config.poll_interval_seconds, remaining))

    async def _read_pod_logs(self, core: Any, run_name: str) -> str:
        pods = await core.list_namespaced_pod(
            self._config.namespace, label_selector=f"job-name={run_name}"
        )
        items = getattr(pods, "items", pods)
        if not items:
            return ""
        return await core.read_namespaced_pod_log(
            _pod_name(items[0]),
            self._config.namespace,
            limit_bytes=self._config.output_limit_bytes + 1,
        )

    async def _exit_code(self, batch: Any, run_name: str) -> int:
        status = _job_status(await batch.read_namespaced_job(run_name, self._config.namespace))
        return 0 if status.get("succeeded") else 1

    async def _delete_job(self, batch: Any, run_name: str) -> None:
        await batch.delete_namespaced_job(
            run_name,
            self._config.namespace,
            propagation_policy="Background",
            grace_period_seconds=0,
        )

    async def _delete_secret(self, core: Any, run_name: str) -> None:
        await core.delete_namespaced_secret(run_name, self._config.namespace)


def _policy_matches(
    policy: Any,
    *,
    label_key: str,
    label_value: str,
    allow_egress: bool,
) -> bool:
    data = policy.to_dict() if hasattr(policy, "to_dict") else policy
    if not isinstance(data, dict):
        return False
    spec = data.get("spec") or {}
    selector = spec.get("pod_selector") or spec.get("podSelector") or {}
    labels = selector.get("match_labels") or selector.get("matchLabels") or {}
    policy_types = spec.get("policy_types") or spec.get("policyTypes") or []
    if labels.get(label_key) != label_value or not {"Ingress", "Egress"}.issubset(policy_types):
        return False
    if spec.get("ingress"):
        return False
    egress = spec.get("egress")
    if not allow_egress:
        return not egress
    return isinstance(egress, list) and any(_is_allow_all_rule(rule) for rule in egress)


def _is_allow_all_rule(rule: Any) -> bool:
    data = rule.to_dict() if hasattr(rule, "to_dict") else rule
    return isinstance(data, dict) and not (data.get("ports") or data.get("to"))


def _job_status(job: Any) -> dict[str, Any]:
    status = getattr(job, "status", job)
    if isinstance(status, dict):
        return status
    return {
        "succeeded": getattr(status, "succeeded", None),
        "failed": getattr(status, "failed", None),
    }


def _pod_name(pod: Any) -> str:
    metadata = getattr(pod, "metadata", None)
    if metadata is not None:
        return str(getattr(metadata, "name", ""))
    return str(pod.get("metadata", {}).get("name", "")) if isinstance(pod, dict) else ""


_BOOTSTRAP = """\
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location("learned_tool", "/tool/tool.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with open("/tool/payload.json", encoding="utf-8") as handle:
    payload = json.load(handle)
result = getattr(module, os.environ["RAVN_TOOL_ENTRY"])(payload)
print(json.dumps(result), end="")
"""


__all__ = [
    "JobExecutor",
    "JobRunResult",
    "KubernetesJobExecutor",
    "KubernetesJobLearnedToolRunner",
]
