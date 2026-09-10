"""Named knowledge services deployed through the cluster's Flux controller."""

import base64
import hashlib
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
        envoy: dict | None = None,
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
        self.envoy = envoy or {}
        self.warden = warden
        self.in_cluster = in_cluster
        self.context = kube_context
        self._client = None
        self._mount_ports = {}

    async def _get_api(self):
        from kubernetes_asyncio import client

        if self._client is None:
            self._client = await create_flux_api_client(
                in_cluster=self.in_cluster,
                context=self.context,
            )
        return client.CustomObjectsApi(self._client)

    async def close(self) -> None:
        for port in self._mount_ports.values():
            close = getattr(port, "aclose", None) or getattr(port, "close", None)
            if close is not None:
                await close()
        if self._client is not None:
            await self._client.close()

    def _view(self, obj: dict[str, Any]) -> dict[str, Any]:
        conditions = obj.get("status", {}).get("conditions", [])
        ready = next((c for c in conditions if c["type"] == "Ready"), {})
        observed = obj.get("status", {}).get("observedGeneration", 0)
        current = obj["metadata"].get("generation", 1)
        return {
            "name": obj["metadata"]
            .get("annotations", {})
            .get("niuu.world/instance-name", obj["metadata"]["name"]),
            "release_name": obj["metadata"]["name"],
            "can_update": True,
            "backend": obj["metadata"]["labels"]["niuu.world/knowledge-backend"],
            "ready": ready.get("status") == "True" and observed >= current,
            "message": ready.get("message", "Waiting for Flux reconciliation"),
        }

    async def list_deployments(self, *, tenant_id: str = "") -> dict[str, Any]:
        api = await self._get_api()
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
            "releases": [
                self._view(item) for item in result["items"] if self._owned(item, tenant_id)
            ],
        }

    @staticmethod
    def _tenant_key(tenant_id: str) -> str:
        return hashlib.sha256(tenant_id.encode()).hexdigest()[:16]

    def _release_name(self, name: str, tenant_id: str) -> str:
        return f"{name}-{self._tenant_key(tenant_id)[:15]}"

    @staticmethod
    def _owned(obj: dict, tenant_id: str) -> bool:
        return (
            bool(tenant_id)
            and obj.get("metadata", {}).get("annotations", {}).get("niuu.world/tenant-id")
            == tenant_id
        )

    async def _owned_release_name(self, name: str, tenant_id: str) -> str:
        status = await self.list_deployments(tenant_id=tenant_id)
        for release in status["releases"]:
            if release["name"] == name:
                return release["release_name"]
        raise ValueError("Knowledge instance not found in this tenant")

    async def discover_mounts(self, tenant_id: str, authorization: str = "") -> list[dict]:
        if not tenant_id:
            return []
        from kubernetes_asyncio import client

        from ravn.adapters.mimir.gbrain import GBrainMimirAdapter
        from ravn.adapters.mimir.http import HttpMimirAdapter
        from ravn.domain.mimir import MimirAuth

        status = await self.list_deployments(tenant_id=tenant_id)
        core = client.CoreV1Api(self._client)
        mounts = []
        for release in status["releases"]:
            if not release["ready"]:
                continue
            name = release["release_name"]
            services = await core.list_namespaced_service(
                self.namespace, label_selector=f"app.kubernetes.io/instance={name}"
            )
            backend = release["backend"]
            service = next(
                (
                    s
                    for s in services.items
                    if s.metadata.labels.get("app.kubernetes.io/name") == backend
                ),
                None,
            )
            if service is None:
                raise RuntimeError(f"Ready instance {release['name']} has no knowledge service")
            port_number = service.spec.ports[0].port
            url = f"http://{service.metadata.name}.{self.namespace}.svc.cluster.local:{port_number}"
            key = (tenant_id, name, url)
            if backend == "gbrain":
                secret = await core.read_namespaced_secret(
                    f"{name}-gbrain-connection", self.namespace
                )
                token = base64.b64decode(secret.data["token"]).decode()
                key += (hashlib.sha256(token.encode()).hexdigest(),)
                if key not in self._mount_ports:
                    self._mount_ports[key] = GBrainMimirAdapter(
                        mcp_url=url + "/mcp", api_token=token, query_expansion=False
                    )
            else:
                if not authorization.startswith("Bearer "):
                    raise ValueError("A caller token is required to open the Mimir instance")
                mimir_port = HttpMimirAdapter(
                    base_url=url + "/api/v1",
                    auth=MimirAuth(type="bearer", token=authorization.removeprefix("Bearer ")),
                )
            mounts.append(
                {
                    "name": release["name"],
                    "tenant_id": tenant_id,
                    "kind": "remote",
                    "role": "shared",
                    "categories": None,
                    "priority": 10,
                    "port": self._mount_ports[key] if backend == "gbrain" else mimir_port,
                    "close_after_request": backend == "mimir",
                    "desc": f"{backend} · {self.cluster} · {self.namespace}",
                }
            )
        return mounts

    async def deploy(self, request: DeploymentRequest) -> dict[str, Any]:
        if not request.tenant_id:
            raise ValueError("An authenticated tenant is required to deploy a knowledge instance")
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
            "niuu": {
                "cluster": self.cluster,
                "instanceId": request.name,
                "tenantId": request.tenant_id,
            },
        }
        if backend == "gbrain":
            values.update(
                engine="postgres",
                existingSecret=secret,
                postgres={"enabled": not bool(secret), "storageClass": self.storage_class},
                dream=request.dream.model_dump(),
                connection={"enabled": True},
            )
        else:
            if not self.envoy.get("enabled") or not self.envoy.get("jwt", {}).get("enabled"):
                raise ValueError(
                    "Configure the target Envoy JWT profile for tenant-owned Mimir instances"
                )
            values["envoy"] = self.envoy
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
            self._release_name(request.name, request.tenant_id),
            values,
            chart_name=backend,
            chart_version=self.versions[backend],
            labels={
                "niuu.world/managed-by": "mimir",
                "niuu.world/knowledge-backend": backend,
                "niuu.world/tenant": self._tenant_key(request.tenant_id),
            },
        )
        body["metadata"].setdefault("annotations", {}).update(
            {"niuu.world/tenant-id": request.tenant_id, "niuu.world/instance-name": request.name}
        )
        api = await self._get_api()
        obj = await api.create_namespaced_custom_object(
            HELMRELEASE_GROUP, HELMRELEASE_VERSION, self.namespace, HELMRELEASE_PLURAL, body
        )
        return self._view(obj)

    async def inspect_deployment(self, name: str, *, tenant_id: str = "") -> dict:
        from kubernetes_asyncio import client

        name = await self._owned_release_name(name.removeprefix("cluster/"), tenant_id)
        api = await self._get_api()
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

    async def control(self, name: str, action: str, *, tenant_id: str = "") -> dict:
        name = await self._owned_release_name(name.removeprefix("cluster/"), tenant_id)
        if action not in {"start", "stop", "update"}:
            raise ValueError("Supported actions: start, stop, update")
        existing = await self._get_helmrelease(name)
        if existing is None:
            raise ValueError("Deployment does not exist")
        labels = existing.get("metadata", {}).get("labels", {})
        if labels.get("niuu.world/managed-by") != "mimir":
            raise ValueError("This release is not managed by the knowledge registry")
        if action == "update":
            backend = labels["niuu.world/knowledge-backend"]
            if backend not in self.versions or backend not in self.images:
                raise ValueError(f"No chart and image configured for {backend}")
            repository, tag = self.images[backend].rsplit(":", 1)
            updated = await self._patch_helmrelease(
                name,
                {
                    "spec": {
                        "chart": {"spec": {"version": self.versions[backend]}},
                        "values": {
                            **(
                                {"connection": {"enabled": True}}
                                if backend == "gbrain"
                                else {"envoy": self.envoy}
                            ),
                            "image": {"repository": repository, "tag": tag},
                            "niuu": {
                                "cluster": self.cluster,
                                "instanceId": self._view(existing)["name"],
                                "tenantId": tenant_id,
                            },
                        },
                    }
                },
            )
            return self._view(updated)
        obj = await self._patch_helmrelease(
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
