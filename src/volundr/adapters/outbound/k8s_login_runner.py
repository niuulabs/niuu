"""Official CLI enrollment in a bounded Job with a memory-backed temporary home."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files

from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
)
from volundr.domain.ports import CredentialEnrollmentRunnerPort

LOGIN_ROOT = "/tmp/login"
LOGIN_LABEL = "niuu.io/credential-enrollment"


class KubernetesLoginRunner(CredentialEnrollmentRunnerPort):
    def __init__(
        self,
        *,
        image: str,
        namespace: str = "niuu-logins",
        codex_executable: str = "/usr/local/bin/codex",
        claude_executable: str = "/usr/local/bin/claude",
        grok_executable: str = "/usr/local/bin/grok",
        worker_interval: float = 0.1,
        cpu_request: str = "100m",
        request_timeout: float = 15,
        cleanup_ttl_seconds: int = 60,
        memory_limit: str = "512Mi",
        cpu_limit: str = "500m",
        temporary_storage_limit: str = "32Mi",
        in_cluster: bool = True,
        **_extra: object,
    ) -> None:
        self._image = image
        self._namespace = namespace
        self._codex_executable = codex_executable
        self._claude_executable = claude_executable
        self._grok_executable = grok_executable
        self._worker_interval = worker_interval
        self._cpu_request = cpu_request
        self._timeout = request_timeout
        self._cleanup_ttl = cleanup_ttl_seconds
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._storage_limit = temporary_storage_limit
        self._in_cluster = in_cluster
        self._worker_source = (
            files("volundr.adapters.outbound").joinpath("login_worker.py").read_text()
        )

    def supports_enrollment(self, method: str) -> bool:
        return method in {"codex_device", "claude_setup", "grok_device"}

    def _executable(self, method: str) -> str:
        if method == "codex_device":
            return self._codex_executable
        if method == "grok_device":
            return self._grok_executable
        return self._claude_executable

    async def _configure(self) -> None:
        from kubernetes_asyncio import config

        if self._in_cluster:
            config.load_incluster_config()
            return
        await config.load_kube_config()

    @staticmethod
    def _name(enrollment: CredentialEnrollment) -> str:
        return f"login-{enrollment.id.hex}"

    def _manifest(self, enrollment: CredentialEnrollment) -> dict:
        ttl = max(1, int((enrollment.expires_at - datetime.now(UTC)).total_seconds()))
        labels = {LOGIN_LABEL: str(enrollment.id)}
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": self._name(enrollment), "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": ttl,
                "ttlSecondsAfterFinished": self._cleanup_ttl,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "fsGroup": 1000,
                        },
                        "containers": [
                            {
                                "name": "login",
                                "image": self._image,
                                "command": [
                                    "/opt/venv/bin/python",
                                    "-c",
                                    self._worker_source,
                                ],
                                "args": [
                                    "--root",
                                    LOGIN_ROOT,
                                    "--method",
                                    enrollment.method,
                                    "--executable",
                                    self._executable(enrollment.method),
                                    "--ttl",
                                    str(ttl),
                                    "--interval",
                                    str(self._worker_interval),
                                ],
                                "securityContext": {
                                    "readOnlyRootFilesystem": True,
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                    "seccompProfile": {"type": "RuntimeDefault"},
                                },
                                "resources": {
                                    "requests": {
                                        "memory": self._memory_limit,
                                        "cpu": self._cpu_request,
                                    },
                                    "limits": {
                                        "memory": self._memory_limit,
                                        "cpu": self._cpu_limit,
                                    },
                                },
                                "volumeMounts": [{"name": "temporary", "mountPath": "/tmp"}],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "temporary",
                                "emptyDir": {"medium": "Memory", "sizeLimit": self._storage_limit},
                            }
                        ],
                    },
                },
            },
        }

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        from kubernetes_asyncio import client

        if not self.supports_enrollment(enrollment.method):
            raise ValueError("Unsupported login method")
        await self._configure()
        async with client.ApiClient() as api:
            await client.BatchV1Api(api).create_namespaced_job(
                self._namespace, self._manifest(enrollment), _request_timeout=self._timeout
            )
        # Persist the identity immediately. Starting a CLI must never hold an
        # HTTP request open or leave an unidentifiable worker after a rollout.
        return replace(enrollment, runner_ref={"job_name": self._name(enrollment)})

    async def _read(self, pod_name: str, command: list[str]) -> dict:
        from kubernetes_asyncio import client
        from kubernetes_asyncio.stream import WsApiClient

        async with asyncio.timeout(self._timeout), WsApiClient() as api:
            socket = await client.CoreV1Api(api).connect_get_namespaced_pod_exec(
                pod_name,
                self._namespace,
                container="login",
                command=command,
                stdout=True,
                stderr=False,
                stdin=False,
                tty=False,
                _request_timeout=self._timeout,
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
                        raise RuntimeError("login_worker_read_failed")
        return json.loads(b"".join(output))

    async def poll_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollmentPoll:
        from kubernetes_asyncio import client
        from kubernetes_asyncio.client.exceptions import ApiException

        await self._configure()
        async with client.ApiClient() as api:
            try:
                job = await client.BatchV1Api(api).read_namespaced_job(
                    self._name(enrollment), self._namespace, _request_timeout=self._timeout
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise
                return CredentialEnrollmentPoll(
                    state=CredentialEnrollmentState.FAILED, error_code="login_worker_missing"
                )
            if job.status.failed:
                return CredentialEnrollmentPoll(
                    state=CredentialEnrollmentState.FAILED, error_code="login_worker_failed"
                )
            pods = await client.CoreV1Api(api).list_namespaced_pod(
                self._namespace,
                label_selector=f"{LOGIN_LABEL}={enrollment.id}",
                _request_timeout=self._timeout,
            )
        if not pods.items or pods.items[0].status.phase == "Pending":
            return CredentialEnrollmentPoll(state=CredentialEnrollmentState.PENDING)
        pod = pods.items[0]
        if pod.status.phase != "Running":
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code="login_worker_failed"
            )
        result = await self._read(
            pod.metadata.name,
            [
                "/opt/venv/bin/python",
                "-c",
                "from pathlib import Path; import json; "
                f"p=Path('{LOGIN_ROOT}/status.json'); "
                "s=json.loads(p.read_text()) if p.exists() else {'state':'pending'}; "
                f"s.update(credential_data=json.loads(Path('{LOGIN_ROOT}/credential.json')"
                ".read_text())) "
                "if s['state']=='complete' else None; print(json.dumps(s))",
            ],
        )
        return CredentialEnrollmentPoll(
            state=CredentialEnrollmentState(result["state"]),
            credential_data=result.get("credential_data", {}),
            verification_uri=result.get("verification_uri", ""),
            user_code=result.get("user_code", ""),
            error_code=result.get("error_code", ""),
        )

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        from kubernetes_asyncio import client
        from kubernetes_asyncio.client.exceptions import ApiException

        await self._configure()
        async with client.ApiClient() as api:
            try:
                await client.BatchV1Api(api).delete_namespaced_job(
                    self._name(enrollment),
                    self._namespace,
                    propagation_policy="Foreground",
                    _request_timeout=self._timeout,
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise

    async def submit_code(self, enrollment: CredentialEnrollment, code: str) -> None:
        from kubernetes_asyncio import client
        from kubernetes_asyncio.stream import WsApiClient

        if enrollment.method != "claude_setup":
            raise ValueError("This login does not accept an authorization code")
        await self._configure()
        async with client.ApiClient() as api:
            pods = await client.CoreV1Api(api).list_namespaced_pod(
                self._namespace,
                label_selector=f"{LOGIN_LABEL}={enrollment.id}",
                _request_timeout=self._timeout,
            )
        if not pods.items or pods.items[0].status.phase != "Running":
            raise ValueError("Login worker is not running")
        # Stdin keeps the authorization code out of Kubernetes audit arguments.
        command = [
            "/opt/venv/bin/python",
            "-c",
            "import sys,json; from pathlib import Path; "
            "value=json.loads(sys.stdin.readline()); "
            f"p=Path('{LOGIN_ROOT}/code.tmp'); p.write_text(json.dumps(value)); "
            f"p.replace('{LOGIN_ROOT}/code.json'); print('accepted',flush=True)",
        ]
        async with asyncio.timeout(self._timeout), WsApiClient() as api:
            websocket = await client.CoreV1Api(api).connect_get_namespaced_pod_exec(
                pods.items[0].metadata.name,
                self._namespace,
                container="login",
                command=command,
                stdin=True,
                stdout=True,
                stderr=False,
                tty=False,
                _preload_content=False,
            )
            output = ""
            async with websocket as ws:
                await ws.send_bytes(b"\x00" + (json.dumps({"code": code}) + "\n").encode())
                async for message in ws:
                    data = message.data
                    if isinstance(data, bytes) and data[:1] == b"\x01":
                        output += data[1:].decode()
            if output.strip() != "accepted":
                raise ValueError("Login worker could not accept the authorization code")
