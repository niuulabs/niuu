"""Flux HelmRelease adapter for session pod management.

Creates HelmRelease custom resources in Kubernetes, which Flux
reconciles into running session pods. Selected via the dynamic
adapter pattern in config YAML.
"""

import copy
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niuu.adapters.flux import (
    HELMRELEASE_GROUP,
    HELMRELEASE_PLURAL,
    HELMRELEASE_VERSION,
    FluxHelmReleases,
    create_flux_api_client,
)
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.hosted_member_harness import (
    DEFAULT_POLL_SECONDS,
    DEFAULT_REQUEST_SECONDS,
    DEFAULT_RESTART_WAIT_SECONDS,
    MemberHarnessControl,
)
from volundr.adapters.outbound.k8s_startup_stages import pod_startup_stages
from volundr.adapters.outbound.mesh_settings import require_unit_mesh
from volundr.domain.models import (
    HOSTED_MEMBERS_KEY,
    Session,
    SessionSpec,
    SessionStatus,
    _deep_merge,
)
from volundr.domain.ports import HostedMemberLaunch, PodManager, PodStartResult
from volundr.domain.services.forge_session_launch import launch_id_of
from volundr.domain.session_startup import StartupStage

logger = logging.getLogger(__name__)

#: Prefix of the constructor kwargs of the Flux resident controller, which was removed.
_REMOVED_RESIDENT_KWARG_PREFIX = "resident_"

#: Reasons a container waits that no amount of waiting will end.
_FAILED_WAITING_REASONS = frozenset(
    {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"}
)

#: The skuld chart's name for the container of member ``index`` (``skuld.memberContainerName``).
_MEMBER_CONTAINER = "member-{index}"

#: Values that belong to the pod, which the root's release alone decides. A member renders as a
#: container of that pod, so a member launched with a different one cannot be honoured, and the
#: unit refuses it rather than running the member somewhere it did not ask for.
_POD_LEVEL_VALUES: tuple[str, ...] = (
    "nodeSelector",
    "tolerations",
    "affinity",
    "runtimeClassName",
    "serviceAccountName",
    "securityContext",
    "imagePullSecrets",
    "homeVolume.enabled",
    "homeVolume.existingClaim",
    "homeVolume.mountPath",
    "homeVolume.persistentTmp",
    "homeVolume.credentialFiles.secretName",
    "homeVolume.credentialFiles.secretMountPath",
    "homeVolume.credentialFiles.destDir",
    "persistence.enabled",
    "persistence.emptyDir",
    "persistence.existingClaim",
    "persistence.mountPath",
    "secretManifest",
)

#: What a member's launch may not ask the pod for beyond its own container: containers, init
#: containers and a service account of its own.
_MEMBER_POD_ADDITIONS: tuple[str, ...] = ("init_containers", "extra_containers", "service_account")


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
        unit_mesh_dir: str = "/tmp/niuu-mesh",
        hosted_member_restart_wait_seconds: float = DEFAULT_RESTART_WAIT_SECONDS,
        hosted_member_poll_seconds: float = DEFAULT_POLL_SECONDS,
        hosted_member_request_seconds: float = DEFAULT_REQUEST_SECONDS,
        **_extra: object,
    ):
        """``session_service_port`` is the skuld chart's ``service.port``: the
        session Service's single entry point (Envoy when enabled).

        ``unit_mesh_dir`` is where the Skulds of a unit (a workflow run's pod) share their
        local mesh: one ``emptyDir`` mounted there in every container, holding the registry
        file they discover each other through."""
        removed = sorted(key for key in _extra if key.startswith(_REMOVED_RESIDENT_KWARG_PREFIX))
        if removed:
            raise ValueError(
                "FluxPodManager: the Flux resident controller was removed, so its "
                f"podManager.kwargs {removed} configure nothing; delete them from "
                "podManager.kwargs (charts/volundr values)."
            )
        self._namespace = namespace
        self._session_service_port = session_service_port
        if not unit_mesh_dir.startswith("/"):
            raise ValueError(
                f"FluxPodManager: unit_mesh_dir must be an absolute path: {unit_mesh_dir!r}"
            )
        self._unit_mesh_dir = unit_mesh_dir.rstrip("/")
        self._harness = MemberHarnessControl(
            restart_wait_seconds=hosted_member_restart_wait_seconds,
            poll_seconds=hosted_member_poll_seconds,
            request_seconds=hosted_member_request_seconds,
        )
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
        """The HelmRelease the session runs in: its own, or its unit root's for a member."""
        return f"skuld-{session.hosted_by or session.id}"

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
        """Create a HelmRelease CR for the session.

        A unit root (a workflow run) is one release for the whole run: the root's own Skuld
        plus a container per member, all started with the pod.
        """
        if session.hosted_by is not None:
            raise ValueError(
                f"Session {session.id} is a member of unit {session.hosted_by}: it runs in "
                "that unit's pod and starts with it (start the unit's root session)"
            )
        members = await self.hosted_member_launches(session)
        self._require_members(session, members)
        spec = self._with_brokered_credentials(spec)
        release_name = self._release_name(session)

        values = self._release_values(spec)
        if members:
            self._add_unit(session, values, members)

        _inject_workload_exchange_env(values)
        _inject_codex_auth_env(values)

        await self._apply_helmrelease(release_name, values)

        logger.info(
            "Created HelmRelease %s in namespace %s%s",
            release_name,
            self._namespace,
            f" hosting {len(members)} member session(s)" if members else "",
        )

        return PodStartResult(
            chat_endpoint=self._chat_endpoint(session.name, str(session.id)),
            code_endpoint=self._code_endpoint(session.name, str(session.id)),
            pod_name=release_name,
        )

    def _release_values(self, spec: SessionSpec) -> dict:
        """The chart values one session's launch asks for: the defaults under its spec."""
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
        return values

    # -- units: one pod for a whole workflow run -----------------------------------------

    @staticmethod
    def _require_members(root: Session, members: Sequence[HostedMemberLaunch]) -> None:
        """The members Forge launched are exactly the ones it recorded as the unit's.

        A unit root whose recorded members did not arrive would start as a pod with only the
        root's Skuld, and the run's members would never come up: refused instead.
        """
        recorded = _recorded_members(root)
        if not members:
            if recorded:
                raise ValueError(
                    f"Unit {root.id} was started with members {sorted(recorded)} but no "
                    "launch for them came from the member source: "
                    "Forge must render each member's spec before it starts the root"
                )
            return
        if not root.is_unit_root:
            raise ValueError(
                f"Session {root.id} was launched with {len(members)} member(s) but is not a "
                "unit root (created with hosts_members)"
            )
        launched = {}
        for launch in members:
            member = launch.session
            if member.hosted_by != root.id:
                raise ValueError(
                    f"Session {member.id} is launched in unit {root.id} but is hosted by "
                    f"{member.hosted_by}"
                )
            if member.hosted_port is None:
                raise ValueError(f"Member {member.id} of unit {root.id} has no broker port")
            if member.id in launched:
                raise ValueError(f"Member {member.id} is launched twice in unit {root.id}")
            launched[member.id] = member.hosted_port
        if launched != recorded:
            raise ValueError(
                f"Unit {root.id} recorded members {sorted(recorded.items())} when it started "
                f"but was launched with {sorted(launched.items())}: a run's members are fixed "
                "when its unit starts"
            )
        ports = list(launched.values())
        if len(set(ports)) != len(ports):
            raise ValueError(f"Unit {root.id} gives two members the same broker port: {ports}")

    def _add_unit(
        self,
        root: Session,
        values: dict,
        members: Sequence[HostedMemberLaunch],
    ) -> None:
        """Add the unit to the root's release values: a Skuld container per member.

        Every Skuld of the pod (the root's and each member's) gets the same local mesh: nng
        on loopback, discovered through one registry file in a directory the pod shares. A
        member's complete values travel under ``members``; the chart renders its container,
        configuration and routes from them. A member's volumes are the pod's, under names
        only that member's mounts use, so its credential Secret is mounted into its own
        container and no other.
        """
        mesh_dir = self._unit_mesh_dir
        require_unit_mesh(
            values.get("mesh") or {},
            unit_id=root.id,
            where="the release values (values.mesh, from the session defaults or the launch)",
        )
        _use_unit_mesh(values, mesh_dir)
        values["unit"] = {"meshDir": mesh_dir}
        pod_volumes = list(values.get("extraVolumes") or [])
        entries: list[dict] = []
        for index, launch in enumerate(members):
            member = launch.session
            member_values = self._member_values(root, values, index, launch)
            pod_volumes.extend(member_values.pop("extraVolumes", []))
            entries.append(
                {
                    "id": str(member.id),
                    "port": member.hosted_port,
                    "values": member_values,
                }
            )
        if pod_volumes:
            values["extraVolumes"] = pod_volumes
        values["members"] = entries
        checkout = _unit_checkout(root, values, entries)
        if checkout:
            values["unit"]["checkoutFrom"] = checkout

    def _member_values(
        self, root: Session, root_values: dict, index: int, launch: HostedMemberLaunch
    ) -> dict:
        """The values member ``index`` would have been launched with on its own."""
        member = launch.session
        where = f"member {member.id} of unit {root.id}"
        additions = launch.spec.pod_spec
        for field in _MEMBER_POD_ADDITIONS:
            if additions is not None and getattr(additions, field):
                raise ValueError(
                    f"{where} asks for {field} of its own; a member is a container of its "
                    "unit's pod and cannot add pod-level parts"
                )
        values = self._release_values(self._with_brokered_credentials(launch.spec))
        _inject_workload_exchange_env(values)
        _inject_codex_auth_env(values)
        for key in _POD_LEVEL_VALUES:
            wanted, unit = _dotted(values, key), _dotted(root_values, key)
            if wanted != unit:
                raise ValueError(
                    f"{where} was launched with {key}={wanted!r} but its unit's pod has "
                    f"{unit!r}: members share one pod, so they must agree on it"
                )
        for key in ("podLabels", "podAnnotations"):
            for name, value in (values.get(key) or {}).items():
                if (root_values.get(key) or {}).get(name) != value:
                    raise ValueError(
                        f"{where} asks for {key}[{name!r}]={value!r}, which its unit's pod "
                        "does not have: members share one pod"
                    )
        _use_unit_mesh(values, self._unit_mesh_dir)
        values["session"] = {**(values.get("session") or {}), "id": str(member.id)}
        values["extraVolumes"] = _own_volumes(values, index)
        return values

    async def stop_hosted_member(self, member: Session) -> None:
        """End one member's harness: its container lives with the pod, its Skuld is asked."""
        await self._harness.stop(member, await self.broker_target(member))

    async def restart_hosted_member(self, member: Session) -> None:
        """Restart one member's harness under its new launch.

        The member's Secret is rewritten with the credential of the new launch (building the
        launch does it), then its Skuld replaces its process once the volume carries it.
        """
        launch = await self.hosted_member_launch(member)
        await self._harness.restart(
            member,
            await self.broker_target(member),
            launch_id_of(launch.session.workload_config) or "",
        )

    async def stop(self, session: Session) -> bool:
        """Delete the HelmRelease CR for the session.

        A member of a unit has no release or pod of its own: it is a container of its unit's
        pod, which stops with the unit (stopping the root). Deleting the unit's release for
        one member would end every other member too, so a member's stop removes nothing and
        says so; Forge ends the member's own session record itself.
        """
        if session.hosted_by is not None:
            return False
        release_name = self._release_name(session)
        return await self._delete_helmrelease(release_name)

    async def status(self, session: Session) -> SessionStatus:
        """Read HelmRelease status conditions and map to SessionStatus.

        A member of a unit answers from its own container in the unit's pod, so a sweep
        that checks every running row keeps a live member and finds a dead one.
        """
        release_name = self._release_name(session)
        obj = await self._get_helmrelease(release_name)
        if session.hosted_by is not None and obj is not None:
            return await self._member_status(session, release_name, obj)
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

    async def _member_status(
        self, member: Session, release_name: str, release: dict
    ) -> SessionStatus:
        """A member's status from its container in the unit's pod."""
        unit_status = self._map_status(release)
        if unit_status != SessionStatus.RUNNING:
            return unit_status
        index = _member_index(release, member)
        if index is None:
            return SessionStatus.STOPPED
        core = await self._get_core_api()
        pod = await self._session_pod(core, release_name)
        if pod is None:
            return SessionStatus.STARTING
        return _container_status(pod, _MEMBER_CONTAINER.format(index=index))

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
            if session.hosted_by is None or obj is None:
                return None
            return await self._member_detail(session, release_name, obj)

        parts: list[str] = []
        warning = await self._latest_pod_warning(release_name)
        if warning:
            parts.append(warning)
        if ready is not None and ready.get("message"):
            parts.append(f"HelmRelease {ready.get('reason') or 'NotReady'}: {ready['message']}")
        return "; ".join(parts) or None

    async def _member_detail(self, member: Session, release_name: str, release: dict) -> str | None:
        """Why a member's container is not running, from its state in the unit's pod."""
        index = _member_index(release, member)
        if index is None:
            return f"session {member.id} is not one of the members its unit was started with"
        core = await self._get_core_api()
        pod = await self._session_pod(core, release_name)
        if pod is None:
            return None
        container = _MEMBER_CONTAINER.format(index=index)
        state = _container_state(pod, container)
        if state is None or state.running is not None:
            return None
        detail = state.waiting or state.terminated
        reason = getattr(detail, "reason", None) or "NotRunning"
        message = getattr(detail, "message", None) or ""
        return f"container {container} of {pod.metadata.name}: {reason} {message}".strip()

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
        """The newest live pod of the session's Deployment."""
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
        # A member shares its unit's Service and is reached at /m/<its id>/ (the pod's nginx
        # sends it to the member's own Skuld), under the same Envoy and owner check.
        path = f"/m/{session.id}/" if session.hosted_by is not None else "/"
        return SessionProxyTarget(
            service_url=f"http://{host}:{self._session_service_port}{path}",
            connect_host=host,
            connect_port=self._session_service_port,
        )

    async def broker_target(self, session: Session) -> SessionProxyTarget | None:
        """Where Forge reaches the broker of a member of a unit.

        A member's chat endpoint carries the unit's address and the member's port, which a
        pod reached through the gateway does not serve; Forge dials the member's path on the
        unit's own Service instead. An ordinary session keeps its chat endpoint (None).
        """
        if session.hosted_by is None:
            return None
        target = self.session_proxy_target(session)
        if target is None:
            raise ValueError(
                f"Session {session.id} is a member of unit {session.hosted_by}, which is not "
                "running: it has no broker to reach"
            )
        return target


def _member_index(release: dict, member: Session) -> int | None:
    """Where the unit's HelmRelease lists ``member`` (its container is ``member-<index>``)."""
    entries = (release.get("spec") or {}).get("values", {}).get("members") or []
    for index, entry in enumerate(entries):
        if str(entry.get("id")) == str(member.id):
            return index
    return None


def _container_state(pod: Any, container: str) -> Any:
    """The current state of ``container`` in ``pod``; None before the kubelet reports it."""
    for status in pod.status.container_statuses or []:
        if status.name == container:
            return status.state
    return None


def _container_status(pod: Any, container: str) -> SessionStatus:
    """A member's session status from its container's state."""
    state = _container_state(pod, container)
    if state is None:
        return SessionStatus.STARTING
    if state.running is not None:
        return SessionStatus.RUNNING
    if state.terminated is not None:
        return SessionStatus.STOPPED if state.terminated.exit_code == 0 else SessionStatus.FAILED
    waiting_reason = getattr(state.waiting, "reason", "") or ""
    if waiting_reason in _FAILED_WAITING_REASONS:
        return SessionStatus.FAILED
    return SessionStatus.STARTING


def _recorded_members(root: Session) -> dict[UUID, int]:
    """The members Forge recorded on a unit root when the unit started: id -> broker port."""
    entries = root.workload_config.get(HOSTED_MEMBERS_KEY) or []
    return {UUID(str(entry["id"])): int(entry["port"]) for entry in entries}


def _dotted(values: Mapping[str, Any], path: str) -> Any:
    """``values`` at a dotted path, None when any step of it is absent."""
    current: Any = values
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _use_unit_mesh(values: dict, mesh_dir: str) -> None:
    """Point a Skuld's mesh at the unit's own: nng on loopback, a registry file in ``mesh_dir``.

    The Skulds of one pod are the whole mesh, so each listens on loopback (the OS picks the
    port) and finds the others through a file in the directory the pod shares: nothing
    outside the pod can reach a mesh port, and no pod listing is needed. A mesh that is off,
    or not on nng, is the operator's choice and is left as it is.
    """
    mesh = values.get("mesh")
    if not isinstance(mesh, dict) or not mesh.get("enabled"):
        return
    if str(mesh.get("transport") or "nng") != "nng":
        return
    mesh["discoveryAdapters"] = [{"adapter": "registry", "path": f"{mesh_dir}/peers.json"}]
    mesh["adapters"] = None
    mesh["nng"] = {
        **(mesh.get("nng") or {}),
        "pubSubAddress": "tcp://127.0.0.1:0",
        "reqRepAddress": "tcp://127.0.0.1:0",
        "advertiseHost": "",
    }


def _own_volumes(values: dict, index: int) -> list[dict]:
    """The member's volumes under names only its own mounts use; its mounts follow.

    A volume the chart owns (``home``, ``sessions``...) is not the member's to rename, so only
    the names the member itself declares change. Two members (or a member and the root) that
    declare the same name therefore cannot collide.
    """
    volumes = [dict(volume) for volume in values.get("extraVolumes") or []]
    renamed = {str(volume["name"]): f"member-{index}-{volume['name']}" for volume in volumes}
    for volume in volumes:
        volume["name"] = renamed[str(volume["name"])]
    values["extraVolumeMounts"] = [
        {**mount, "name": renamed.get(str(mount.get("name")), mount.get("name"))}
        for mount in values.get("extraVolumeMounts") or []
    ]
    return volumes


def _unit_checkout(root: Session, root_values: dict, entries: list[dict]) -> str | None:
    """Which member the unit's one checkout is cloned from, when the root names no repository.

    A run works on one repository and branch, shared by every member. The root's own wins when
    it names one; otherwise the members' must agree, and the chart clones what the first one
    that names a repository names. A member that names another repository or branch is
    refused: it would otherwise work in a checkout of something it did not ask for.
    """
    wanted = {}
    for entry in entries:
        git = (entry["values"].get("git") or {}) if isinstance(entry["values"], dict) else {}
        if git.get("repoUrl"):
            wanted[entry["id"]] = (
                git.get("repoUrl"),
                git.get("branch") or "main",
                git.get("baseBranch") or "",
            )
    root_git = root_values.get("git") or {}
    if root_git.get("repoUrl"):
        root_wanted = (
            root_git.get("repoUrl"),
            root_git.get("branch") or "main",
            root_git.get("baseBranch") or "",
        )
        for member_id, member_wanted in wanted.items():
            if member_wanted != root_wanted:
                raise ValueError(
                    f"Member {member_id} of unit {root.id} was launched on {member_wanted} but "
                    f"the unit's one checkout is {root_wanted}: a run's members share a "
                    "repository and branch"
                )
        return None
    if len(set(wanted.values())) > 1:
        raise ValueError(
            f"Members of unit {root.id} were launched on different repositories or branches "
            f"{sorted(set(wanted.values()))}: a run's members share one checkout"
        )
    return next(iter(wanted), None)


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
