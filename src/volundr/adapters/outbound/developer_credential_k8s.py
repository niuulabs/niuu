"""Mutable Kubernetes Secret projection for developer coordinator credentials."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from volundr.domain.models import PodSpecAdditions
from volundr.ports.developer_execution_credentials import (
    DeveloperCredentialProjection,
    DeveloperCredentialProjectionPort,
)

DEFAULT_MOUNT_PATH = "/var/run/secrets/niuu-developer-execution"


class KubernetesDeveloperCredentialProjection(DeveloperCredentialProjectionPort):
    """Create or replace one per-session Secret and mount its directory."""

    def __init__(
        self,
        *,
        namespace: str,
        mount_path: str = DEFAULT_MOUNT_PATH,
        secret_prefix: str = "developer-execution",
        api: Any | None = None,
        **_extra: object,
    ) -> None:
        if not namespace.strip():
            raise ValueError("Kubernetes developer credential projection requires namespace")
        self._namespace = namespace
        self._mount_path = mount_path.rstrip("/")
        self._secret_prefix = secret_prefix.rstrip("-")
        self._api = api

    def supports(self, runtime_backend: str) -> bool:
        return runtime_backend == "kubernetes"

    def _name(self, session_id: UUID) -> str:
        return f"{self._secret_prefix}-{session_id.hex}"[:63].rstrip("-")

    async def _get_api(self) -> Any:
        if self._api is not None:
            return self._api
        from kubernetes_asyncio import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()
        self._api = client.CoreV1Api()
        return self._api

    @staticmethod
    def _status(exc: Exception) -> int | None:
        value = getattr(exc, "status", None)
        return value if isinstance(value, int) else None

    async def project(
        self,
        *,
        session_id: UUID,
        token: str,
        runtime_backend: str,
    ) -> DeveloperCredentialProjection:
        if not self.supports(runtime_backend):
            raise ValueError(f"Kubernetes credential projection cannot serve {runtime_backend!r}")
        if not token.strip():
            raise ValueError("developer credential token must not be empty")
        api = await self._get_api()
        name = self._name(session_id)
        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "volundr",
                    "volundr.niuu.io/session-id": str(session_id),
                    "volundr.niuu.io/purpose": "developer-execution-credential",
                },
            },
            "type": "Opaque",
            "stringData": {"token": token},
        }
        try:
            await api.read_namespaced_secret(name=name, namespace=self._namespace)
        except Exception as exc:
            if self._status(exc) != 404:
                raise
            try:
                await api.create_namespaced_secret(namespace=self._namespace, body=body)
            except Exception as create_exc:
                if self._status(create_exc) != 409:
                    raise
                await api.patch_namespaced_secret(
                    name=name,
                    namespace=self._namespace,
                    body=body,
                )
        else:
            await api.patch_namespaced_secret(name=name, namespace=self._namespace, body=body)

        volume_name = f"developer-credential-{session_id.hex[:12]}"
        return DeveloperCredentialProjection(
            token_file=f"{self._mount_path}/token",
            pod_spec=PodSpecAdditions(
                volumes=(
                    {
                        "name": volume_name,
                        "secret": {"secretName": name, "defaultMode": 0o440},
                    },
                ),
                volume_mounts=(
                    {
                        "name": volume_name,
                        "mountPath": self._mount_path,
                        "readOnly": True,
                    },
                ),
            ),
        )

    async def remove(self, session_id: UUID) -> None:
        api = await self._get_api()
        try:
            await api.delete_namespaced_secret(
                name=self._name(session_id),
                namespace=self._namespace,
            )
        except Exception as exc:
            if self._status(exc) != 404:
                raise
