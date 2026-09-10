"""Shared Flux HelmRelease operations for sessions and named services.

Callers own workload values, naming, and credentials; this mixin owns the Flux
resource envelope and lifecycle. Kubernetes access comes from `_get_api`.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)
HELMRELEASE_GROUP = "helm.toolkit.fluxcd.io"
HELMRELEASE_VERSION = "v2"
HELMRELEASE_PLURAL = "helmreleases"


async def create_flux_api_client(*, in_cluster: bool | None = None, context: str | None = None):
    """Reuse session Kubernetes credentials; explicit targets never auto-switch."""
    from kubernetes_asyncio import client, config

    if in_cluster is False:
        await config.load_kube_config(context=context)
    elif in_cluster is True:
        config.load_incluster_config()
    else:
        # Preserve the established session auto-discovery behavior.
        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config(context=context)
    return client.ApiClient()


class FluxHelmReleases:
    _managed_by = "volundr"

    def _build_helmrelease(
        self,
        name: str,
        values: dict,
        *,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
        chart_name: str | None = None,
        chart_version: str | None = None,
    ) -> dict:
        """Build a HelmRelease CR manifest."""
        source_ref: dict = {
            "kind": self._source_ref_kind,
            "name": self._source_ref_name,
        }
        if self._source_ref_namespace:
            source_ref["namespace"] = self._source_ref_namespace

        metadata: dict[str, Any] = {
            "name": name,
            "namespace": self._namespace,
            "labels": {
                "app.kubernetes.io/managed-by": self._managed_by,
                **(labels or {}),
            },
        }
        if annotations:
            metadata["annotations"] = annotations

        return {
            "apiVersion": f"{HELMRELEASE_GROUP}/{HELMRELEASE_VERSION}",
            "kind": "HelmRelease",
            "metadata": metadata,
            "spec": {
                "interval": self._interval,
                "timeout": self._timeout,
                "chart": {
                    "spec": {
                        "chart": chart_name or self._chart_name,
                        "version": chart_version or self._chart_version,
                        "sourceRef": source_ref,
                    },
                },
                "values": values,
            },
        }

    async def _apply_helmrelease(
        self,
        name: str,
        values: dict,
        *,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
    ) -> None:
        api = await self._get_api()
        manifest = self._build_helmrelease(
            name,
            values,
            labels=labels,
            annotations=annotations,
        )
        try:
            await api.create_namespaced_custom_object(
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                body=manifest,
            )
        except Exception as exc:
            if "409" not in str(exc) and "AlreadyExists" not in str(exc):
                raise
            logger.info("HelmRelease %s already exists, patching", name)
            await api.patch_namespaced_custom_object(
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                name=name,
                body=manifest,
                _content_type="application/merge-patch+json",
            )

    async def _get_helmrelease(self, name: str) -> dict[str, Any] | None:
        api = await self._get_api()
        try:
            return await api.get_namespaced_custom_object(
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                name=name,
            )
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                return None
            raise

    async def _patch_helmrelease(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        api = await self._get_api()
        return await api.patch_namespaced_custom_object(
            group=HELMRELEASE_GROUP,
            version=HELMRELEASE_VERSION,
            namespace=self._namespace,
            plural=HELMRELEASE_PLURAL,
            name=name,
            body=body,
            _content_type="application/merge-patch+json",
        )

    async def _delete_helmrelease(self, name: str) -> bool:
        api = await self._get_api()
        try:
            await api.delete_namespaced_custom_object(
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                name=name,
            )
            logger.info("Deleted HelmRelease %s", name)
            return True
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                logger.debug("HelmRelease %s is already absent", name)
                return False
            raise
