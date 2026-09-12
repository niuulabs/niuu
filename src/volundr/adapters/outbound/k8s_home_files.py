"""Short-lived, owner-scoped home-volume access without a coding session."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from volundr.adapters.outbound import user_home_files
from volundr.domain.ports import HomeStorageBusyError


def browser_pod_name(user_id: str) -> str:
    return "volundr-home-" + hashlib.sha256(user_id.encode()).hexdigest()[:20]


def browser_manifest(user_id: str, claim: str, image: str, lifetime: int) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": browser_pod_name(user_id),
            "labels": {
                "app.kubernetes.io/name": "skuld",
                "app.kubernetes.io/component": "home-browser",
                "app.kubernetes.io/managed-by": "volundr",
                "volundr/owner": user_id,
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": lifetime,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            # Match the same ownership/placement rule as the user's coding pods.
            # Self-affinity permits the first pod when the user has no sessions.
            "affinity": {
                "podAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": [
                        {
                            "topologyKey": "kubernetes.io/hostname",
                            "labelSelector": {
                                "matchLabels": {
                                    "app.kubernetes.io/name": "skuld",
                                    "volundr/owner": user_id,
                                }
                            },
                        }
                    ]
                }
            },
            "volumes": [{"name": "home", "persistentVolumeClaim": {"claimName": claim}}],
            "containers": [
                {
                    "name": "files",
                    "image": image,
                    "command": ["python", "-B", "-c", f"import time; time.sleep({lifetime})"],
                    "volumeMounts": [{"name": "home", "mountPath": "/home"}],
                    "securityContext": {
                        "readOnlyRootFilesystem": True,
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "64Mi"},
                        "limits": {"cpu": "250m", "memory": "128Mi"},
                    },
                }
            ],
        },
    }


async def _execute(
    namespace: str, pod_name: str, operation: str, path: str, timeout: float
) -> dict:
    from kubernetes_asyncio import client
    from kubernetes_asyncio.stream import WsApiClient

    script = Path(user_home_files.__file__).read_text()
    async with asyncio.timeout(timeout), WsApiClient() as api:
        socket = await client.CoreV1Api(api).connect_get_namespaced_pod_exec(
            pod_name,
            namespace,
            container="files",
            command=["python", "-B", "-c", script, "/home", operation, path],
            stdout=True,
            stderr=False,
            stdin=False,
            tty=False,
            _request_timeout=timeout,
            _preload_content=False,
        )
        output = []
        async with socket as websocket:
            async for message in websocket:
                if not isinstance(message.data, bytes) or not message.data:
                    continue
                channel, data = message.data[0], message.data[1:]
                if channel == 1:
                    output.append(data)
                elif channel == 3 and WsApiClient.parse_error_data(data) != 0:
                    raise RuntimeError("Home file operation failed")
    result = json.loads(b"".join(output))
    if result.get("error"):
        errors = {
            "FileNotFoundError": FileNotFoundError,
            "PermissionError": PermissionError,
            "ValueError": ValueError,
            "NotADirectoryError": ValueError,
        }
        raise errors.get(result["error"], RuntimeError)(result["detail"])
    return {"status": "ready", **result}


async def manage_home(
    api,
    namespace: str,
    user_id: str,
    claim: str,
    image: str,
    lifetime: int,
    timeout: float,
    operation: str,
    path: str,
) -> dict:
    if operation not in {"list", "delete"}:
        raise ValueError("Unsupported home operation")
    if not image:
        raise NotImplementedError("Home file management is not enabled on this cluster")
    name = browser_pod_name(user_id)
    if operation == "delete":
        pods = await api.list_namespaced_pod(namespace)
        for pod in pods.items:
            if pod.metadata.name == name or pod.status.phase not in {"Pending", "Running"}:
                continue
            if any(
                v.persistent_volume_claim and v.persistent_volume_claim.claim_name == claim
                for v in pod.spec.volumes or []
            ):
                raise HomeStorageBusyError(
                    "Stop workloads using this home on this cluster before deleting files"
                )
    try:
        pod = await api.read_namespaced_pod(name, namespace)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise
        try:
            await api.create_namespaced_pod(
                namespace, browser_manifest(user_id, claim, image, lifetime)
            )
        except Exception as create_error:
            if getattr(create_error, "status", None) != 409:
                raise
        return {"status": "starting", "detail": "Mounting your home storage"}
    if (pod.metadata.labels or {}).get("volundr/owner") != user_id:
        raise PermissionError("Home browser is not owned by this user")
    if not any(
        v.persistent_volume_claim and v.persistent_volume_claim.claim_name == claim
        for v in pod.spec.volumes or []
    ):
        raise PermissionError("Home browser has an unexpected volume")
    if pod.status.phase in {"Failed", "Succeeded"}:
        await api.delete_namespaced_pod(
            name, namespace, body={"preconditions": {"uid": pod.metadata.uid}}
        )
        return {"status": "starting", "detail": "Renewing your home storage connection"}
    if pod.status.phase != "Running":
        return {"status": "starting", "detail": "Waiting for your home volume to mount"}
    return await _execute(namespace, name, operation, path, timeout)
