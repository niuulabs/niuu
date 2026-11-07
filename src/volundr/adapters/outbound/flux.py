"""Flux HelmRelease adapter for session pod management.

Creates HelmRelease custom resources in Kubernetes, which Flux
reconciles into running session pods. Selected via the dynamic
adapter pattern in config YAML.
"""

import copy
import logging
from datetime import UTC, datetime
from typing import Any

from niuu.adapters.flux import (
    HELMRELEASE_GROUP,
    HELMRELEASE_PLURAL,
    HELMRELEASE_VERSION,
    FluxHelmReleases,
    create_flux_api_client,
)
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.k8s_startup_stages import pod_startup_stages
from volundr.domain.models import (
    Session,
    SessionSpec,
    SessionStatus,
    _deep_merge,
)
from volundr.domain.ports import PodManager, PodStartResult
from volundr.domain.session_startup import StartupStage

logger = logging.getLogger(__name__)

#: Prefix of the constructor kwargs of the Flux resident controller, which was removed.
_REMOVED_RESIDENT_KWARG_PREFIX = "resident_"


class FluxPodManager(
    FluxHelmReleases,
    BrokeredCredentialPodManager,
    PodManager,
):
    """Flux-native implementation of PodManager.

    Creates / deletes HelmRelease CRs via the Kubernetes API.
    Flux's helm-controller reconciles them into actual Helm releases.

    Constructor accepts plain kwargs (dynamic adapter pattern). The composition
    root hands every pod manager the same connection settings
    (``server_host``, ``gateway_endpoint``, ``token_url``, ...), which other pod
    managers use; they are accepted and ignored here. A ``resident_*`` kwarg is
    not one of them: it configured the removed Flux resident controller, so it
    is refused.
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
        codex_auth_adapter: str = "skuld.codex_auth.VolundrCodexAuthProvider",
        codex_auth_kwargs: dict | None = None,
        session_service_port: int = 8080,
        **_extra: object,
    ):
        """``session_service_port`` is the skuld chart's ``service.port``: the
        session Service's single entry point (Envoy when enabled)."""
        removed = sorted(key for key in _extra if key.startswith(_REMOVED_RESIDENT_KWARG_PREFIX))
        if removed:
            raise ValueError(
                "FluxPodManager: the Flux resident controller was removed, so its "
                f"podManager.kwargs {removed} configure nothing; delete them from "
                "podManager.kwargs (charts/volundr values)."
            )
        self._namespace = namespace
        self._session_service_port = session_service_port
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
        self._configure_brokered_credentials(
            codex_auth_adapter=codex_auth_adapter,
            codex_auth_kwargs=codex_auth_kwargs,
        )
        self._api_client = None

    async def _get_api(self):
        """Lazy-load kubernetes_asyncio custom objects API."""
        if self._api_client is None:
            self._api_client = await create_flux_api_client()
        from kubernetes_asyncio import client

        return client.CustomObjectsApi(self._api_client)

    async def _get_core_api(self):
        """CoreV1Api on the same client, for the session's pods and events."""
        await self._get_api()
        from kubernetes_asyncio import client

        return client.CoreV1Api(self._api_client)

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

    def initial_code_endpoint(self, session: Session) -> str | None:
        return self._code_endpoint(session.name, str(session.id))

    async def start(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> PodStartResult:
        """Create a HelmRelease CR for the session."""
        spec = self._with_brokered_credentials(spec)
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
        _inject_codex_auth_env(values)

        await self._apply_helmrelease(release_name, values)

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
        release_name = self._release_name(session)
        return await self._delete_helmrelease(release_name)

    async def status(self, session: Session) -> SessionStatus:
        """Read HelmRelease status conditions and map to SessionStatus."""
        release_name = self._release_name(session)
        obj = await self._get_helmrelease(release_name)
        if obj is None:
            if session.status == SessionStatus.STARTING:
                return SessionStatus.STARTING
            if session.status == SessionStatus.PROVISIONING:
                # The release was created (provisioning began) and is now gone:
                # Flux remediation uninstalled it after a failed install. It
                # will never come back — reporting STARTING here spins forever
                # and STOPPED would surface as a successful completion upstream.
                logger.error(
                    "HelmRelease %s vanished while session %s was provisioning "
                    "— install failed and Flux uninstalled it; marking FAILED",
                    release_name,
                    session.id,
                )
                return SessionStatus.FAILED
            return SessionStatus.STOPPED

        return self._map_status(obj)

    async def status_detail(self, session: Session) -> str | None:
        """Say why a session's pods are not running, from the cluster's own record.

        A failed install used to surface as "infrastructure reported failure":
        Flux uninstalls the release and its pods, taking the reason with them.
        Pod Warning events outlive the pods, so they still say what happened
        (e.g. FailedScheduling: Insufficient memory). A Ready release has no
        detail to report.
        """
        release_name = self._release_name(session)
        obj = await self._get_helmrelease(release_name)
        conditions = (obj or {}).get("status", {}).get("conditions", [])
        ready = next((c for c in conditions if c.get("type") == "Ready"), None)
        if ready is not None and ready.get("status") == "True":
            return None

        parts: list[str] = []
        warning = await self._latest_pod_warning(release_name)
        if warning:
            parts.append(warning)
        if ready is not None and ready.get("message"):
            parts.append(f"HelmRelease {ready.get('reason') or 'NotReady'}: {ready['message']}")
        return "; ".join(parts) or None

    @property
    def reports_startup_stages(self) -> bool:
        return True

    async def startup_stages(self, session: Session) -> tuple[StartupStage, ...] | None:
        """The launch's stages from its HelmRelease, its pod and the pod's events."""
        release_name = self._release_name(session)
        release = await self._get_helmrelease(release_name)
        if release is None:
            return ()
        core = await self._get_core_api()
        pod = await self._session_pod(core, release_name)
        events = []
        if pod is not None:
            listed = await self._read_events(
                core,
                field_selector=(f"involvedObject.kind=Pod,involvedObject.name={pod.metadata.name}"),
            )
            events = list(listed.items or [])
        return pod_startup_stages(release, pod, events)

    async def _session_pod(self, core, release_name: str):
        """The newest live pod of the session's Deployment (not its room gateway)."""
        pods = await core.list_namespaced_pod(
            self._namespace,
            label_selector=f"app.kubernetes.io/instance={release_name}",
        )
        mine = []
        for pod in pods.items or []:
            if pod.metadata.deletion_timestamp is not None:
                continue
            template_hash = (pod.metadata.labels or {}).get("pod-template-hash")
            owners = pod.metadata.owner_references or []
            if template_hash and any(
                owner.kind == "ReplicaSet" and owner.name == f"{release_name}-{template_hash}"
                for owner in owners
            ):
                mine.append(pod)
        if not mine:
            return None
        return max(mine, key=lambda pod: pod.metadata.creation_timestamp)

    async def _read_events(self, core, *, field_selector: str):
        try:
            return await core.list_namespaced_event(self._namespace, field_selector=field_selector)
        except Exception as exc:
            if getattr(exc, "status", None) != 403:
                raise
            raise RuntimeError(
                f"Völundr may not list events in namespace {self._namespace!r}, which it "
                "reads to say why a session did not start; grant events get/list there "
                "(the volundr chart's Role, or its ClusterRole with rbac.clusterWide)"
            ) from exc

    async def _latest_pod_warning(self, release_name: str) -> str | None:
        core = await self._get_core_api()
        events = await self._read_events(
            core, field_selector="type=Warning,involvedObject.kind=Pod"
        )
        prefix = f"{release_name}-"
        mine = [
            event
            for event in events.items or []
            if (event.involved_object.name or "").startswith(prefix)
        ]
        if not mine:
            return None
        latest = max(mine, key=_event_time)
        return f"pod {latest.involved_object.name}: {latest.reason}: {latest.message}"

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
                if str(event.get("type") or "").upper() == "DELETED":
                    # Flux uninstalled the release mid-provisioning (install
                    # failed and remediation tore it down). Terminal — fail
                    # now instead of watching a void until the timeout.
                    logger.error(
                        "HelmRelease %s was deleted while waiting for readiness "
                        "— install failed and Flux uninstalled it; marking FAILED",
                        release_name,
                    )
                    return SessionStatus.FAILED
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

        return await self.status(session)

    async def close(self) -> None:
        """Close the Kubernetes API client."""
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None

    def session_proxy_target(self, session: Session) -> SessionProxyTarget | None:
        """The session pod's own Service, for in-cluster hops to its Skuld.

        The browser reaches a session through the gateway; Forge-side hops (the
        live-preview router) dial this Service, where the pod's Envoy still
        authenticates every request. Only a running session has one.
        """
        if session.status is not SessionStatus.RUNNING:
            return None
        host = f"{self._release_name(session)}.{self._namespace}.svc.cluster.local"
        return SessionProxyTarget(
            service_url=f"http://{host}:{self._session_service_port}/",
            connect_host=host,
            connect_port=self._session_service_port,
        )


def _inject_workload_exchange_env(values: dict) -> None:
    """Propagate the workload token exchange URL (on ``volundr.apiUrl``) into all containers."""
    volundr_cfg = values.get("volundr")
    if not isinstance(volundr_cfg, dict):
        return
    api_url = str(volundr_cfg.get("apiUrl") or "").rstrip("/")
    if not api_url:
        return
    env_entry = {
        "name": "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
        "value": f"{api_url}/api/v1/tokens/workload/exchange",
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


def _inject_codex_auth_env(values: dict) -> None:
    """Give every session container the Codex broker, not just the broker pod.

    A sidecar container that runs the same Codex transport Skuld does but
    builds it itself needs the same SKULD__CODEX_AUTH__* contract. Reaching
    only the main container leaves it opening the Codex websocket with no
    credential — 401 on every turn, while the session itself looks healthy.
    """
    environment = BrokeredCredentialPodManager._brokered_credential_environment_values(values)
    if not environment:
        return

    entries = [{"name": name, "value": value} for name, value in sorted(environment.items())]

    env_vars = list(values.get("envVars") or [])
    for entry in entries:
        if not _has_env(env_vars, entry["name"]):
            env_vars.append(dict(entry))
    values["envVars"] = env_vars

    extra_containers = values.get("extraContainers")
    if not isinstance(extra_containers, list):
        return
    for container in extra_containers:
        if not isinstance(container, dict):
            continue
        container_env = list(container.get("env") or [])
        for entry in entries:
            if _has_env(container_env, entry["name"]):
                continue
            container_env.append(dict(entry))
        container["env"] = container_env


def _has_env(env: list, name: str) -> bool:
    return any(isinstance(entry, dict) and entry.get("name") == name for entry in env)


def _event_time(event: Any) -> datetime:
    """When a Kubernetes event last happened; events carry one of three stamps."""
    stamp = event.last_timestamp or event.event_time or event.first_timestamp
    if stamp is None:
        return datetime.min.replace(tzinfo=UTC)
    return stamp
