"""Flux HelmRelease adapter for pod management.

Creates HelmRelease custom resources in Kubernetes, which Flux
reconciles into running session pods. Selected via the dynamic
adapter pattern in config YAML.
"""

import copy
import logging

from volundr.domain.models import Session, SessionSpec, SessionStatus, _deep_merge
from volundr.domain.ports import PodManager, PodStartResult

logger = logging.getLogger(__name__)

# Flux HelmRelease API coordinates
HELMRELEASE_GROUP = "helm.toolkit.fluxcd.io"
HELMRELEASE_VERSION = "v2"
HELMRELEASE_PLURAL = "helmreleases"


class FluxPodManager(PodManager):
    """Flux-native implementation of PodManager.

    Creates / deletes HelmRelease CRs via the Kubernetes API.
    Flux's helm-controller reconciles them into actual Helm releases.

    Constructor accepts plain kwargs (dynamic adapter pattern).
    """

    def __init__(
        self,
        *,
        namespace: str = "default",
        chart_name: str = "skuld",
        chart_version: str = "0.38.0",
        source_ref_kind: str = "HelmRepository",
        source_ref_name: str = "skuld",
        source_ref_namespace: str = "",
        timeout: str = "5m",
        interval: str = "5m",
        base_domain: str = "volundr.local",
        chat_scheme: str = "wss",
        code_scheme: str = "https",
        chat_path: str = "/session",
        code_path: str = "/",
        gateway_domain: str | None = None,
        session_defaults: dict | None = None,
        **_extra: object,
    ):
        self._namespace = namespace
        self._chart_name = chart_name
        self._chart_version = chart_version
        self._source_ref_kind = source_ref_kind
        self._source_ref_name = source_ref_name
        self._source_ref_namespace = source_ref_namespace or namespace
        self._timeout = timeout
        self._interval = interval
        self._base_domain = base_domain
        self._chat_scheme = chat_scheme
        self._code_scheme = code_scheme
        self._chat_path = chat_path
        self._code_path = code_path
        self._gateway_domain = gateway_domain
        self._session_defaults = session_defaults or {}
        self._api_client = None

    async def _get_api(self):
        """Lazy-load kubernetes_asyncio custom objects API."""
        if self._api_client is None:
            from kubernetes_asyncio import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                await config.load_kube_config()
            self._api_client = client.ApiClient()
        from kubernetes_asyncio import client

        return client.CustomObjectsApi(self._api_client)

    def _release_name(self, session: Session) -> str:
        return f"skuld-{session.id}"

    def _session_host(self, session_name: str) -> str:
        return f"{session_name}.{self._base_domain}"

    def _chat_endpoint(self, session_name: str, session_id: str = "") -> str:
        if self._gateway_domain:
            return f"wss://{self._gateway_domain}/s/{session_id}/session"
        return f"{self._chat_scheme}://{self._session_host(session_name)}{self._chat_path}"

    def initial_chat_endpoint(self, session: Session) -> str | None:
        return self._chat_endpoint(session.name, str(session.id))

    def _code_endpoint(self, session_name: str, session_id: str = "") -> str:
        if self._gateway_domain:
            return f"https://{self._gateway_domain}/s/{session_id}/"
        return f"{self._code_scheme}://{self._session_host(session_name)}{self._code_path}"

    def _build_helmrelease(self, name: str, values: dict) -> dict:
        """Build a HelmRelease CR manifest."""
        source_ref: dict = {
            "kind": self._source_ref_kind,
            "name": self._source_ref_name,
        }
        if self._source_ref_namespace:
            source_ref["namespace"] = self._source_ref_namespace

        return {
            "apiVersion": f"{HELMRELEASE_GROUP}/{HELMRELEASE_VERSION}",
            "kind": "HelmRelease",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "volundr",
                },
            },
            "spec": {
                "interval": self._interval,
                "timeout": self._timeout,
                "chart": {
                    "spec": {
                        "chart": self._chart_name,
                        "version": self._chart_version,
                        "sourceRef": source_ref,
                    },
                },
                "values": values,
            },
        }

    async def start(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> PodStartResult:
        """Create a HelmRelease CR for the session."""
        api = await self._get_api()
        release_name = self._release_name(session)

        # Merge session defaults with spec values from contributors
        values = copy.deepcopy(self._session_defaults)
        _deep_merge(values, spec.values)

        # Translate pod_spec additions into Helm values
        if spec.pod_spec:
            if spec.pod_spec.env:
                env_vars = list(values.get("envVars") or [])
                env_names = {
                    str(entry.get("name"))
                    for entry in env_vars
                    if isinstance(entry, dict) and entry.get("name")
                }
                for env in spec.pod_spec.env:
                    env_dict = dict(env)
                    env_name = str(env_dict.get("name") or "")
                    if env_name and env_name in env_names:
                        env_vars = [
                            existing
                            for existing in env_vars
                            if not (
                                isinstance(existing, dict)
                                and str(existing.get("name") or "") == env_name
                            )
                        ]
                    env_vars.append(env_dict)
                    if env_name:
                        env_names.add(env_name)
                values["envVars"] = env_vars
            if spec.pod_spec.volumes:
                values["extraVolumes"] = [dict(v) for v in spec.pod_spec.volumes]
            if spec.pod_spec.volume_mounts:
                values["extraVolumeMounts"] = [dict(vm) for vm in spec.pod_spec.volume_mounts]
            if spec.pod_spec.init_containers:
                values["extraInitContainers"] = [
                    dict(container) for container in spec.pod_spec.init_containers
                ]
            if spec.pod_spec.extra_containers:
                values["extraContainers"] = [
                    dict(container) for container in spec.pod_spec.extra_containers
                ]
            if spec.pod_spec.service_account:
                values["serviceAccountName"] = spec.pod_spec.service_account
            if spec.pod_spec.labels:
                values["podLabels"] = dict(spec.pod_spec.labels)
            if spec.pod_spec.annotations:
                values["podAnnotations"] = dict(spec.pod_spec.annotations)

        _inject_workload_exchange_env(values)

        manifest = self._build_helmrelease(release_name, values)

        try:
            await api.create_namespaced_custom_object(
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                body=manifest,
            )
        except Exception as exc:
            err_str = str(exc)
            if "409" in err_str or "AlreadyExists" in err_str:
                logger.info("HelmRelease %s already exists, patching", release_name)
                await api.patch_namespaced_custom_object(
                    group=HELMRELEASE_GROUP,
                    version=HELMRELEASE_VERSION,
                    namespace=self._namespace,
                    plural=HELMRELEASE_PLURAL,
                    name=release_name,
                    body=manifest,
                )
            else:
                raise

        logger.info(
            "Created HelmRelease %s in namespace %s",
            release_name,
            self._namespace,
        )

        return PodStartResult(
            chat_endpoint=self._chat_endpoint(session.name, str(session.id)),
            code_endpoint=self._code_endpoint(session.name, str(session.id)),
            pod_name=release_name,
        )

    async def stop(self, session: Session) -> bool:
        """Delete the HelmRelease CR for the session."""
        api = await self._get_api()
        release_name = self._release_name(session)

        try:
            await api.delete_namespaced_custom_object(
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                name=release_name,
            )
            logger.info("Deleted HelmRelease %s", release_name)
            return True
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                logger.debug(
                    "HelmRelease %s not found, treating as already stopped",
                    release_name,
                )
                return False
            raise

    async def status(self, session: Session) -> SessionStatus:
        """Read HelmRelease status conditions and map to SessionStatus."""
        api = await self._get_api()
        release_name = self._release_name(session)

        try:
            obj = await api.get_namespaced_custom_object(
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                name=release_name,
            )
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                return SessionStatus.STOPPED
            raise

        return self._map_status(obj)

    @staticmethod
    def _map_status(obj: dict) -> SessionStatus:
        """Map HelmRelease .status.conditions to SessionStatus."""
        status = obj.get("status", {})
        conditions = status.get("conditions", [])

        for cond in conditions:
            if cond.get("type") != "Ready":
                continue
            if cond.get("status") == "True":
                return SessionStatus.RUNNING
            reason = cond.get("reason", "")
            if reason in (
                "InstallFailed",
                "UpgradeFailed",
                "ReconciliationFailed",
            ):
                return SessionStatus.FAILED
            return SessionStatus.STARTING

        # No Ready condition yet — still reconciling
        return SessionStatus.STARTING

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        """Watch the HelmRelease CR until infrastructure is ready or failed."""
        # Check current status first to allow early return
        current = await self.status(session)
        if current in (SessionStatus.RUNNING, SessionStatus.FAILED):
            return current

        from kubernetes_asyncio import watch

        api = await self._get_api()
        release_name = self._release_name(session)

        w = watch.Watch()
        try:
            async for event in w.stream(
                api.list_namespaced_custom_object,
                group=HELMRELEASE_GROUP,
                version=HELMRELEASE_VERSION,
                namespace=self._namespace,
                plural=HELMRELEASE_PLURAL,
                field_selector=f"metadata.name={release_name}",
                timeout_seconds=int(timeout),
            ):
                obj = event.get("object", {})
                if not isinstance(obj, dict):
                    continue
                status = self._map_status(obj)
                if status == SessionStatus.RUNNING:
                    return SessionStatus.RUNNING
                if status == SessionStatus.FAILED:
                    return SessionStatus.FAILED
        except Exception as exc:
            logger.warning(
                "Watch stream error for HelmRelease %s: %s",
                release_name,
                exc,
                exc_info=True,
            )
            raise
        finally:
            w.stop()

        return SessionStatus.FAILED

    async def close(self) -> None:
        """Close the Kubernetes API client."""
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None


def _inject_workload_exchange_env(values: dict) -> None:
    """Propagate workload token exchange URL into all session containers."""
    volundr_cfg = values.get("volundr")
    if not isinstance(volundr_cfg, dict):
        return
    api_url = str(volundr_cfg.get("apiUrl") or "").rstrip("/")
    if not api_url:
        return
    exchange_url = f"{api_url}/api/v1/tokens/workload/exchange"
    env_entry = {
        "name": "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
        "value": exchange_url,
    }

    env_vars = list(values.get("envVars") or [])
    if not _has_env(env_vars, env_entry["name"]):
        env_vars.append(env_entry)
        values["envVars"] = env_vars

    extra_containers = values.get("extraContainers")
    if not isinstance(extra_containers, list):
        return
    for container in extra_containers:
        if not isinstance(container, dict):
            continue
        container_env = list(container.get("env") or [])
        if _has_env(container_env, env_entry["name"]):
            continue
        container_env.append(dict(env_entry))
        container["env"] = container_env


def _has_env(env: list, name: str) -> bool:
    return any(isinstance(entry, dict) and entry.get("name") == name for entry in env)
