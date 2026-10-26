"""Flux HelmRelease adapter for pod management.

Creates HelmRelease custom resources in Kubernetes, which Flux
reconciles into running session pods. Selected via the dynamic
adapter pattern in config YAML.
"""

import base64
import copy
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from volundr.adapters.outbound.k8s_storage_adapter import build_pvc_manifest
from volundr.adapters.outbound.local_resident_storage import LOG_LEVELS, resident_log_level
from volundr.adapters.outbound.resident_container_spec import (
    resident_flock_labels,
    resident_flock_profile_configured,
    resident_mesh_pod_metadata,
    resident_openclaw_shim_enabled,
)
from volundr.domain.models import (
    LABEL_MANAGED_BY,
    LABEL_PVC_TYPE,
    CredentialMapping,
    ResidentBackend,
    ResidentCondition,
    ResidentConditionStatus,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentEngine,
    ResidentLogEntry,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
    Session,
    SessionSpec,
    SessionStatus,
    _deep_merge,
)
from volundr.domain.ports import (
    CredentialStorePort,
    IntegrationRepository,
    PodManager,
    PodStartResult,
    ResidentLogsUnavailableError,
    ResidentOpenClawShimHost,
    ResidentOpenClawTokens,
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeObservation,
    SecretInjectionPort,
)

logger = logging.getLogger(__name__)

LABEL_RESIDENT_ID = "niuu.world/resident-id"
# Deliberately not volundr/owner: the resident pod does not carry that label
# (Valhalla's session podAffinity co-locates a user's skuld pods by it), so an
# owner-labelled claim would fail the PVC isolation policy (charts/volundr
# kyverno-pvc-isolation.yaml).
LABEL_RESIDENT_OWNER_ID = "niuu.world/owner-id"
LABEL_RESIDENT_TENANT_ID = "niuu.world/tenant-id"
# A resident's own volumes, keyed by the profile's deployment.volumes block.
RESIDENT_VOLUME_KINDS = ("home", "workspace")
RESIDENT_VOLUME_SETTINGS = frozenset({"storageClass", "size", "accessMode"})
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
#: Key of a Ravn resident's own OpenClaw shim token Secret (resident.openclaw.tokenSecret).
RESIDENT_OPENCLAW_TOKEN_KEY = "token"
#: Protocol a resident's voice channel speaks (ravn.adapters.channels.voice): the
#: agent-service wire of realtime voice clients, served under /voice/ on the resident's route.
RESIDENT_VOICE_PROTOCOL = "agent-service-voice-v1"
#: The resident pod container its declared credentials are rendered into: the Ravn
#: daemon, which holds the persona, its tools and the voice channel.
RESIDENT_CREDENTIAL_CONTAINERS = "ravn"
#: Where the OpenBao agent renders injected files in the pod (its shared secret volume).
RESIDENT_SECRET_ROOT = "/run/secrets/"
#: The platform version (chart and images) Völundr last rendered into a resident's release.
ANNOTATION_PLATFORM_VERSION = "niuu.world/platform-version"
#: The version that release replaced. A Völundr that still renders it (the old replica
#: while Völundr itself rolls out) must not move the resident back to it.
ANNOTATION_REPLACED_PLATFORM_VERSION = "niuu.world/replaced-platform-version"
#: Which of the owner's credentials each integration a resident declares was bound to
#: when it was deployed (``{slug: credential name}``). Every later render reuses it:
#: following the platform, restart and resume run without the owner's login, which
#: reading the owner's connections needs.
ANNOTATION_RESIDENT_INTEGRATIONS = "niuu.world/resident-integrations"


@dataclass(frozen=True)
class RenderedResident:
    """A resident's release values and the integration credentials they were bound to."""

    values: dict[str, Any]
    integrations: dict[str, str]


@dataclass(frozen=True)
class ResidentVolumeClaim:
    """One PVC this controller creates and deletes for a single resident."""

    kind: str
    name: str
    size: str
    storage_class: str
    access_mode: str


class FluxPodManager(
    FluxHelmReleases,
    BrokeredCredentialPodManager,
    PodManager,
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentOpenClawShimHost,
):
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
        codex_auth_adapter: str = "skuld.codex_auth.VolundrCodexAuthProvider",
        codex_auth_kwargs: dict | None = None,
        resident_platform_base_url: str = "",
        resident_triggers_enabled: bool = False,
        resident_budget_enabled: bool = False,
        resident_home_size: str = "1Gi",
        resident_workspace_size: str = "2Gi",
        resident_storage_class: str = "",
        resident_access_mode: str = "ReadWriteOnce",
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
        # Every managed resident gets platform reachability so its own
        # triggers/budget reporting (resident.triggers / resident.budget in
        # the skuld chart) can work — this is baseline platform
        # connectivity, not a Simple-mode-specific concern. Empty base URL
        # (the default) means the operator has not wired this controller to
        # a platform yet, so residents get no platform block at all rather
        # than one with an empty, unreachable base_url.
        self._resident_platform_base_url = resident_platform_base_url
        self._resident_triggers_enabled = resident_triggers_enabled
        self._resident_budget_enabled = resident_budget_enabled
        # Where each Ravn resident's own OpenClaw shim token is kept, handed over by
        # the Ravn session controller (set_resident_openclaw_tokens). None: no Ravn
        # sessions on this backend.
        self._resident_openclaw_tokens: ResidentOpenClawTokens | None = None
        # The owner's credentials a profile declares for its residents (deployment.credentials),
        # injected through the same OpenBao agent as session credentials.
        self._secret_injection: SecretInjectionPort | None = None
        self._integrations: IntegrationRepository | None = None
        self._credential_store: CredentialStorePort | None = None
        # Defaults for each resident's own home and workspace PVCs; a profile
        # overrides them per volume in deployment.volumes.<home|workspace>.
        # An empty storage class means the cluster's default class.
        self._resident_volume_defaults = {
            "home": {
                "size": resident_home_size,
                "storageClass": resident_storage_class,
                "accessMode": resident_access_mode,
            },
            "workspace": {
                "size": resident_workspace_size,
                "storageClass": resident_storage_class,
                "accessMode": resident_access_mode,
            },
        }
        if not resident_platform_base_url and (
            resident_triggers_enabled or resident_budget_enabled
        ):
            # Without a base_url, _resident_values below never renders a
            # "platform" block at all — resident_triggers_enabled/
            # resident_budget_enabled would be silently ignored rather than
            # doing what the operator who set them clearly wants (residents
            # that execute triggers and report spend). Configured-but-
            # impossible is fatal, not a silent no-op. See no-fallbacks.md.
            raise ValueError(
                "FluxPodManager: resident_triggers_enabled/resident_budget_enabled "
                "require resident_platform_base_url to also be set — without it, "
                "residents get no platform block and these flags do nothing. Set "
                "podManager.kwargs.resident_platform_base_url (charts/volundr) "
                "or clear both flags."
            )
        self._configure_brokered_credentials(
            codex_auth_adapter=codex_auth_adapter,
            codex_auth_kwargs=codex_auth_kwargs,
        )
        self._api_client = None
        self._realm_repository = None
        # Residents whose move to a platform version failed in this Völundr:
        # resident id -> (target version, reason). Not retried until the target
        # changes, an explicit restart or resume, or Völundr restarts.
        self._failed_platform_upgrades: dict[str, tuple[str, str]] = {}

    def set_realm_repository(self, repository) -> None:
        """Enable resolving a resident's realm slug for its Helm values.

        Optional: without it, deploying a resident with ``realm_id`` set
        raises in ``_resolve_realm_slug`` rather than silently deploying with
        no realm/charter binding.
        """
        self._realm_repository = repository

    async def _resolve_realm_slug(self, runtime: ResidentRuntime) -> str:
        """Return the slug of the realm this resident is bound to, or "".

        Mirrors LocalContainerResidentRuntimeController._resolve_realm_slug.
        """
        if runtime.realm_id is None:
            return ""
        if self._realm_repository is None:
            raise RuntimeError(
                f"resident {runtime.name!r} has realm_id={runtime.realm_id} but this "
                "controller has no realm repository configured; call set_realm_repository "
                "at composition time."
            )
        realm = await self._realm_repository.get_realm(runtime.realm_id)
        if realm is None:
            raise RuntimeError(
                f"resident {runtime.name!r} names realm_id={runtime.realm_id}, but no "
                "such realm exists; clear the resident's realm binding or restore the realm."
            )
        return realm.slug

    async def _get_api(self):
        """Lazy-load kubernetes_asyncio custom objects API."""
        if self._api_client is None:
            self._api_client = await create_flux_api_client()
        from kubernetes_asyncio import client

        return client.CustomObjectsApi(self._api_client)

    async def _get_core_api(self):
        """CoreV1Api on the same client, for a resident's own PVCs."""
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

    async def _latest_pod_warning(self, release_name: str) -> str | None:
        core = await self._get_core_api()
        try:
            events = await core.list_namespaced_event(
                self._namespace,
                field_selector="type=Warning,involvedObject.kind=Pod",
            )
        except Exception as exc:
            if getattr(exc, "status", None) != 403:
                raise
            raise RuntimeError(
                f"Völundr may not list events in namespace {self._namespace!r}, which it "
                "reads to say why a session did not start; grant events get/list there "
                "(the volundr chart's Role, or its ClusterRole with rbac.clusterWide)"
            ) from exc
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

    @property
    def backend(self) -> ResidentBackend:
        return ResidentBackend.HELMRELEASE

    def supports(self, profile: ResidentDeploymentProfile) -> bool:
        if (
            profile.backend is not ResidentBackend.HELMRELEASE
            or profile.engine is not ResidentEngine.RAVN
        ):
            return False
        values = profile.deployment.get("values") or {}
        return isinstance(values, dict) and resident_flock_profile_configured(profile, values)

    @staticmethod
    def _resident_release_name(runtime: ResidentRuntime) -> str:
        return f"resident-{runtime.id}"

    def _resident_service_account_values(self, runtime: ResidentRuntime) -> dict[str, Any]:
        """serviceAccountName for a resident's own workload identity.

        Only set when this controller has a platform to authenticate to
        (self._resident_platform_base_url) — a resident that never calls the
        platform has no need of a dedicated ServiceAccount or the RBAC that
        comes with one. When set, it is the *same* "resident-<uuid>" name as
        the release itself (_resident_release_name) — the chart's
        serviceaccount.yaml creates a ServiceAccount by this exact name (see
        charts/skuld/templates/serviceaccount.yaml), and the platform's
        workload-identity mapping's owner_id_claim_pattern
        (workloadIdentity.residentMapping in charts/volundr) extracts this
        same runtime id back out of the verified
        "system:serviceaccount:<namespace>:resident-<uuid>" subject — so
        GET/POST /api/v1/ravn/budget/* key rows by resident_runtimes.id, the
        same id the web already passes, instead of one id shared by every
        resident that matches a bare subject_prefix mapping.
        """
        if not self._resident_platform_base_url:
            return {}
        return {"serviceAccountName": self._resident_release_name(runtime)}

    def _backend_ref(self, runtime: ResidentRuntime) -> dict[str, Any]:
        release_name = self._resident_release_name(runtime)
        return {
            "apiVersion": f"{HELMRELEASE_GROUP}/{HELMRELEASE_VERSION}",
            "kind": "HelmRelease",
            "namespace": self._namespace,
            "name": release_name,
            "workloadSelector": f"app.kubernetes.io/instance={release_name}",
        }

    def _endpoints(
        self, runtime: ResidentRuntime, *, voice: bool = False
    ) -> list[ResidentEndpoint]:
        endpoints = [
            ResidentEndpoint(
                kind="chat",
                protocol="skuld-v1",
                url=self._chat_endpoint(str(runtime.id), str(runtime.id)),
            )
        ]
        if voice:
            endpoints.append(
                ResidentEndpoint(
                    kind="voice",
                    protocol=RESIDENT_VOICE_PROTOCOL,
                    url=self._voice_endpoint(runtime),
                )
            )
        return endpoints

    def _voice_endpoint(self, runtime: ResidentRuntime) -> str:
        if self._gateway_domain:
            return f"https://{self._gateway_domain}/s/{runtime.id}/voice"
        return f"{self._code_scheme}://{self._session_host(str(runtime.id))}/voice"

    @staticmethod
    def _values_enable_voice(values: Any) -> bool:
        """Whether rendered chart values turn the resident's voice channel on."""
        resident = values.get("resident") if isinstance(values, dict) else None
        voice = resident.get("voice") if isinstance(resident, dict) else None
        return isinstance(voice, dict) and voice.get("enabled") is True

    @staticmethod
    def _resident_claim_name(runtime: ResidentRuntime, kind: str) -> str:
        return f"resident-{runtime.id}-{kind}"

    def _resident_claims(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> list[ResidentVolumeClaim]:
        """The PVCs this resident owns: its own home and its own workspace.

        A profile that names its own ``existingClaim`` (or turns the volume
        off, or uses ``persistence.emptyDir``) keeps that choice, and no PVC
        is created for that volume.
        """
        values = profile.deployment.get("values") or {}
        if not isinstance(values, dict):
            raise ValueError(f"Resident profile {profile.id} deployment.values must be an object")
        volumes = profile.deployment.get("volumes") or {}
        if not isinstance(volumes, dict):
            raise ValueError(f"Resident profile {profile.id} deployment.volumes must be an object")
        unknown = set(volumes) - set(RESIDENT_VOLUME_KINDS)
        if unknown:
            raise ValueError(
                f"Resident profile {profile.id} deployment.volumes has unknown "
                f"volumes {sorted(unknown)}; expected {list(RESIDENT_VOLUME_KINDS)}"
            )

        claims: list[ResidentVolumeClaim] = []
        if self._resident_owns_volume(values.get("homeVolume"), profile, "homeVolume"):
            claims.append(self._resident_claim(runtime, profile, "home", volumes.get("home")))
        if self._resident_owns_volume(values.get("persistence"), profile, "persistence"):
            claims.append(
                self._resident_claim(runtime, profile, "workspace", volumes.get("workspace"))
            )
        return claims

    @staticmethod
    def _resident_owns_volume(
        configured: object,
        profile: ResidentDeploymentProfile,
        key: str,
    ) -> bool:
        if configured is None:
            return True
        if not isinstance(configured, dict):
            raise ValueError(
                f"Resident profile {profile.id} deployment.values.{key} must be an object"
            )
        if configured.get("enabled") is False:
            return False
        if configured.get("emptyDir"):
            return False
        return not configured.get("existingClaim")

    def _resident_claim(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        kind: str,
        configured: object,
    ) -> ResidentVolumeClaim:
        configured = configured or {}
        if not isinstance(configured, dict):
            raise ValueError(
                f"Resident profile {profile.id} deployment.volumes.{kind} must be an object"
            )
        unknown = set(configured) - RESIDENT_VOLUME_SETTINGS
        if unknown:
            raise ValueError(
                f"Resident profile {profile.id} deployment.volumes.{kind} has unknown "
                f"settings {sorted(unknown)}; expected {sorted(RESIDENT_VOLUME_SETTINGS)}"
            )
        settings = {**self._resident_volume_defaults[kind], **configured}
        for setting in ("size", "accessMode"):
            if not str(settings[setting] or ""):
                raise ValueError(
                    f"Resident profile {profile.id} deployment.volumes.{kind}.{setting} "
                    "must not be empty"
                )
        return ResidentVolumeClaim(
            kind=kind,
            name=self._resident_claim_name(runtime, kind),
            size=str(settings["size"]),
            storage_class=str(settings["storageClass"] or ""),
            access_mode=str(settings["accessMode"]),
        )

    @staticmethod
    def _resident_claim_values(claims: list[ResidentVolumeClaim]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for claim in claims:
            key = "homeVolume" if claim.kind == "home" else "persistence"
            values[key] = {"enabled": True, "existingClaim": claim.name}
        return values

    async def _ensure_resident_claims(
        self,
        runtime: ResidentRuntime,
        claims: list[ResidentVolumeClaim],
    ) -> None:
        """Create the resident's PVCs; an existing one must already be its own."""
        if not claims:
            return
        api = await self._get_core_api()
        for claim in claims:
            manifest = build_pvc_manifest(
                name=claim.name,
                namespace=self._namespace,
                storage=claim.size,
                storage_class=claim.storage_class or None,
                access_mode=claim.access_mode,
                labels={
                    LABEL_MANAGED_BY: self._managed_by,
                    # Not "home"/"workspace": those are the user home and
                    # session workspaces K8sStorageAdapter lists and manages.
                    LABEL_PVC_TYPE: f"resident-{claim.kind}",
                    LABEL_RESIDENT_ID: str(runtime.id),
                    LABEL_RESIDENT_OWNER_ID: runtime.owner_id,
                    LABEL_RESIDENT_TENANT_ID: runtime.tenant_id,
                },
            )
            try:
                await api.create_namespaced_persistent_volume_claim(
                    namespace=self._namespace,
                    body=manifest,
                )
                logger.info(
                    "Created resident %s PVC %s in namespace %s",
                    claim.kind,
                    claim.name,
                    self._namespace,
                )
                continue
            except Exception as exc:
                if getattr(exc, "status", None) != _HTTP_CONFLICT:
                    raise
            existing = await api.read_namespaced_persistent_volume_claim(
                name=claim.name,
                namespace=self._namespace,
            )
            labels = existing.metadata.labels or {}
            managed = labels.get(LABEL_MANAGED_BY) == self._managed_by
            owned = labels.get(LABEL_RESIDENT_ID) == str(runtime.id)
            if not (managed and owned):
                raise ValueError(
                    f"PVC {self._namespace}/{claim.name} exists but is not resident "
                    f"{runtime.id}'s own volume; delete or rename it before deploying"
                )

    async def _delete_resident_claims(self, runtime: ResidentRuntime) -> None:
        api = await self._get_core_api()
        for kind in RESIDENT_VOLUME_KINDS:
            name = self._resident_claim_name(runtime, kind)
            try:
                await api.delete_namespaced_persistent_volume_claim(
                    name=name,
                    namespace=self._namespace,
                )
                logger.info("Deleted resident %s PVC %s", kind, name)
            except Exception as exc:
                if getattr(exc, "status", None) != _HTTP_NOT_FOUND:
                    raise
                logger.debug("Resident %s PVC %s is already absent", kind, name)

    def set_resident_credentials(
        self,
        *,
        secret_injection: SecretInjectionPort,
        integrations: IntegrationRepository,
        credential_store: CredentialStorePort,
    ) -> None:
        """Wire what a resident's declared credentials are resolved and injected with."""
        self._secret_injection = secret_injection
        self._integrations = integrations
        self._credential_store = credential_store

    async def _resident_credential_annotations(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        bound: Mapping[str, str],
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Inject the owner's credentials a profile declares, bound to the resident's own SA.

        ``deployment.credentials`` lists ``{integration: <slug>}`` (the owner's one enabled
        connection of that integration) or ``{credential: <name>}``, each with ``files``
        mapping a path under /run/secrets/ to a credential field. They render into the
        ravn container. Declared but impossible is fatal.

        An integration already in ``bound`` keeps that credential; any other is read
        from the owner's connections. Returns the annotations and every integration's
        credential, for the release to record (``ANNOTATION_RESIDENT_INTEGRATIONS``).
        """
        declared = profile.deployment.get("credentials") or []
        if not declared:
            return {}, {}
        if not isinstance(declared, list):
            raise ValueError(f"Resident profile {profile.id} deployment.credentials must be a list")
        injection = self._secret_injection
        if injection is None or not injection.supports_workloads:
            raise ValueError(
                f"Resident profile {profile.id} declares credentials, but this Völundr has no "
                "secret injection that serves residents: configure the OpenBao injection adapter"
            )
        service_account = self._resident_service_account_values(runtime).get("serviceAccountName")
        if not service_account:
            raise ValueError(
                f"Resident profile {profile.id} declares credentials, which bind to the "
                "resident's own service account: set the resident platform base URL"
            )
        mappings = [
            await self._resident_credential_mapping(runtime, profile, item, bound)
            for item in declared
        ]
        integrations = {
            str(item["integration"]): mapping.credential_name
            for item, mapping in zip(declared, mappings, strict=True)
            if item.get("integration")
        }
        additions = await injection.workload_pod_spec_additions(
            runtime.owner_id,
            mappings,
            workload_id=str(runtime.id),
            service_account_name=str(service_account),
            inject_containers=RESIDENT_CREDENTIAL_CONTAINERS,
            tenant_id=runtime.tenant_id,
        )
        return dict(additions.annotations), integrations

    async def _resident_credential_mapping(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        item: Any,
        bound: Mapping[str, str],
    ) -> CredentialMapping:
        where = f"Resident profile {profile.id} deployment.credentials"
        if not isinstance(item, dict):
            raise ValueError(f"{where} entries must be objects")
        integration = str(item.get("integration") or "")
        credential = str(item.get("credential") or "")
        if bool(integration) == bool(credential):
            raise ValueError(f"{where} entries name exactly one of integration or credential")
        files = item.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError(f"{where} entries map files: {{path: field}}")
        for path, field in files.items():
            if not str(path).startswith(RESIDENT_SECRET_ROOT) or not field:
                raise ValueError(
                    f"{where} files must map a path under {RESIDENT_SECRET_ROOT} to a field"
                )
        if integration:
            credential = bound.get(integration) or await self._owner_integration_credential(
                runtime, where, integration
            )
        if self._credential_store is None:
            raise ValueError(f"{where} needs a credential store to check the owner's credentials")
        stored = await self._credential_store.get("user", runtime.owner_id, credential)
        if stored is None:
            raise ValueError(f"{where}: the owner has no credential {credential!r}")
        missing = sorted({str(field) for field in files.values()} - set(stored.keys))
        if missing:
            raise ValueError(f"{where}: credential {credential!r} has no field(s) {missing}")
        return CredentialMapping(
            credential_name=credential,
            file_mappings={str(path): str(field) for path, field in files.items()},
        )

    async def _owner_integration_credential(
        self, runtime: ResidentRuntime, where: str, slug: str
    ) -> str:
        if self._integrations is None:
            raise ValueError(f"{where} names integration {slug!r} but no integrations are wired")
        connections = [
            connection
            for connection in await self._integrations.list_connections(runtime.owner_id)
            if connection.slug == slug and connection.enabled
        ]
        if len(connections) != 1:
            raise ValueError(
                f"{where} names integration {slug!r}: the owner needs exactly one enabled "
                f"{slug} connection, and has {len(connections)}"
            )
        return connections[0].credential_name

    def set_resident_openclaw_tokens(self, tokens: ResidentOpenClawTokens) -> None:
        """Where each Ravn resident's own shim token is kept (RavnResidentSessionController)."""
        self._resident_openclaw_tokens = tokens

    async def resident_openclaw_token(self, runtime: ResidentRuntime) -> str:
        """The kept token; else the one in the resident's own Secret (adopted); else new.

        A resident Volundr deployed before tokens were kept per resident runs with the
        token in resident-<id>-openclaw; adopting it keeps its sessions across the
        upgrade without a restart.
        """
        tokens = self._resident_openclaw_tokens
        if tokens is None:
            raise RuntimeError("Ravn resident sessions are not wired on helmrelease")
        token = await tokens.stored(runtime)
        if token:
            return token
        return await tokens.ensure(runtime, adopt=await self._read_resident_openclaw_token(runtime))

    async def _read_resident_openclaw_token(self, runtime: ResidentRuntime) -> str | None:
        """The token in the resident's own shim Secret, when Volundr wrote it."""
        api = await self._get_core_api()
        try:
            secret = await api.read_namespaced_secret(
                name=self._resident_openclaw_secret_name(runtime),
                namespace=self._namespace,
            )
        except Exception as exc:
            if getattr(exc, "status", None) == _HTTP_NOT_FOUND:
                return None
            raise
        metadata = getattr(secret, "metadata", None)
        labels = getattr(metadata, "labels", None) or {}
        if not (
            labels.get(LABEL_MANAGED_BY) == self._managed_by
            and labels.get(LABEL_RESIDENT_ID) == str(runtime.id)
        ):
            return None
        encoded = (getattr(secret, "data", None) or {}).get(RESIDENT_OPENCLAW_TOKEN_KEY)
        if not encoded:
            return None
        return base64.b64decode(encoded).decode("utf-8") or None

    @staticmethod
    def _resident_openclaw_secret_name(runtime: ResidentRuntime) -> str:
        return f"resident-{runtime.id}-openclaw"

    def _resident_openclaw_enabled(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> bool:
        values = profile.deployment.get("values") or {}
        return resident_openclaw_shim_enabled(
            runtime,
            values if isinstance(values, dict) else {},
            tokens_provisioned=self._resident_openclaw_tokens is not None,
        )

    def resident_openclaw_target(
        self,
        runtime: ResidentRuntime,
        port: int,
    ) -> SessionProxyTarget | None:
        """The shim's port on the resident's own Service (charts/skuld service.yaml)."""
        namespace = str(runtime.backend_ref.get("namespace") or "")
        release = str(runtime.backend_ref.get("name") or "")
        if not namespace or not release:
            return None
        host = f"{release}-skuld.{namespace}.svc.cluster.local"
        return SessionProxyTarget(
            service_url=f"http://{host}:{port}/",
            connect_host=host,
            connect_port=port,
        )

    async def _ensure_resident_openclaw_secret(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> None:
        """Write the resident's own shim token into its own Secret, for its pod."""
        if not self._resident_openclaw_enabled(runtime, profile):
            return
        name = self._resident_openclaw_secret_name(runtime)
        token = {RESIDENT_OPENCLAW_TOKEN_KEY: await self.resident_openclaw_token(runtime)}
        api = await self._get_core_api()
        try:
            await api.create_namespaced_secret(
                namespace=self._namespace,
                body={
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {
                        "name": name,
                        "namespace": self._namespace,
                        "labels": {
                            LABEL_MANAGED_BY: self._managed_by,
                            LABEL_RESIDENT_ID: str(runtime.id),
                            LABEL_RESIDENT_OWNER_ID: runtime.owner_id,
                            LABEL_RESIDENT_TENANT_ID: runtime.tenant_id,
                        },
                    },
                    "stringData": token,
                },
            )
            logger.info("Created resident shim token Secret %s/%s", self._namespace, name)
            return
        except Exception as exc:
            if getattr(exc, "status", None) != _HTTP_CONFLICT:
                raise
        existing = await api.read_namespaced_secret(name=name, namespace=self._namespace)
        labels = existing.metadata.labels or {}
        if not (
            labels.get(LABEL_MANAGED_BY) == self._managed_by
            and labels.get(LABEL_RESIDENT_ID) == str(runtime.id)
        ):
            raise ValueError(
                f"Secret {self._namespace}/{name} exists but is not resident "
                f"{runtime.id}'s own shim token; delete or rename it before deploying"
            )
        # Keep it in step with the token Volundr keeps for the resident.
        await api.patch_namespaced_secret(
            name=name,
            namespace=self._namespace,
            body={"stringData": token},
        )

    async def _delete_resident_openclaw_secret(self, runtime: ResidentRuntime) -> None:
        tokens = self._resident_openclaw_tokens
        if tokens is None:
            # Without Ravn sessions Volundr writes no such Secret (and holds no Role for it).
            return
        if runtime.engine is ResidentEngine.RAVN:
            await tokens.forget(runtime)
        name = self._resident_openclaw_secret_name(runtime)
        api = await self._get_core_api()
        try:
            await api.delete_namespaced_secret(name=name, namespace=self._namespace)
            logger.info("Deleted resident shim token Secret %s", name)
        except Exception as exc:
            if getattr(exc, "status", None) != _HTTP_NOT_FOUND:
                raise
            logger.debug("Resident shim token Secret %s is already absent", name)

    def _resident_values(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        realm_slug: str = "",
    ) -> dict[str, Any]:
        configured_values = profile.deployment.get("values") or {}
        if not isinstance(configured_values, dict):
            raise ValueError(f"Resident profile {profile.id} deployment.values must be an object")
        values = self._with_cluster_volundr_api_url(configured_values)
        configured_resident = values.get("resident")
        configured_persona = ""
        if isinstance(configured_resident, dict):
            configured_persona = str(configured_resident.get("persona") or "")
        persona = runtime.persona_name or configured_persona
        if not persona:
            raise ValueError(f"Resident profile {profile.id} requires a persona")

        resident_values: dict[str, Any] = {
            "enabled": True,
            "name": runtime.name,
            "persona": persona,
            "routeId": str(runtime.id),
        }
        if self._resident_platform_base_url:
            resident_values["platform"] = {
                "enabled": True,
                "baseUrl": self._resident_platform_base_url,
            }
            if self._resident_triggers_enabled:
                resident_values["triggers"] = {"enabled": True}
            if self._resident_budget_enabled:
                resident_values["budget"] = {"enabled": True}
            # This release owns creating its ServiceAccount only because it
            # assigns one unique to this runtime (resident-<runtime.id> —
            # see _resident_service_account_values). Fleet-managed or
            # otherwise-shared ServiceAccount names must never be created
            # here (Helm ownership conflict, and uninstalling this one
            # release would delete an SA other residents still use) — see
            # charts/skuld's resident.serviceAccount.create (default false).
            resident_values["serviceAccount"] = {"create": True}
        if runtime.model:
            resident_values["llm"] = {"model": runtime.model}
        if runtime.flock_id is not None:
            resident_values["flock"] = {
                "id": str(runtime.flock_id),
                "memberId": str(runtime.flock_member_id or ""),
                "role": runtime.flock_role,
                "peerId": runtime.flock_peer_id,
            }
        if realm_slug:
            resident_values["realm"] = {"slug": realm_slug}
        if self._resident_openclaw_enabled(runtime, profile):
            resident_values["openclaw"] = {
                "enabled": True,
                "tokenSecret": {
                    "name": self._resident_openclaw_secret_name(runtime),
                    "key": RESIDENT_OPENCLAW_TOKEN_KEY,
                },
            }
        else:
            # Explicit either way: the chart's own default must not decide.
            resident_values["openclaw"] = {"enabled": False}

        flock_labels = resident_flock_labels(runtime, prefix="niuu.world")
        mesh_labels, mesh_annotations = resident_mesh_pod_metadata(runtime)

        runtime_values: dict[str, Any] = {
            "replicaCount": (0 if runtime.desired_state is ResidentDesiredState.SUSPENDED else 1),
            "session": {
                "id": str(runtime.id),
                "name": runtime.name,
                "ownerId": runtime.owner_id,
            },
            "resident": resident_values,
            # A resident is a resident runtime, not a Forge session: no
            # Forge session event log or activity exists for its id. Its
            # main-room history is skuld's own conversation file on its
            # workspace; usage goes to /resident-runtimes/<id>/usage.
            "broker": {"eventLogEnabled": False, "activityReportingEnabled": False},
            **self._resident_service_account_values(runtime),
            **self._resident_claim_values(self._resident_claims(runtime, profile)),
            "podLabels": {
                "niuu.world/managed": "true",
                "niuu.world/resident-id": str(runtime.id),
                "niuu.world/backend": runtime.backend.value,
                "niuu.world/engine": runtime.engine.value,
                **flock_labels,
                **mesh_labels,
            },
            "podAnnotations": {
                "niuu.world/resident-id": str(runtime.id),
                "niuu.world/resident-name": runtime.name,
                "niuu.world/owner-id": runtime.owner_id,
                "niuu.world/tenant-id": runtime.tenant_id,
                "niuu.world/visibility": "user",
                "niuu.world/profile-id": runtime.profile_id,
                **flock_labels,
                **mesh_annotations,
            },
        }
        if runtime.model:
            runtime_values["session"]["model"] = runtime.model
        _deep_merge(values, runtime_values)
        values = self._with_brokered_credential_values(values)
        _inject_workload_exchange_env(
            values,
            exchange_url=_resident_platform_exchange_url(values),
        )
        _inject_codex_auth_env(values)
        return values

    def _with_cluster_volundr_api_url(self, configured_values: dict[str, Any]) -> dict[str, Any]:
        """Profile values on top of this cluster's own Völundr API URL.

        A resident's cluster-local calls (its Forge event log, activity and
        usage reports, the Codex credential broker) belong to the Völundr that
        runs it, exactly as a session pod's do, so it gets the same
        ``volundr.apiUrl`` session pods get from ``session_defaults``. The
        platform (``resident.platform``) stays the target for platform APIs and
        the workload-token exchange. A profile that sets its own
        ``volundr.apiUrl`` keeps it.
        """
        session_volundr = self._session_defaults.get("volundr")
        cluster_api_url = ""
        if isinstance(session_volundr, dict):
            cluster_api_url = str(session_volundr.get("apiUrl") or "")
        if not cluster_api_url:
            return copy.deepcopy(configured_values)
        values: dict[str, Any] = {"volundr": {"apiUrl": cluster_api_url}}
        _deep_merge(values, copy.deepcopy(configured_values))
        return values

    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        release_name = self._resident_release_name(runtime)
        rendered = await self._render_resident_values(runtime, profile)
        values = rendered.values
        version = self._platform_version(values)
        await self._ensure_resident_claims(runtime, self._resident_claims(runtime, profile))
        await self._ensure_resident_openclaw_secret(runtime, profile)
        await self._apply_helmrelease(
            release_name,
            values,
            labels=self._resident_release_labels(runtime),
            annotations={
                **self._resident_release_annotations(runtime),
                ANNOTATION_PLATFORM_VERSION: version,
                **_integrations_annotation(rendered.integrations),
            },
        )
        self._failed_platform_upgrades.pop(str(runtime.id), None)
        return ResidentRuntimeObservation(
            observed_state=ResidentObservedState.DEPLOYING,
            backend_ref={**self._backend_ref(runtime), "platformVersion": version},
            endpoints=self._endpoints(runtime, voice=self._values_enable_voice(values)),
            conditions=[
                ResidentCondition(
                    type="BackendReady",
                    status=ResidentConditionStatus.UNKNOWN,
                    reason="ReconciliationPending",
                    message="Flux is reconciling the resident HelmRelease",
                )
            ],
        )

    async def reconcile(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        release_name = self._resident_release_name(runtime)
        helmrelease = await self._get_helmrelease(release_name)
        if helmrelease is None:
            return await self.deploy(runtime, profile)

        upgrade_failure: ResidentCondition | None = None
        if runtime.desired_state is ResidentDesiredState.RUNNING:
            # A suspended resident is not woken for this: resume renders it anew.
            helmrelease, upgrade_failure = await self._follow_platform(
                runtime, profile, helmrelease
            )

        desired_replicas = 0 if runtime.desired_state is ResidentDesiredState.SUSPENDED else 1
        values = (helmrelease.get("spec") or {}).get("values") or {}
        if values.get("replicaCount", 1) != desired_replicas:
            await self._set_replica_count(runtime, desired_replicas)

        deployment = await self._get_resident_deployment(release_name)
        observation = self._resident_observation(runtime, helmrelease, deployment)
        if upgrade_failure is None:
            return observation
        return replace(
            observation,
            observed_state=ResidentObservedState.FAILED,
            conditions=[upgrade_failure, *observation.conditions],
        )

    async def _follow_platform(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        helmrelease: dict[str, Any],
    ) -> tuple[dict[str, Any], ResidentCondition | None]:
        """Move a running resident's release to the version this Völundr renders, once.

        The version is the chart and the images the profile renders now, compared
        with the release's own spec, so it holds across Völundr restarts and
        replicas. The release keeps the resident's own PVCs and shim token Secret,
        so a version change is a rolling update with its disks and channels intact.
        A version this release was already moved to, or moved away from, is never
        applied again: an edited release is left alone, and an older Völundr still
        rendering the replaced version (during Völundr's own rollout) does not move
        the resident back. A failed move marks the resident failed and is not
        retried until the target changes or the resident is restarted.

        Returns the release as it now is and the failure condition, if any.
        """
        runtime_id = str(runtime.id)
        try:
            rendered = await self._render_resident_values(
                runtime, profile, bound_integrations=_release_integrations(helmrelease)
            )
        except Exception:
            # Deploy reports a profile or realm that does not render; reconcile
            # keeps observing the release as it runs.
            logger.debug("Resident %s does not render; not following the platform", runtime_id)
            return helmrelease, None
        target = self._platform_version(rendered.values)
        current = self._release_platform_version(helmrelease)
        if current == target:
            self._failed_platform_upgrades.pop(runtime_id, None)
            return helmrelease, None
        annotations = (helmrelease.get("metadata") or {}).get("annotations") or {}
        if target in (
            annotations.get(ANNOTATION_PLATFORM_VERSION),
            annotations.get(ANNOTATION_REPLACED_PLATFORM_VERSION),
        ):
            return helmrelease, None
        failed = self._failed_platform_upgrades.get(runtime_id)
        if failed is not None and failed[0] == target:
            return helmrelease, self._upgrade_failed_condition(failed[1])

        logger.info("Upgrading resident %s from %s to %s", runtime.name, current, target)
        try:
            upgraded = await self._roll_resident_release(runtime, profile, helmrelease, rendered)
        except Exception as exc:
            if getattr(exc, "status", None) == _HTTP_CONFLICT:
                # Another Völundr replica moved it first; the next pass observes that.
                logger.info("Resident %s was upgraded concurrently", runtime.name)
                return helmrelease, None
            reason = (
                f"Upgrading resident {runtime.name} from {current} to {target} failed: {exc}. "
                "Restart the resident to retry."
            )
            logger.warning(reason)
            self._failed_platform_upgrades[runtime_id] = (target, reason)
            return helmrelease, self._upgrade_failed_condition(reason)
        return upgraded, None

    @staticmethod
    def _upgrade_failed_condition(message: str) -> ResidentCondition:
        return ResidentCondition(
            type="PlatformUpgrade",
            status=ResidentConditionStatus.FALSE,
            reason="UpgradeFailed",
            message=message,
        )

    async def _render_resident_values(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        *,
        bound_integrations: Mapping[str, str] | None = None,
    ) -> RenderedResident:
        """The resident's values; ``bound_integrations`` are those its release recorded."""
        values = self._resident_values(runtime, profile, await self._resolve_realm_slug(runtime))
        annotations, integrations = await self._resident_credential_annotations(
            runtime, profile, bound_integrations or {}
        )
        if annotations:
            values.setdefault("podAnnotations", {}).update(annotations)
        return RenderedResident(values=values, integrations=integrations)

    def _platform_version(self, values: dict[str, Any]) -> str:
        """The version this Völundr renders for a resident: its chart and images."""
        return resident_platform_version(self._chart_name, self._chart_version, values)

    @staticmethod
    def _release_platform_version(helmrelease: dict[str, Any]) -> str:
        """The version a resident's release runs, read from its spec."""
        spec = helmrelease.get("spec") or {}
        chart = (spec.get("chart") or {}).get("spec") or {}
        return resident_platform_version(
            str(chart.get("chart") or ""),
            str(chart.get("version") or ""),
            spec.get("values") or {},
        )

    def _resident_release_labels(self, runtime: ResidentRuntime) -> dict[str, str]:
        return {
            "niuu.world/kind": "resident",
            "niuu.world/resident-id": str(runtime.id),
            "niuu.world/backend": runtime.backend.value,
            **resident_flock_labels(runtime, prefix="niuu.world"),
        }

    @staticmethod
    def _resident_release_annotations(runtime: ResidentRuntime) -> dict[str, str]:
        return {
            "niuu.world/owner-id": runtime.owner_id,
            "niuu.world/tenant-id": runtime.tenant_id,
            "niuu.world/profile-id": runtime.profile_id,
            **resident_flock_labels(runtime, prefix="niuu.world"),
        }

    async def _roll_resident_release(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        helmrelease: dict[str, Any],
        rendered: RenderedResident,
    ) -> dict[str, Any]:
        """Replace the release's chart and values with ``rendered``, keeping its metadata.

        The resident keeps the storage its release mounts (its own PVCs, or the
        claim a resident older than those runs on): the rolled pod mounts the same
        disks, never a new empty one. Its shim token Secret is ensured first. The
        replace carries the release's resourceVersion: a concurrent writer makes it
        fail with a conflict rather than be overwritten.
        """
        values = _with_release_storage(rendered.values, helmrelease)
        claims = [
            claim
            for claim in self._resident_claims(runtime, profile)
            if claim.name in _claim_names(values)
        ]
        await self._ensure_resident_claims(runtime, claims)
        await self._ensure_resident_openclaw_secret(runtime, profile)
        release_name = self._resident_release_name(runtime)
        current = self._release_platform_version(helmrelease)
        target = self._platform_version(values)
        manifest = self._build_helmrelease(
            release_name,
            values,
            labels=self._resident_release_labels(runtime),
            annotations=self._resident_release_annotations(runtime),
        )
        body = copy.deepcopy(helmrelease)
        body.pop("status", None)
        metadata = body.setdefault("metadata", {})
        metadata["labels"] = {**(metadata.get("labels") or {}), **manifest["metadata"]["labels"]}
        annotations = {
            **(metadata.get("annotations") or {}),
            **manifest["metadata"]["annotations"],
            ANNOTATION_PLATFORM_VERSION: target,
        }
        annotations.pop(ANNOTATION_RESIDENT_INTEGRATIONS, None)
        annotations.update(_integrations_annotation(rendered.integrations))
        if current != target:
            annotations[ANNOTATION_REPLACED_PLATFORM_VERSION] = current
        metadata["annotations"] = annotations
        body["spec"] = {**(body.get("spec") or {}), **manifest["spec"]}
        replaced = await self._replace_helmrelease(release_name, body)
        self._failed_platform_upgrades.pop(str(runtime.id), None)
        return replaced if isinstance(replaced, dict) and replaced.get("spec") else body

    async def _rerender_resident_release(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        *,
        pod_annotations: dict[str, str] | None = None,
    ) -> ResidentRuntimeObservation:
        """Render the resident anew on this Völundr's version, as restart and resume do."""
        helmrelease = await self._get_helmrelease(self._resident_release_name(runtime))
        if helmrelease is None:
            return await self.deploy(runtime, profile)
        rendered = await self._render_resident_values(
            runtime, profile, bound_integrations=_release_integrations(helmrelease)
        )
        if pod_annotations:
            rendered.values.setdefault("podAnnotations", {}).update(pod_annotations)
        current = self._release_platform_version(helmrelease)
        target = self._platform_version(rendered.values)
        if current != target:
            logger.info("Upgrading resident %s from %s to %s", runtime.name, current, target)
        await self._roll_resident_release(runtime, profile, helmrelease, rendered)
        return await self._observe_after_lifecycle(runtime)

    def _resident_observation(
        self,
        runtime: ResidentRuntime,
        helmrelease: dict[str, Any],
        deployment: dict[str, Any] | None,
    ) -> ResidentRuntimeObservation:
        backend_ref = self._backend_ref(runtime)
        backend_ref["platformVersion"] = self._release_platform_version(helmrelease)
        deployment_name = self._deployment_name(deployment)
        if deployment_name:
            backend_ref["deploymentName"] = deployment_name
        release_values = (helmrelease.get("spec") or {}).get("values") or {}
        return ResidentRuntimeObservation(
            observed_state=self._observed_state(runtime, helmrelease, deployment),
            backend_ref=backend_ref,
            endpoints=self._endpoints(runtime, voice=self._values_enable_voice(release_values)),
            conditions=self._normalized_conditions(helmrelease, deployment),
        )

    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        """Roll the resident's pod, rendered anew on this Völundr's version."""
        return await self._rerender_resident_release(
            runtime,
            profile,
            pod_annotations={"niuu.world/restarted-at": datetime.now(UTC).isoformat()},
        )

    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        await self._set_replica_count(runtime, 0)
        return await self._observe_after_lifecycle(runtime)

    async def resume(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile | None = None,
    ) -> ResidentRuntimeObservation:
        """Scale the resident back up; with its profile, on this Völundr's version.

        A suspended resident is not upgraded while it sleeps, so resuming it is
        where it moves to the current version: one start, on the new pod spec.
        """
        if profile is not None:
            return await self._rerender_resident_release(runtime, profile)
        await self._set_replica_count(runtime, 1)
        return await self._observe_after_lifecycle(runtime)

    async def delete(self, runtime: ResidentRuntime) -> bool:
        """Delete the HelmRelease, then the resident's own PVCs and shim token Secret."""
        existed = await self._delete_helmrelease(self._resident_release_name(runtime))
        await self._delete_resident_claims(runtime)
        await self._delete_resident_openclaw_secret(runtime)
        if self._secret_injection is not None and self._secret_injection.supports_workloads:
            await self._secret_injection.cleanup_workload(str(runtime.id))
        return existed

    async def logs(
        self,
        runtime: ResidentRuntime,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> ResidentLogPage:
        """Tail each started container of the resident's pods; a source is a container name.

        Every pod of the release is read, so a rollout shows the old and the new
        pod side by side (``fields["pod"]`` tells them apart).
        """
        release_name = self._resident_release_name(runtime)
        selector = f"app.kubernetes.io/instance={release_name}"
        api = await self._get_core_api()
        try:
            pod_list = await api.list_namespaced_pod(
                namespace=self._namespace,
                label_selector=selector,
            )
        except Exception as exc:
            if getattr(exc, "status", None) == _HTTP_FORBIDDEN:
                raise self._pod_logs_forbidden("list pods") from exc
            raise
        pods = list(getattr(pod_list, "items", None) or [])
        if not pods:
            raise ResidentLogsUnavailableError(
                f"resident {runtime.name!r} has no pod in namespace {self._namespace} "
                f"({selector}); it is suspended or not scheduled yet"
            )

        entries: list[ResidentLogEntry] = []
        read_any = False
        for pod in pods:
            pod_name = str(pod.metadata.name)
            for container in _started_containers(pod):
                if sources and container not in sources:
                    continue
                read_any = True
                try:
                    payload = await api.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self._namespace,
                        container=container,
                        tail_lines=lines,
                        timestamps=True,
                    )
                except Exception as exc:
                    if getattr(exc, "status", None) == _HTTP_FORBIDDEN:
                        raise self._pod_logs_forbidden(f"read pod {pod_name} logs") from exc
                    raise
                entries.extend(
                    parse_pod_logs(
                        str(payload or ""),
                        pod=pod_name,
                        container=container,
                        min_level=min_level,
                    )
                )
        if not read_any:
            wanted = f" named {', '.join(sources)}" if sources else ""
            raise ResidentLogsUnavailableError(
                f"resident {runtime.name!r} has no started container{wanted} in its pods "
                f"({', '.join(str(pod.metadata.name) for pod in pods)}); it is still starting"
            )
        entries.sort(key=lambda entry: entry.timestamp_ms)
        return ResidentLogPage(entries=entries[-lines:], buffer_total=len(entries))

    def _pod_logs_forbidden(self, action: str) -> PermissionError:
        """Name the missing grant when Kubernetes refuses Völundr's service account."""
        return PermissionError(
            f"Kubernetes forbids Völundr's service account to {action} in namespace "
            f"{self._namespace}: it needs get/list on pods and get on pods/log there, which "
            f"the Völundr chart's resident-logs Role grants (charts/volundr/templates/rbac.yaml)"
        )

    async def _set_replica_count(self, runtime: ResidentRuntime, replicas: int) -> None:
        await self._patch_helmrelease(
            self._resident_release_name(runtime),
            {"spec": {"values": {"replicaCount": replicas}}},
        )

    async def _observe_after_lifecycle(
        self,
        runtime: ResidentRuntime,
    ) -> ResidentRuntimeObservation:
        """Observe immediately after an explicit patch without needing profile data."""
        release_name = self._resident_release_name(runtime)
        helmrelease = await self._get_helmrelease(release_name)
        if helmrelease is None:
            return ResidentRuntimeObservation(
                observed_state=ResidentObservedState.FAILED,
                backend_ref=self._backend_ref(runtime),
                endpoints=self._endpoints(runtime),
                conditions=[
                    ResidentCondition(
                        type="BackendReady",
                        status=ResidentConditionStatus.FALSE,
                        reason="HelmReleaseNotFound",
                        message=f"HelmRelease {self._namespace}/{release_name} does not exist",
                    )
                ],
            )
        deployment = await self._get_resident_deployment(release_name)
        return self._resident_observation(runtime, helmrelease, deployment)

    async def _get_resident_deployment(self, release_name: str) -> dict[str, Any] | None:
        await self._get_api()
        from kubernetes_asyncio import client

        apps = client.AppsV1Api(self._api_client)
        result = await apps.list_namespaced_deployment(
            namespace=self._namespace,
            label_selector=f"app.kubernetes.io/instance={release_name}",
        )
        items = (
            result.get("items", []) if isinstance(result, dict) else getattr(result, "items", [])
        )
        if not items:
            return None
        deployment = items[0]
        if isinstance(deployment, dict):
            return deployment
        if hasattr(deployment, "to_dict"):
            return deployment.to_dict()
        return self._api_client.sanitize_for_serialization(deployment)

    @staticmethod
    def _deployment_name(deployment: dict[str, Any] | None) -> str:
        if not deployment:
            return ""
        metadata = deployment.get("metadata") or {}
        return str(metadata.get("name") or "")

    @classmethod
    def _observed_state(
        cls,
        runtime: ResidentRuntime,
        helmrelease: dict[str, Any],
        deployment: dict[str, Any] | None,
    ) -> ResidentObservedState:
        values = (helmrelease.get("spec") or {}).get("values") or {}
        replicas = values.get("replicaCount", 1)
        if runtime.desired_state is ResidentDesiredState.SUSPENDED:
            if replicas != 0:
                return ResidentObservedState.DEPLOYING
            if deployment is None:
                return ResidentObservedState.DEPLOYING
            deployment_spec = deployment.get("spec") or {}
            desired_replicas = int(deployment_spec.get("replicas") or 0)
            if desired_replicas == 0 and cls._available_replicas(deployment) == 0:
                return ResidentObservedState.SUSPENDED
            return ResidentObservedState.DEPLOYING

        ready = cls._ready_condition(helmrelease)
        if (
            ready
            and ready.get("status") == "False"
            and ready.get("reason")
            in {
                "InstallFailed",
                "UpgradeFailed",
                "ReconciliationFailed",
            }
        ):
            return ResidentObservedState.FAILED
        if not ready or ready.get("status") != "True" or deployment is None:
            return ResidentObservedState.DEPLOYING

        for condition in (deployment.get("status") or {}).get("conditions") or []:
            if condition.get("status") != "False":
                continue
            if condition.get("reason") in {"ProgressDeadlineExceeded", "ReplicaFailure"}:
                return ResidentObservedState.FAILED

        if cls._available_replicas(deployment) > 0:
            return ResidentObservedState.ACTIVE
        return ResidentObservedState.DEPLOYING

    @staticmethod
    def _available_replicas(deployment: dict[str, Any]) -> int:
        status = deployment.get("status") or {}
        return int(status.get("available_replicas") or status.get("availableReplicas") or 0)

    @staticmethod
    def _ready_condition(helmrelease: dict[str, Any]) -> dict[str, Any] | None:
        for condition in (helmrelease.get("status") or {}).get("conditions") or []:
            if condition.get("type") == "Ready":
                return condition
        return None

    @classmethod
    def _normalized_conditions(
        cls,
        helmrelease: dict[str, Any],
        deployment: dict[str, Any] | None,
    ) -> list[ResidentCondition]:
        normalized = [
            cls._normalize_condition("HelmRelease", condition)
            for condition in (helmrelease.get("status") or {}).get("conditions") or []
        ]
        if deployment is not None:
            normalized.extend(
                cls._normalize_condition("Deployment", condition)
                for condition in (deployment.get("status") or {}).get("conditions") or []
            )
        return normalized

    @staticmethod
    def _normalize_condition(source: str, condition: dict[str, Any]) -> ResidentCondition:
        raw_status = str(condition.get("status") or "Unknown").lower()
        status = ResidentConditionStatus.UNKNOWN
        if raw_status == "true":
            status = ResidentConditionStatus.TRUE
        if raw_status == "false":
            status = ResidentConditionStatus.FALSE
        transition = condition.get("lastTransitionTime") or condition.get("last_transition_time")
        if isinstance(transition, str):
            transition = datetime.fromisoformat(transition.replace("Z", "+00:00"))
        if not isinstance(transition, datetime):
            transition = datetime.now(UTC)
        return ResidentCondition(
            type=f"{source}{condition.get('type') or 'Condition'}",
            status=status,
            reason=str(condition.get("reason") or ""),
            message=str(condition.get("message") or ""),
            last_transition_at=transition,
        )


#: A level word in plain-text log formats (Skuld's "... - INFO - ...", Ravn's "... INFO ...").
_POD_LOG_LEVEL = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b")
_POD_LOG_LEVEL_ALIASES = {"warn": "warning", "fatal": "critical"}


def _started_containers(pod: Any) -> list[str]:
    """Names of the pod's containers that have run, so Kubernetes holds their logs.

    A container still waiting for its first start has no log yet (Kubernetes
    answers 400); a crash-looping one keeps its last run's.
    """
    spec = pod.spec
    status = pod.status
    statuses = [
        *(getattr(status, "init_container_statuses", None) or []),
        *(getattr(status, "container_statuses", None) or []),
    ]
    started = {
        container_status.name
        for container_status in statuses
        if _container_has_run(container_status)
    }
    declared = [
        *(getattr(spec, "init_containers", None) or []),
        *(getattr(spec, "containers", None) or []),
    ]
    return [container.name for container in declared if container.name in started]


def _container_has_run(container_status: Any) -> bool:
    state = container_status.state
    last_state = getattr(container_status, "last_state", None)
    return bool(
        (state is not None and (state.running is not None or state.terminated is not None))
        or (last_state is not None and last_state.terminated is not None)
        or (container_status.restart_count or 0) > 0
    )


def parse_pod_logs(
    payload: str,
    *,
    pod: str,
    container: str,
    min_level: str,
) -> list[ResidentLogEntry]:
    """Parse ``kubectl logs --timestamps`` output of one container into entries.

    Every line starts with the kubelet's RFC 3339 timestamp. A JSON line (Skuld's
    and Ravn's json log format) gives its own level and message; a plain line
    gives the level word it carries, else one inferred like the local runtimes do.
    """
    minimum = LOG_LEVELS.get(_POD_LOG_LEVEL_ALIASES.get(min_level.lower(), min_level.lower()), 0)
    entries: list[ResidentLogEntry] = []
    for line in payload.splitlines():
        stamp, _, message = line.partition(" ")
        try:
            timestamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        message = message.rstrip()
        if not message:
            continue
        level, message = _pod_log_level_and_message(message)
        if LOG_LEVELS.get(level, 20) < minimum:
            continue
        entries.append(
            ResidentLogEntry(
                timestamp_ms=int(timestamp.timestamp() * 1000),
                level=level,
                source=container,
                target=container,
                message=message,
                fields={"pod": pod},
            )
        )
    return entries


def _pod_log_level_and_message(message: str) -> tuple[str, str]:
    if message.startswith("{"):
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            raw_level = str(payload.get("level") or payload.get("levelname") or "").lower()
            text = str(payload.get("message") or payload.get("msg") or payload.get("event") or "")
            level = _POD_LOG_LEVEL_ALIASES.get(raw_level, raw_level)
            if level not in LOG_LEVELS:
                level = resident_log_level(text or message)
            return level, text or message
    match = _POD_LOG_LEVEL.search(message)
    if match is not None:
        word = match.group(1).lower()
        return _POD_LOG_LEVEL_ALIASES.get(word, word), message
    return resident_log_level(message), message


def resident_platform_version(chart: str, chart_version: str, values: dict[str, Any]) -> str:
    """A resident release's platform version: its chart and every image its values name.

    Readable and deterministic, e.g. ``skuld 2.0.0-dev.264, ghcr.io/niuulabs/skuld:dev-1``;
    an image below the top level carries its values path (``nginx ...``). Völundr's
    rendered values and a release's stored spec give the same string for the same
    version, so comparing the two tells whether a resident runs this Völundr's version.
    """
    refs = sorted(_image_refs(values, ""), key=lambda item: (item[0] != "", item[0]))
    parts = [f"{chart} {chart_version}".strip()]
    parts.extend(ref if not path else f"{path} {ref}" for path, ref in refs)
    return ", ".join(part for part in parts if part)


#: The values that name a resident's volumes: an upgrade keeps them as the release has them.
_RESIDENT_STORAGE_VALUES = ("homeVolume", "persistence")


def _integrations_annotation(integrations: Mapping[str, str]) -> dict[str, str]:
    """The release annotation recording a resident's integration credentials, if any."""
    if not integrations:
        return {}
    return {ANNOTATION_RESIDENT_INTEGRATIONS: json.dumps(dict(sorted(integrations.items())))}


def _release_integrations(helmrelease: dict[str, Any]) -> dict[str, str]:
    """The integration credentials a resident's release recorded when it was deployed."""
    annotations = (helmrelease.get("metadata") or {}).get("annotations") or {}
    raw = annotations.get(ANNOTATION_RESIDENT_INTEGRATIONS)
    if not raw:
        return {}
    recorded = json.loads(raw)
    if not isinstance(recorded, dict) or not all(
        isinstance(slug, str) and isinstance(name, str) and name for slug, name in recorded.items()
    ):
        raise ValueError(f"{ANNOTATION_RESIDENT_INTEGRATIONS} must map integrations to credentials")
    return recorded


def _with_release_storage(values: dict[str, Any], helmrelease: dict[str, Any]) -> dict[str, Any]:
    """``values`` with the volumes the running release mounts, exactly as it mounts them."""
    current = (helmrelease.get("spec") or {}).get("values") or {}
    kept = copy.deepcopy(values)
    for key in _RESIDENT_STORAGE_VALUES:
        if key in current:
            kept[key] = copy.deepcopy(current[key])
        else:
            kept.pop(key, None)
    return kept


def _claim_names(values: dict[str, Any]) -> set[str]:
    names = set()
    for key in _RESIDENT_STORAGE_VALUES:
        storage = values.get(key)
        if isinstance(storage, dict) and storage.get("existingClaim"):
            names.add(str(storage["existingClaim"]))
    return names


def _image_refs(value: Any, path: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image":
                ref = _image_ref(child)
                if ref:
                    refs.append((path, ref))
                continue
            refs.extend(_image_refs(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            refs.extend(_image_refs(item, f"{path}[{index}]"))
    return refs


def _image_ref(image: Any) -> str:
    if isinstance(image, str):
        return image.strip()
    if not isinstance(image, dict):
        return ""
    repository = str(image.get("repository") or "").strip()
    if not repository:
        return ""
    tag = str(image.get("tag") or "").strip()
    digest = str(image.get("digest") or "").strip()
    ref = f"{repository}:{tag}" if tag else repository
    return f"{ref}@{digest}" if digest else ref


def _resident_platform_exchange_url(values: dict) -> str:
    """The platform's workload-token exchange for a resident, or "".

    Mirrors charts/skuld's skuld.residentWorkloadExchangeUrl: the explicit
    resident.platform.workloadExchangeUrl, else the platform base URL's. A
    resident without a platform has only its cluster's Völundr.
    """
    resident = values.get("resident")
    platform = resident.get("platform") if isinstance(resident, dict) else None
    if not isinstance(platform, dict) or not platform.get("enabled"):
        return ""
    explicit = str(platform.get("workloadExchangeUrl") or "").strip()
    if explicit:
        return explicit
    base_url = str(platform.get("baseUrl") or "").rstrip("/")
    if not base_url:
        return ""
    return f"{base_url}/api/v1/tokens/workload/exchange"


def _inject_workload_exchange_env(values: dict, *, exchange_url: str = "") -> None:
    """Propagate workload token exchange URL into all session containers.

    ``exchange_url`` names the exchange explicitly; without it the exchange is
    the one on ``volundr.apiUrl``.
    """
    if not exchange_url:
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


def _inject_codex_auth_env(values: dict) -> None:
    """Give every session container the Codex broker, not just the broker pod.

    A flock persona runs the same Codex transport Skuld does but builds it
    itself, so it needs the same SKULD__CODEX_AUTH__* contract. Reaching only
    the main container left all six research personas opening the Codex
    websocket with no credential — 401 on every turn, while the session itself
    looked healthy.
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
