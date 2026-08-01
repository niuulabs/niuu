"""Flux HelmRelease adapter for pod management.

Creates HelmRelease custom resources in Kubernetes, which Flux
reconciles into running session pods. Selected via the dynamic
adapter pattern in config YAML.
"""

import copy
import logging
from datetime import UTC, datetime
from typing import Any

from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.resident_container_spec import (
    resident_flock_labels,
    resident_flock_profile_configured,
    resident_mesh_pod_metadata,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentCondition,
    ResidentConditionStatus,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
    Session,
    SessionSpec,
    SessionStatus,
    _deep_merge,
)
from volundr.domain.ports import (
    PodManager,
    PodStartResult,
    ResidentRuntimeController,
    ResidentRuntimeObservation,
)

logger = logging.getLogger(__name__)

# Flux HelmRelease API coordinates
HELMRELEASE_GROUP = "helm.toolkit.fluxcd.io"
HELMRELEASE_VERSION = "v2"
HELMRELEASE_PLURAL = "helmreleases"


class FluxPodManager(BrokeredCredentialPodManager, PodManager, ResidentRuntimeController):
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
        self._configure_brokered_credentials(
            codex_auth_adapter=codex_auth_adapter,
            codex_auth_kwargs=codex_auth_kwargs,
        )
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

    def initial_code_endpoint(self, session: Session) -> str | None:
        return self._code_endpoint(session.name, str(session.id))

    def _build_helmrelease(
        self,
        name: str,
        values: dict,
        *,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
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
                "app.kubernetes.io/managed-by": "volundr",
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
                        "chart": self._chart_name,
                        "version": self._chart_version,
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

    async def _patch_helmrelease(self, name: str, body: dict[str, Any]) -> None:
        api = await self._get_api()
        await api.patch_namespaced_custom_object(
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

    def _backend_ref(self, runtime: ResidentRuntime) -> dict[str, Any]:
        release_name = self._resident_release_name(runtime)
        return {
            "apiVersion": f"{HELMRELEASE_GROUP}/{HELMRELEASE_VERSION}",
            "kind": "HelmRelease",
            "namespace": self._namespace,
            "name": release_name,
            "workloadSelector": f"app.kubernetes.io/instance={release_name}",
        }

    def _endpoints(self, runtime: ResidentRuntime) -> list[ResidentEndpoint]:
        return [
            ResidentEndpoint(
                kind="chat",
                protocol="skuld-v1",
                url=self._chat_endpoint(str(runtime.id), str(runtime.id)),
            )
        ]

    def _resident_values(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> dict[str, Any]:
        configured_values = profile.deployment.get("values") or {}
        if not isinstance(configured_values, dict):
            raise ValueError(f"Resident profile {profile.id} deployment.values must be an object")
        values = copy.deepcopy(configured_values)
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
        if runtime.model:
            resident_values["llm"] = {"model": runtime.model}
        if runtime.flock_id is not None:
            resident_values["flock"] = {
                "id": str(runtime.flock_id),
                "memberId": str(runtime.flock_member_id or ""),
                "role": runtime.flock_role,
                "peerId": runtime.flock_peer_id,
            }

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
        _inject_workload_exchange_env(values)
        return values

    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        release_name = self._resident_release_name(runtime)
        await self._apply_helmrelease(
            release_name,
            self._resident_values(runtime, profile),
            labels={
                "niuu.world/kind": "resident",
                "niuu.world/resident-id": str(runtime.id),
                "niuu.world/backend": runtime.backend.value,
                **resident_flock_labels(runtime, prefix="niuu.world"),
            },
            annotations={
                "niuu.world/owner-id": runtime.owner_id,
                "niuu.world/tenant-id": runtime.tenant_id,
                "niuu.world/profile-id": runtime.profile_id,
                **resident_flock_labels(runtime, prefix="niuu.world"),
            },
        )
        return ResidentRuntimeObservation(
            observed_state=ResidentObservedState.DEPLOYING,
            backend_ref=self._backend_ref(runtime),
            endpoints=self._endpoints(runtime),
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

        desired_replicas = 0 if runtime.desired_state is ResidentDesiredState.SUSPENDED else 1
        values = (helmrelease.get("spec") or {}).get("values") or {}
        if values.get("replicaCount", 1) != desired_replicas:
            await self._set_replica_count(runtime, desired_replicas)

        deployment = await self._get_resident_deployment(release_name)
        return self._resident_observation(runtime, helmrelease, deployment)

    def _resident_observation(
        self,
        runtime: ResidentRuntime,
        helmrelease: dict[str, Any],
        deployment: dict[str, Any] | None,
    ) -> ResidentRuntimeObservation:
        backend_ref = self._backend_ref(runtime)
        deployment_name = self._deployment_name(deployment)
        if deployment_name:
            backend_ref["deploymentName"] = deployment_name
        return ResidentRuntimeObservation(
            observed_state=self._observed_state(runtime, helmrelease, deployment),
            backend_ref=backend_ref,
            endpoints=self._endpoints(runtime),
            conditions=self._normalized_conditions(helmrelease, deployment),
        )

    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        restarted_at = datetime.now(UTC).isoformat()
        await self._patch_helmrelease(
            self._resident_release_name(runtime),
            {
                "spec": {
                    "values": {
                        "podAnnotations": {"niuu.world/restarted-at": restarted_at},
                    }
                }
            },
        )
        return await self._observe_after_lifecycle(runtime)

    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        await self._set_replica_count(runtime, 0)
        return await self._observe_after_lifecycle(runtime)

    async def resume(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        await self._set_replica_count(runtime, 1)
        return await self._observe_after_lifecycle(runtime)

    async def delete(self, runtime: ResidentRuntime) -> bool:
        return await self._delete_helmrelease(self._resident_release_name(runtime))

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
