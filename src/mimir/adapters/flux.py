"""Named knowledge services deployed through the cluster's Flux controller.

It also provisions the memory gateway's per-org memory systems
(``provision_memory_system``): one gbrain HelmRelease per org, named
``memory-<key of the org>``, in this target's namespace, labelled
``niuu.world/managed-by: mimir-memory`` so the knowledge registry never lists it as a
mount. The HelmRelease is the registry: its ``niuu.world/tenant-id`` annotation says
whose it is, and creating it is atomic, so two gateway replicas asking for one org at
once deploy it once (the second create is a 409 and reads the first). Beside it:

- the gbrain chart's own ``<release>-gbrain-admin`` Secret holds the owner bootstrap
  token (created by the chart once and kept on upgrade);
- ``<release>-memory-seal`` holds the key that seals attribution, created here once
  (a 409 reads the winner's), never owned by the HelmRelease, so deleting the release
  keeps it.

gbrain is initialised database-only (the chart's ``--db-only``) on its own PostgreSQL
(CloudNativePG, ``postgres.enabled``) unless ``memory_values`` says otherwise.
"""

import asyncio
import base64
import copy
import hashlib
import secrets
from typing import Any

from mimir.ports.deployment import (
    DeploymentRequest,
    KnowledgeDeploymentPort,
    MemoryProvisioningError,
    MemorySystemPhase,
    MemorySystemRecord,
    memory_system_key,
    memory_system_name,
)
from niuu.adapters.flux import (
    HELMRELEASE_GROUP,
    HELMRELEASE_PLURAL,
    HELMRELEASE_VERSION,
    FluxHelmReleases,
    create_flux_api_client,
)

#: The only knowledge backend this target deploys.
GBRAIN = "gbrain"

#: The ``niuu.world/managed-by`` label of a provisioned memory system.
MEMORY_MANAGED_BY = "mimir-memory"
_TENANT_ANNOTATION = "niuu.world/tenant-id"
_SEAL_KEY = "key"
_ADMIN_TOKEN_KEY = "GBRAIN_ADMIN_BOOTSTRAP_TOKEN"
#: Random bytes of a new signing key (64 URL-safe characters, as the mimir chart makes).
_SEAL_BYTES = 48
#: Chart values a memory system's deployment owns; ``memory_values`` cannot set them.
_OWNED_MEMORY_VALUES = ("existingSecret", "niuu", "image")


def _image(reference: str) -> tuple[str, str]:
    """``(repository, tag)`` of an image reference, a digest kept with its tag."""
    name, _, digest = reference.partition("@")
    repository, sep, tag = name.rpartition(":")
    if not sep or "/" in tag:
        raise ValueError(f"image {reference!r} has no tag; pin it as <repository>:<tag>")
    return repository, f"{tag}@{digest}" if digest else tag


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        current = result.get(key)
        result[key] = (
            _deep_merge(current, value)
            if isinstance(current, dict) and isinstance(value, dict)
            else copy.deepcopy(value)
        )
    return result


def _status(exc: BaseException) -> int | None:
    return getattr(exc, "status", None)


class FluxKnowledgeDeploymentAdapter(FluxHelmReleases, KnowledgeDeploymentPort):
    """Knowledge instances and memory systems as Flux HelmReleases.

    Args (memory systems):
        memory_values: gbrain chart values merged over a memory system's defaults
            (``engine: postgres`` with its own CloudNativePG database, no connection
            token), e.g. ``{postgres: {instances: 1, size: 10Gi}, resources: {...}}``.
            ``existingSecret``, ``niuu`` and ``image`` belong to the deployment: one
            shared Secret would give every org the same database and owner token.
    """

    _managed_by = "mimir"
    provisions_memory_systems = True

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
        global_instances: list[str] | None = None,
        source_namespace: str = "",
        in_cluster: bool = True,
        kube_context: str | None = None,
        think_model: str = "",
        gbrain_config: dict[str, Any] | None = None,
        memory_values: dict[str, Any] | None = None,
    ) -> None:
        owned = [key for key in _OWNED_MEMORY_VALUES if key in (memory_values or {})]
        if owned:
            raise ValueError(
                f"memory_values sets {', '.join(owned)}, which each memory system's "
                "deployment owns (an org's own database, owner token and image); remove them"
            )
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
        self.global_instances = global_instances or []
        self.in_cluster = in_cluster
        self.context = kube_context
        # `<provider>:<model>` passed straight through to gbrain's `think` MCP
        # tool call (GBrainMimirAdapter.query). Without this, gbrain's think
        # ignores its own configured chat model and falls back to a hardcoded
        # Anthropic default (NO_ANTHROPIC_API_KEY) — see
        # mimir.adapters.gbrain.GBrainMimirAdapter.__init__.
        self.think_model = think_model
        # Additional gbrain chart `config` (config.json passthrough) applied
        # to every tenant gbrain HelmRelease this target deploys — e.g.
        # `provider_base_urls.<recipe>` to point a chat/think provider at an
        # in-cluster gateway. Deliberately excludes credentials/storage
        # settings, same contract as charts/gbrain/values.yaml's `config`.
        self.gbrain_config = gbrain_config or {}
        self.memory_values = copy.deepcopy(memory_values or {})
        self._client = None
        self._client_lock = asyncio.Lock()
        self._mount_ports = {}

    def _configured(self, backend: str) -> bool:
        return backend in self.versions and backend in self.images

    async def _get_api(self):
        from kubernetes_asyncio import client

        # Reads are issued concurrently, so the first of them must not race
        # another into building (and leaking) a second client.
        async with self._client_lock:
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
            "access_scope": (
                "tenant"
                if obj["metadata"].get("annotations", {}).get("niuu.world/tenant-id")
                else "global"
                if obj["metadata"].get("annotations", {}).get("niuu.world/scope") == "global"
                else "unknown"
            ),
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
            "backends": [GBRAIN] if self._configured(GBRAIN) else [],
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

        from mimir.adapters.gbrain import GBrainMimirAdapter

        # Runs on every Mímir request, so its Kubernetes reads are issued
        # together rather than one after another (two per ready instance plus
        # one per global instance).
        status, global_objects = await asyncio.gather(
            self.list_deployments(tenant_id=tenant_id),
            asyncio.gather(*(self._get_helmrelease(name) for name in self.global_instances)),
        )
        for name, obj in zip(self.global_instances, global_objects, strict=True):
            if obj is None:
                raise ValueError(f"Configured global knowledge instance {name} does not exist")
            annotations = obj.get("metadata", {}).get("annotations", {})
            values = obj.get("spec", {}).get("values", {})
            if (
                annotations.get("niuu.world/scope") != "global"
                or annotations.get("niuu.world/tenant-id")
                or values.get("niuu", {}).get("tenantId")
            ):
                raise ValueError(f"Knowledge instance {name} is not explicitly global")
            view = self._view(obj)
            if any(release["name"] == view["name"] for release in status["releases"]):
                raise ValueError(
                    f"Global knowledge mount {view['name']} conflicts with a tenant mount"
                )
            status["releases"].append(view)
        core = client.CoreV1Api(self._client)

        async def mount_for(release: dict[str, Any]) -> dict[str, Any]:
            name = release["release_name"]
            services = await core.list_namespaced_service(
                self.namespace, label_selector=f"app.kubernetes.io/instance={name}"
            )
            service = next(
                (
                    s
                    for s in services.items
                    if s.metadata.labels.get("app.kubernetes.io/name") == GBRAIN
                ),
                None,
            )
            if service is None:
                raise RuntimeError(f"Ready instance {release['name']} has no knowledge service")
            port_number = service.spec.ports[0].port
            url = f"http://{service.metadata.name}.{self.namespace}.svc.cluster.local:{port_number}"
            secret = await core.read_namespaced_secret(f"{name}-gbrain-connection", self.namespace)
            token = base64.b64decode(secret.data["token"]).decode()
            key = (tenant_id, name, url, hashlib.sha256(token.encode()).hexdigest())
            if key not in self._mount_ports:
                self._mount_ports[key] = GBrainMimirAdapter(
                    mcp_url=url + "/mcp",
                    api_token=token,
                    query_expansion=False,
                    think_model=self.think_model,
                )
            return {
                "name": release["name"],
                "tenant_id": tenant_id,
                "access_scope": "global" if name in self.global_instances else "tenant",
                "kind": "remote",
                "role": "shared",
                "categories": None,
                "priority": 10,
                "port": self._mount_ports[key],
                "desc": f"{GBRAIN} · {self.cluster} · {self.namespace}",
            }

        return list(
            await asyncio.gather(
                *(mount_for(release) for release in status["releases"] if release["ready"])
            )
        )

    async def deploy(self, request: DeploymentRequest) -> dict[str, Any]:
        if not request.tenant_id:
            raise ValueError("An authenticated tenant is required to deploy a knowledge instance")
        if request.name in self.global_instances:
            raise ValueError("This name is reserved for an operator-managed global instance")
        backend = request.backend
        if not self._configured(backend):
            raise ValueError(f"No chart and image configured for {backend}")
        secret = request.secret or self.secrets.get(backend, "").replace("{name}", request.name)
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
        values.update(
            engine="postgres",
            existingSecret=secret,
            postgres={"enabled": not bool(secret), "storageClass": self.storage_class},
            connection={"enabled": True},
        )
        if self.gbrain_config:
            values["config"] = copy.deepcopy(self.gbrain_config)
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
        return {**self._view(obj), "logs": logs}

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
            if backend != GBRAIN or not self._configured(backend):
                raise ValueError(f"No chart and image configured for {backend}")
            repository, tag = self.images[backend].rsplit(":", 1)
            updated = await self._patch_helmrelease(
                name,
                {
                    "spec": {
                        "chart": {"spec": {"version": self.versions[backend]}},
                        "values": {
                            "connection": {"enabled": True},
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
                    }
                }
            },
        )
        return self._view(obj)

    # -- memory systems --------------------------------------------------

    async def _core(self):
        from kubernetes_asyncio import client

        await self._get_api()
        return client.CoreV1Api(self._client)

    @staticmethod
    def _provisioning_errors() -> tuple[type[BaseException], ...]:
        """What asking the Kubernetes API can raise: a refusal, or the connection failing
        (aiohttp's connection errors are OSErrors) or timing out."""
        from kubernetes_asyncio.client.exceptions import ApiException

        return (ApiException, OSError, asyncio.TimeoutError)

    async def provision_memory_system(self, tenant_id: str) -> MemorySystemRecord:
        name = memory_system_name(tenant_id)
        try:
            release = await self._get_helmrelease(name)
            if release is not None:
                return await self._memory_record(release, tenant_id)
            # The seal comes first: a release never exists without its key, so a release
            # whose key is missing is a lost key (failed), never one to make again.
            await self._ensure_seal(name, tenant_id)
            release, started = await self._create_memory_release(name, tenant_id)
            return await self._memory_record(release, tenant_id, started=started)
        except self._provisioning_errors() as exc:
            raise MemoryProvisioningError(
                f"Kubernetes refused provisioning {name} in namespace {self.namespace}: {exc}"
            ) from exc

    async def memory_system(self, tenant_id: str) -> MemorySystemRecord | None:
        name = memory_system_name(tenant_id)
        try:
            release = await self._get_helmrelease(name)
            if release is None:
                return None
            return await self._memory_record(release, tenant_id)
        except self._provisioning_errors() as exc:
            raise MemoryProvisioningError(
                f"cannot read memory system {name} in namespace {self.namespace}: {exc}"
            ) from exc

    async def memory_systems(self) -> list[MemorySystemRecord]:
        try:
            api = await self._get_api()
            listed = await api.list_namespaced_custom_object(
                HELMRELEASE_GROUP,
                HELMRELEASE_VERSION,
                self.namespace,
                HELMRELEASE_PLURAL,
                label_selector=f"niuu.world/managed-by={MEMORY_MANAGED_BY}",
            )
            records = []
            for release in listed["items"]:
                tenant_id = release["metadata"].get("annotations", {}).get(_TENANT_ANNOTATION)
                if not tenant_id:
                    raise MemoryProvisioningError(
                        f"memory system {release['metadata']['name']} has no "
                        f"{_TENANT_ANNOTATION} annotation; it was not made by the gateway"
                    )
                records.append(await self._memory_record(release, tenant_id))
            return records
        except self._provisioning_errors() as exc:
            raise MemoryProvisioningError(
                f"cannot list memory systems in namespace {self.namespace}: {exc}"
            ) from exc

    def _memory_values(self, name: str, tenant_id: str) -> dict[str, Any]:
        if not self._configured(GBRAIN):
            raise MemoryProvisioningError(
                "no gbrain chart version and image are configured for this target "
                "(chart_versions.gbrain, images.gbrain)"
            )
        repository, tag = _image(self.images[GBRAIN])
        values: dict[str, Any] = {
            "engine": "postgres",
            "postgres": {"enabled": True, "storageClass": self.storage_class},
            "persistence": {"storageClass": self.storage_class},
            "connection": {"enabled": False},
        }
        if self.gbrain_config:
            values["config"] = copy.deepcopy(self.gbrain_config)
        values = _deep_merge(values, self.memory_values)
        values["image"] = {"repository": repository, "tag": tag}
        values["niuu"] = {"cluster": self.cluster, "instanceId": name, "tenantId": tenant_id}
        return values

    async def _create_memory_release(self, name: str, tenant_id: str) -> tuple[dict, bool]:
        from kubernetes_asyncio.client.exceptions import ApiException

        body = self._build_helmrelease(
            name,
            self._memory_values(name, tenant_id),
            chart_name=GBRAIN,
            chart_version=self.versions[GBRAIN],
            labels={
                "niuu.world/managed-by": MEMORY_MANAGED_BY,
                "niuu.world/knowledge-backend": GBRAIN,
                "niuu.world/tenant": memory_system_key(tenant_id),
            },
            annotations={_TENANT_ANNOTATION: tenant_id, "niuu.world/instance-name": name},
        )
        api = await self._get_api()
        try:
            created = await api.create_namespaced_custom_object(
                HELMRELEASE_GROUP, HELMRELEASE_VERSION, self.namespace, HELMRELEASE_PLURAL, body
            )
        except ApiException as exc:
            if _status(exc) != 409:
                raise
            # Another replica created it first: theirs is the org's system.
            existing = await self._get_helmrelease(name)
            if existing is None:
                raise MemoryProvisioningError(
                    f"memory system {name} was reported as existing and then not found"
                ) from exc
            return existing, False
        return created, True

    def _seal_name(self, name: str) -> str:
        return f"{name}-memory-seal"

    async def _ensure_seal(self, name: str, tenant_id: str) -> None:
        from kubernetes_asyncio.client.exceptions import ApiException

        core = await self._core()
        key = secrets.token_urlsafe(_SEAL_BYTES)
        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": self._seal_name(name),
                "labels": {
                    "niuu.world/managed-by": MEMORY_MANAGED_BY,
                    "niuu.world/tenant": memory_system_key(tenant_id),
                },
                "annotations": {_TENANT_ANNOTATION: tenant_id},
            },
            "type": "Opaque",
            "data": {_SEAL_KEY: base64.b64encode(key.encode()).decode()},
        }
        try:
            await core.create_namespaced_secret(self.namespace, body)
        except ApiException as exc:
            if _status(exc) != 409:
                raise
            # Made before (by another replica, or an attempt that stopped before its
            # release): that key is the system's, never replaced.

    async def _read_secret(self, name: str) -> Any | None:
        from kubernetes_asyncio.client.exceptions import ApiException

        core = await self._core()
        try:
            return await core.read_namespaced_secret(name, self.namespace)
        except ApiException as exc:
            if _status(exc) == 404:
                return None
            raise

    @staticmethod
    def _secret_value(secret: Any, key: str) -> str:
        data = getattr(secret, "data", None) or {}
        value = data.get(key)
        return base64.b64decode(value).decode().strip() if value else ""

    @staticmethod
    def _owner(obj: Any) -> str:
        metadata = obj["metadata"] if isinstance(obj, dict) else obj.metadata
        annotations = (
            metadata.get("annotations") if isinstance(metadata, dict) else metadata.annotations
        )
        return (annotations or {}).get(_TENANT_ANNOTATION, "")

    async def _memory_record(
        self, release: dict[str, Any], tenant_id: str, *, started: bool = False
    ) -> MemorySystemRecord:
        name = release["metadata"]["name"]
        owner = self._owner(release)
        if owner != tenant_id:
            raise MemoryProvisioningError(
                f"HelmRelease {name} belongs to org {owner!r}, not {tenant_id!r}; the two orgs' "
                "memory-system keys collide. Move one of them to memory.tenants"
            )

        def record(phase: MemorySystemPhase, message: str, **ready: str) -> MemorySystemRecord:
            return MemorySystemRecord(
                tenant_id=tenant_id,
                name=name,
                phase=phase,
                message=message,
                started=started,
                **ready,
            )

        seal = await self._read_secret(self._seal_name(name))
        if seal is None or self._owner(seal) != tenant_id:
            return record(
                MemorySystemPhase.FAILED,
                f"its signing key Secret {self._seal_name(name)} in namespace {self.namespace} "
                "is missing or belongs to another org. Restore it from a backup: a new key "
                "would make every record it sealed read as unattested",
            )
        signing_key = self._secret_value(seal, _SEAL_KEY)
        if not signing_key:
            return record(
                MemorySystemPhase.FAILED,
                f"its signing key Secret {self._seal_name(name)} has no '{_SEAL_KEY}'",
            )
        conditions = {c.get("type"): c for c in release.get("status", {}).get("conditions", [])}
        stalled = conditions.get("Stalled", {})
        if stalled.get("status") == "True":
            return record(
                MemorySystemPhase.FAILED,
                f"Flux stopped installing HelmRelease {name} in namespace {self.namespace} "
                f"({stalled.get('reason', 'Stalled')}: {stalled.get('message', '')}). Fix the "
                "cause, then `flux reconcile helmrelease` it",
            )
        ready = conditions.get("Ready", {})
        observed = release.get("status", {}).get("observedGeneration", 0)
        current = release["metadata"].get("generation", 1)
        if ready.get("status") != "True" or observed < current:
            return record(
                MemorySystemPhase.PREPARING,
                ready.get("message") or "waiting for Flux to install its gbrain",
            )
        gbrain = f"{name}-gbrain"
        admin = await self._read_secret(f"{gbrain}-admin")
        admin_token = self._secret_value(admin, _ADMIN_TOKEN_KEY) if admin is not None else ""
        if not admin_token:
            return record(
                MemorySystemPhase.FAILED,
                f"gbrain's owner token ({_ADMIN_TOKEN_KEY} in Secret {gbrain}-admin) is "
                "missing although the release is installed",
            )
        core = await self._core()
        service = await core.read_namespaced_service(gbrain, self.namespace)
        port = service.spec.ports[0].port
        return record(
            MemorySystemPhase.READY,
            "ready",
            mcp_url=f"http://{gbrain}.{self.namespace}.svc.cluster.local:{port}/mcp",
            admin_token=admin_token,
            signing_key=signing_key,
        )
