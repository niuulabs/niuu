"""Named knowledge services deployed through the cluster's Flux controller."""

from typing import Any

from mimir.dream_results import dream_results
from mimir.ports.deployment import DeploymentRequest, KnowledgeDeploymentPort
from niuu.adapters.flux import (
    HELMRELEASE_GROUP,
    HELMRELEASE_PLURAL,
    HELMRELEASE_VERSION,
    FluxHelmReleases,
    create_flux_api_client,
)


class FluxKnowledgeDeploymentAdapter(FluxHelmReleases, KnowledgeDeploymentPort):
    _managed_by = "mimir"

    def __init__(
        self,
        *,
        namespace: str,
        source_name: str,
        chart_versions: dict[str, str],
        images: dict[str, str],
        cluster: str = "current",
        storage_class: str = "",
        secrets: dict[str, str] | None = None,
        warden: dict | None = None,
        source_namespace: str = "",
        in_cluster: bool = True,
        kube_context: str | None = None,
    ) -> None:
        self._namespace = namespace
        self._source_ref_kind = "HelmRepository"
        self._source_ref_name = source_name
        self._source_ref_namespace = source_namespace or namespace
        self._interval = "5m"
        self._timeout = "10m"
        self.namespace = namespace
        self.source_name = source_name
        self.source_namespace = source_namespace or namespace
        self.versions = chart_versions
        self.images = images
        self.cluster = cluster
        self.storage_class = storage_class
        self.secrets = secrets or {}
        self.warden = warden
        self.in_cluster = in_cluster
        self.context = kube_context
        self._client = None

    async def _api(self):
        from kubernetes_asyncio import client

        if self._client is None:
            self._client = await create_flux_api_client(
                in_cluster=self.in_cluster,
                context=self.context,
            )
        return client.CustomObjectsApi(self._client)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    def _view(self, obj: dict[str, Any]) -> dict[str, Any]:
        conditions = obj.get("status", {}).get("conditions", [])
        ready = next((c for c in conditions if c["type"] == "Ready"), {})
        observed = obj.get("status", {}).get("observedGeneration", 0)
        current = obj["metadata"].get("generation", 1)
        return {
            "name": obj["metadata"]["name"],
            "backend": obj["metadata"]["labels"]["niuu.world/knowledge-backend"],
            "ready": ready.get("status") == "True" and observed >= current,
            "message": ready.get("message", "Waiting for Flux reconciliation"),
        }

    async def list_deployments(self) -> dict[str, Any]:
        api = await self._api()
        result = await api.list_namespaced_custom_object(
            HELMRELEASE_GROUP,
            HELMRELEASE_VERSION,
            self.namespace,
            HELMRELEASE_PLURAL,
            label_selector="niuu.world/managed-by=mimir",
        )
        return {
            "cluster": self.cluster,
            "namespace": self.namespace,
            "target": "cluster",
            "warden_available": self.warden is not None,
            "dream_available": "gbrain" in self.versions,
            "backends": sorted(set(self.versions) & set(self.images)),
            "releases": [self._view(item) for item in result["items"]],
        }

    async def deploy(self, request: DeploymentRequest) -> dict[str, Any]:
        backend = request.backend
        if backend not in self.versions or backend not in self.images:
            raise ValueError(f"No chart and image configured for {backend}")
        secret = request.secret or self.secrets.get(backend, "").replace("{name}", request.name)
        if request.warden and self.warden is None:
            raise ValueError("Configure a warden runtime on this target before enabling it")
        image = self.images[backend]
        repository, tag = image.rsplit(":", 1)
        values: dict[str, Any] = {
            "image": {"repository": repository, "tag": tag},
            "persistence": {"storageClass": self.storage_class},
        }
        if backend == "gbrain":
            values.update(
                engine="postgres",
                existingSecret=secret,
                postgres={"enabled": not bool(secret), "storageClass": self.storage_class},
                dream=request.dream.model_dump(),
            )
        else:
            values["image"]["registry"] = ""
            values["config"] = {"name": request.name, "role": "shared"}
        if request.warden:
            import copy

            warden = copy.deepcopy(self.warden)
            warden["enabled"] = True
            warden["spec"] = request.warden_overrides.apply(warden.get("spec", {}))
            # Explicit instance choices take precedence over legacy runtime overrides.
            config = warden.setdefault("config", {})
            paths = {
                "model": [("llm", "model")],
                "persona": [
                    ("initiative", "default_persona"),
                    ("dream_cycle", "persona"),
                    ("mimir", "source_trigger", "persona"),
                    ("mimir", "staleness_trigger", "persona"),
                ],
                "dream_cycle_cron_expression": [("dream_cycle", "cron_expression")],
                "source_trigger_poll_interval_seconds": [
                    ("mimir", "source_trigger", "poll_interval_seconds")
                ],
                "staleness_trigger_schedule_hours": [
                    ("mimir", "staleness_trigger", "schedule_hours")
                ],
            }
            for key, value in request.warden_overrides.model_dump(exclude_none=True).items():
                for path in paths[key]:
                    section = config
                    for part in path[:-1]:
                        section = section.setdefault(part, {})
                    section[path[-1]] = value
            values["warden"] = warden
        body = self._build_helmrelease(
            request.name,
            values,
            chart_name=backend,
            chart_version=self.versions[backend],
            labels={"niuu.world/managed-by": "mimir", "niuu.world/knowledge-backend": backend},
        )
        api = await self._api()
        obj = await api.create_namespaced_custom_object(
            HELMRELEASE_GROUP, HELMRELEASE_VERSION, self.namespace, HELMRELEASE_PLURAL, body
        )
        return self._view(obj)

    async def inspect_deployment(self, name: str) -> dict:
        from kubernetes_asyncio import client

        name = name.removeprefix("cluster/")
        api = await self._api()
        obj = await api.get_namespaced_custom_object(
            HELMRELEASE_GROUP, HELMRELEASE_VERSION, self.namespace, HELMRELEASE_PLURAL, name
        )
        if obj.get("metadata", {}).get("labels", {}).get("niuu.world/managed-by") != "mimir":
            raise ValueError("This release is not managed by the knowledge registry")
        core = client.CoreV1Api(self._client)
        pods = await core.list_namespaced_pod(
            self.namespace, label_selector=f"app.kubernetes.io/instance={name}"
        )
        logs = {}
        for pod in pods.items:
            for container in pod.spec.containers:
                key = f"{pod.metadata.name}/{container.name}"
                logs[key] = await core.read_namespaced_pod_log(
                    pod.metadata.name, self.namespace, container=container.name, tail_lines=200
                )
        values = obj.get("spec", {}).get("values", {})
        return {
            **self._view(obj),
            "logs": logs,
            "dream_results": [
                report for output in logs.values() for report in dream_results(output)
            ],
            "dream": values.get("dream", {}),
            "warden": values.get("warden", {}).get("enabled", False),
        }

    async def control(self, name: str, action: str) -> dict:
        name = name.removeprefix("cluster/")
        if action not in {"start", "stop"}:
            raise ValueError("Supported actions: start, stop")
        await self.inspect_deployment(name)
        api = await self._api()
        obj = await api.patch_namespaced_custom_object(
            HELMRELEASE_GROUP,
            HELMRELEASE_VERSION,
            self.namespace,
            HELMRELEASE_PLURAL,
            name,
            {
                "spec": {
                    "values": {
                        "replicaCount": 1 if action == "start" else 0,
                        "dream": {"suspend": action == "stop"},
                    }
                }
            },
        )
        return self._view(obj)
