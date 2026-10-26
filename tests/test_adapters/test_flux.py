"""Tests for Flux HelmRelease pod manager adapter."""

from contextlib import ExitStack
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.conftest import make_spec
from volundr.adapters.outbound.flux import (
    HELMRELEASE_GROUP,
    HELMRELEASE_PLURAL,
    HELMRELEASE_VERSION,
    FluxPodManager,
    _inject_workload_exchange_env,
    parse_pod_logs,
)
from volundr.domain.models import (
    GitSource,
    PodSpecAdditions,
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.ports import ResidentLogsUnavailableError, ResidentRuntimeObservation


def _resident_runtime(**overrides) -> ResidentRuntime:
    values = {
        "owner_id": "user-a",
        "tenant_id": "tenant-a",
        "name": "Muninn",
        "persona_name": "product-steward",
        "model": "gpt-5.6",
        "backend": ResidentBackend.HELMRELEASE,
        "engine": ResidentEngine.RAVN,
        "profile_id": "ravn-helm",
        "capabilities": [
            ResidentCapability.CHAT,
            ResidentCapability.RUNTIME_RESTART,
            ResidentCapability.RUNTIME_SUSPEND,
        ],
    }
    values.update(overrides)
    return ResidentRuntime(**values)


def _resident_profile() -> ResidentDeploymentProfile:
    return ResidentDeploymentProfile(
        id="ravn-helm",
        display_name="Resident Ravn",
        backend=ResidentBackend.HELMRELEASE,
        engine=ResidentEngine.RAVN,
        capabilities=[
            ResidentCapability.CHAT,
            ResidentCapability.RUNTIME_RESTART,
            ResidentCapability.RUNTIME_SUSPEND,
        ],
        deployment={
            "values": {
                "persistence": {
                    "enabled": True,
                    "emptyDir": False,
                    "existingClaim": "resident-workspaces",
                },
                "resident": {
                    "platform": {
                        "enabled": True,
                        "baseUrl": "https://niuu.example.test",
                    },
                    "llm": {"provider": "openai"},
                },
            }
        },
    )


@pytest.fixture
def sample_session() -> Session:
    return Session(
        id=uuid4(),
        name="Test Session",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
    )


@pytest.fixture
def pod_manager() -> FluxPodManager:
    return FluxPodManager(
        namespace="test-ns",
        chart_name="skuld",
        chart_version="0.38.0",
        source_ref_kind="HelmRepository",
        source_ref_name="skuld-repo",
        timeout="5m",
        interval="5m",
        base_domain="volundr.example.com",
        chat_scheme="wss",
        code_scheme="https",
        session_defaults={
            "image": {"repository": "ghcr.io/niuulabs/skuld", "tag": "latest"},
            "resources": {"requests": {"memory": "256Mi", "cpu": "100m"}},
        },
    )


@pytest.fixture
def mock_api():
    api = AsyncMock()
    return api


_ORIGINAL_GET_CORE_API = FluxPodManager._get_core_api


@pytest.fixture(autouse=True)
def core_api(monkeypatch) -> AsyncMock:
    """CoreV1Api for resident PVCs; the manifest is the builder's kwargs."""
    api = AsyncMock()
    monkeypatch.setattr(FluxPodManager, "_get_core_api", AsyncMock(return_value=api))
    monkeypatch.setattr(
        "volundr.adapters.outbound.flux.build_pvc_manifest",
        lambda **manifest: manifest,
    )
    return api


class TestFluxPodManagerStart:
    async def test_start_creates_helmrelease(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        spec = make_spec(
            session={"id": str(sample_session.id), "name": sample_session.name},
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.start(sample_session, spec)

        mock_api.create_namespaced_custom_object.assert_called_once()
        call_kwargs = mock_api.create_namespaced_custom_object.call_args[1]
        assert call_kwargs["group"] == HELMRELEASE_GROUP
        assert call_kwargs["version"] == HELMRELEASE_VERSION
        assert call_kwargs["namespace"] == "test-ns"
        assert call_kwargs["plural"] == HELMRELEASE_PLURAL

        body = call_kwargs["body"]
        assert body["metadata"]["name"] == f"skuld-{sample_session.id}"
        assert body["spec"]["chart"]["spec"]["chart"] == "skuld"
        assert body["spec"]["chart"]["spec"]["version"] == "0.38.0"
        assert body["spec"]["values"]["session"]["id"] == str(sample_session.id)
        assert body["spec"]["values"]["session"]["name"] == sample_session.name

    async def test_start_returns_endpoints(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        spec = make_spec()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.start(sample_session, spec)

        assert result.chat_endpoint == f"wss://{sample_session.name}.volundr.example.com/session"
        assert result.code_endpoint == f"https://{sample_session.name}.volundr.example.com/"
        assert result.pod_name == f"skuld-{sample_session.id}"

    async def test_start_merges_session_defaults_with_spec(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        spec = make_spec()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        values = body["spec"]["values"]
        assert values["image"]["repository"] == "ghcr.io/niuulabs/skuld"
        assert values["resources"]["requests"]["memory"] == "256Mi"

    async def test_start_spec_values_override_defaults(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        spec = make_spec(
            resources={"cpu": "4", "memory": "16Gi"},
            env={"CUDA_VISIBLE_DEVICES": "0"},
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        values = body["spec"]["values"]
        # Dict values are shallow-merged: spec values are merged into defaults
        assert values["resources"]["cpu"] == "4"
        assert values["resources"]["memory"] == "16Gi"
        assert values["env"] == {"CUDA_VISIBLE_DEVICES": "0"}

    async def test_start_patches_on_conflict(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        spec = make_spec()
        mock_api.create_namespaced_custom_object.side_effect = Exception(
            "409 Conflict: AlreadyExists"
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.start(sample_session, spec)

        mock_api.patch_namespaced_custom_object.assert_called_once()


class TestFluxPodManagerStop:
    async def test_stop_deletes_helmrelease(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.stop(sample_session)

        assert result is True
        mock_api.delete_namespaced_custom_object.assert_called_once_with(
            group=HELMRELEASE_GROUP,
            version=HELMRELEASE_VERSION,
            namespace="test-ns",
            plural=HELMRELEASE_PLURAL,
            name=f"skuld-{sample_session.id}",
        )

    async def test_stop_returns_false_on_not_found(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.delete_namespaced_custom_object.side_effect = Exception("404 NotFound")
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.stop(sample_session)

        assert result is False


class TestFluxPodManagerStatus:
    async def test_status_running(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                ],
            },
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.status(sample_session)

        assert result == SessionStatus.RUNNING

    async def test_status_starting(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "Progressing"},
                ],
            },
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.status(sample_session)

        assert result == SessionStatus.STARTING

    async def test_status_failed(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "InstallFailed"},
                ],
            },
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.status(sample_session)

        assert result == SessionStatus.FAILED

    async def test_status_stopped_when_not_found(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.get_namespaced_custom_object.side_effect = Exception("404 NotFound")
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.status(sample_session)

        assert result == SessionStatus.STOPPED

    async def test_status_starting_when_no_conditions(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {},
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.status(sample_session)

        assert result == SessionStatus.STARTING

    async def test_status_failed_when_release_vanishes_mid_provisioning(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        # The release was created (session is PROVISIONING) and is now gone:
        # Flux remediation uninstalled it after a failed install. STARTING
        # would spin forever and STOPPED would read as a clean completion.
        provisioning = sample_session.with_status(SessionStatus.PROVISIONING)
        mock_api.get_namespaced_custom_object.side_effect = Exception("404 NotFound")
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.status(provisioning)

        assert result == SessionStatus.FAILED


class TestFluxPodManagerWaitForReady:
    async def test_wait_returns_immediately_when_running(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.wait_for_ready(sample_session, timeout=10)

        assert result == SessionStatus.RUNNING

    async def test_wait_returns_immediately_when_failed(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "InstallFailed"},
                ],
            },
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.wait_for_ready(sample_session, timeout=10)

        assert result == SessionStatus.FAILED

    async def test_wait_fails_fast_when_release_is_deleted(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        # Flux uninstalling the release mid-provision surfaces as a DELETED
        # watch event — terminal, no waiting out the timeout.
        mock_api.get_namespaced_custom_object.return_value = {"status": {}}

        class _FakeWatch:
            async def stream(self, *args, **kwargs):
                yield {"type": "DELETED", "object": {"status": {}}}

            def stop(self) -> None:
                return None

        import sys
        from types import SimpleNamespace

        fake_watch_module = SimpleNamespace(Watch=_FakeWatch)
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            patch.dict(
                sys.modules,
                {
                    "kubernetes_asyncio": SimpleNamespace(watch=fake_watch_module),
                    "kubernetes_asyncio.watch": fake_watch_module,
                },
            ),
        ):
            result = await pod_manager.wait_for_ready(sample_session, timeout=10)

        assert result == SessionStatus.FAILED


class TestFluxPodManagerClose:
    async def test_close_when_no_client(self, pod_manager: FluxPodManager):
        await pod_manager.close()
        assert pod_manager._api_client is None

    async def test_close_closes_api_client(self, pod_manager: FluxPodManager):
        mock_client = AsyncMock()
        pod_manager._api_client = mock_client
        await pod_manager.close()
        mock_client.close.assert_called_once()
        assert pod_manager._api_client is None


class TestFluxPodManagerKwargsPattern:
    def test_accepts_extra_kwargs_without_error(self):
        pm = FluxPodManager(
            namespace="test",
            unknown_key="ignored",
            another_key=42,
        )
        assert pm._namespace == "test"

    def test_defaults_are_sensible(self):
        pm = FluxPodManager()
        assert pm._namespace == "default"
        assert pm._chart_name == "skuld"
        assert pm._base_domain == "volundr.local"


class TestFluxPodManagerGatewayEndpoints:
    """Tests for path-based endpoint generation with gateway_domain."""

    def test_host_based_endpoints_without_gateway_domain(self):
        pm = FluxPodManager(
            base_domain="volundr.example.com",
            chat_scheme="wss",
            code_scheme="https",
        )
        assert pm._chat_endpoint("my-session") == ("wss://my-session.volundr.example.com/session")
        assert pm._code_endpoint("my-session") == ("https://my-session.volundr.example.com/")

    def test_path_based_endpoints_with_gateway_domain(self):
        pm = FluxPodManager(
            gateway_domain="gateway.example.com",
            base_domain="volundr.example.com",
        )
        sid = "abc-123"
        assert pm._chat_endpoint("my-session", sid) == (
            "wss://gateway.example.com/s/abc-123/session"
        )
        assert pm._code_endpoint("my-session", sid) == ("https://gateway.example.com/s/abc-123/")

    async def test_start_returns_path_based_endpoints(
        self,
        sample_session: Session,
        mock_api,
    ):
        pm = FluxPodManager(
            namespace="test-ns",
            gateway_domain="gateway.example.com",
            base_domain="volundr.example.com",
        )
        spec = make_spec()
        with patch.object(pm, "_get_api", return_value=mock_api):
            result = await pm.start(sample_session, spec)

        session_id = str(sample_session.id)
        assert result.chat_endpoint == (f"wss://gateway.example.com/s/{session_id}/session")
        assert result.code_endpoint == (f"https://gateway.example.com/s/{session_id}/")


class TestFluxPodManagerSpecValues:
    """Tests for spec values pass-through to Skuld Helm values."""

    async def test_spec_values_passed_to_helmrelease(
        self,
        sample_session: Session,
        mock_api,
    ):
        pm = FluxPodManager(
            namespace="test-ns",
            base_domain="volundr.example.com",
        )
        spec = make_spec(
            gateway={
                "enabled": True,
                "name": "my-gw",
                "namespace": "my-ns",
            },
            git={"repoUrl": "https://github.com/org/repo", "branch": "main"},
        )
        with patch.object(pm, "_get_api", return_value=mock_api):
            await pm.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        assert body["spec"]["values"]["gateway"]["enabled"] is True
        assert body["spec"]["values"]["gateway"]["name"] == "my-gw"
        assert body["spec"]["values"]["git"]["repoUrl"] == "https://github.com/org/repo"

    async def test_resource_overrides_reach_devrunner_values(
        self,
        sample_session: Session,
        mock_api,
    ):
        """Verify user resource config flows through to devrunner Helm values."""
        pm = FluxPodManager(
            namespace="test-ns",
            base_domain="volundr.example.com",
            session_defaults={
                "localServices": {
                    "devrunner": {
                        "resources": {
                            "requests": {"memory": "512Mi", "cpu": "100m"},
                            "limits": {"memory": "4Gi", "cpu": "2000m"},
                        }
                    }
                },
            },
        )
        spec = make_spec(
            localServices={
                "devrunner": {
                    "resources": {
                        "requests": {"cpu": "8", "memory": "32Gi"},
                        "limits": {"cpu": "8", "memory": "32Gi", "nvidia.com/gpu": "4"},
                    }
                }
            },
            nodeSelector={"nvidia.com/gpu.product": "H100"},
            tolerations=[{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}],
            runtimeClassName="nvidia",
        )
        with patch.object(pm, "_get_api", return_value=mock_api):
            await pm.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        values = body["spec"]["values"]
        # Devrunner resources should be overridden
        dr = values["localServices"]["devrunner"]["resources"]
        assert dr["requests"]["cpu"] == "8"
        assert dr["requests"]["memory"] == "32Gi"
        assert dr["limits"]["nvidia.com/gpu"] == "4"
        # Pod-level scheduling
        assert values["nodeSelector"]["nvidia.com/gpu.product"] == "H100"
        assert len(values["tolerations"]) == 1
        assert values["runtimeClassName"] == "nvidia"

    async def test_empty_spec_uses_only_defaults(
        self,
        sample_session: Session,
        mock_api,
    ):
        pm = FluxPodManager(
            namespace="test-ns",
            base_domain="volundr.example.com",
        )
        spec = make_spec()
        with patch.object(pm, "_get_api", return_value=mock_api):
            await pm.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        assert "gateway" not in body["spec"]["values"]

    async def test_pod_spec_translated_to_helm_values(
        self,
        sample_session: Session,
        mock_api,
    ):
        """PodSpecAdditions translate to Skuld Helm values."""
        pm = FluxPodManager(
            namespace="test-ns",
            base_domain="volundr.example.com",
        )
        pod_spec = PodSpecAdditions(
            env=({"name": "SKULD__MESH__ENABLED", "value": "true"},),
            volumes=({"name": "csi-vol", "csi": {"driver": "secrets-store.csi.k8s.io"}},),
            volume_mounts=({"name": "csi-vol", "mountPath": "/run/secrets/user"},),
            init_containers=({"name": "write-ravn-cfg-coder", "image": "busybox"},),
            extra_containers=({"name": "ravn-coder", "image": "ghcr.io/niuulabs/ravn:test"},),
            service_account="skuld-user-1",
        )
        spec = SessionSpec(
            values={"session": {"id": str(sample_session.id)}},
            pod_spec=pod_spec,
        )
        with patch.object(pm, "_get_api", return_value=mock_api):
            await pm.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        values = body["spec"]["values"]
        # The Codex broker is delivered to every session container the same way
        # OpenShell merges it into its sandbox — see _inject_codex_auth_env.
        assert values["envVars"] == [
            {"name": "SKULD__MESH__ENABLED", "value": "true"},
            {
                "name": "SKULD__CODEX_AUTH__ADAPTER",
                "value": "skuld.codex_auth.VolundrCodexAuthProvider",
            },
            {"name": "SKULD__CODEX_AUTH__KWARGS", "value": "{}"},
        ]
        assert len(values["extraVolumes"]) == 1
        assert values["extraVolumes"][0]["name"] == "csi-vol"
        assert len(values["extraVolumeMounts"]) == 1
        assert values["extraVolumeMounts"][0]["mountPath"] == "/run/secrets/user"
        assert values["extraInitContainers"] == [
            {"name": "write-ravn-cfg-coder", "image": "busybox"}
        ]
        # Persona sidecars build the Codex transport themselves, so the broker
        # has to reach them too — a single sandbox never needed this.
        assert values["extraContainers"] == [
            {
                "name": "ravn-coder",
                "image": "ghcr.io/niuulabs/ravn:test",
                "env": [
                    {
                        "name": "SKULD__CODEX_AUTH__ADAPTER",
                        "value": "skuld.codex_auth.VolundrCodexAuthProvider",
                    },
                    {"name": "SKULD__CODEX_AUTH__KWARGS", "value": "{}"},
                ],
            }
        ]
        assert values["serviceAccountName"] == "skuld-user-1"

    async def test_pod_spec_env_overrides_matching_default_env_var(
        self,
        sample_session: Session,
        mock_api,
    ):
        """PodSpec env can replace matching default envVars by name."""
        pm = FluxPodManager(
            namespace="test-ns",
            base_domain="volundr.example.com",
            session_defaults={
                "envVars": [
                    {"name": "SKULD__CLAUDE_AUTH", "value": "api_key"},
                    {"name": "SKULD__MESH__ENABLED", "value": "false"},
                ]
            },
        )
        spec = SessionSpec(
            values={"session": {"id": str(sample_session.id)}},
            pod_spec=PodSpecAdditions(env=({"name": "SKULD__MESH__ENABLED", "value": "true"},)),
        )
        with patch.object(pm, "_get_api", return_value=mock_api):
            await pm.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        assert body["spec"]["values"]["envVars"] == [
            {"name": "SKULD__CLAUDE_AUTH", "value": "api_key"},
            {"name": "SKULD__MESH__ENABLED", "value": "true"},
            {
                "name": "SKULD__CODEX_AUTH__ADAPTER",
                "value": "skuld.codex_auth.VolundrCodexAuthProvider",
            },
            {"name": "SKULD__CODEX_AUTH__KWARGS", "value": "{}"},
        ]

    async def test_empty_pod_spec_no_extra_values(
        self,
        sample_session: Session,
        mock_api,
    ):
        """Empty PodSpecAdditions produces no extraVolumes/extraVolumeMounts."""
        pm = FluxPodManager(
            namespace="test-ns",
            base_domain="volundr.example.com",
        )
        spec = make_spec()
        with patch.object(pm, "_get_api", return_value=mock_api):
            await pm.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        values = body["spec"]["values"]
        assert "extraVolumes" not in values
        assert "extraVolumeMounts" not in values
        assert "serviceAccountName" not in values


class TestFluxPodManagerStartErrorHandling:
    """Tests for start() error handling paths."""

    async def test_start_patches_on_409_conflict(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """409 error from create triggers a patch instead."""
        spec = make_spec()
        mock_api.create_namespaced_custom_object.side_effect = Exception("409 Conflict")
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.start(sample_session, spec)

        mock_api.patch_namespaced_custom_object.assert_called_once()
        patch_kwargs = mock_api.patch_namespaced_custom_object.call_args[1]
        assert patch_kwargs["name"] == f"skuld-{sample_session.id}"
        assert patch_kwargs["namespace"] == "test-ns"
        assert patch_kwargs["_content_type"] == "application/merge-patch+json"
        assert result.pod_name == f"skuld-{sample_session.id}"

    async def test_start_patches_on_already_exists(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """AlreadyExists error from create triggers a patch instead."""
        spec = make_spec()
        mock_api.create_namespaced_custom_object.side_effect = Exception(
            "AlreadyExists: helmreleases already exists"
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            result = await pod_manager.start(sample_session, spec)

        mock_api.patch_namespaced_custom_object.assert_called_once()
        assert result.pod_name == f"skuld-{sample_session.id}"

    async def test_start_raises_on_non_conflict_error(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """Non-409/AlreadyExists errors from create are re-raised."""
        spec = make_spec()
        mock_api.create_namespaced_custom_object.side_effect = Exception(
            "500 Internal Server Error"
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            with pytest.raises(Exception, match="500 Internal Server Error"):
                await pod_manager.start(sample_session, spec)

        mock_api.patch_namespaced_custom_object.assert_not_called()

    async def test_start_with_pod_labels_and_annotations(self, sample_session: Session, mock_api):
        """PodSpecAdditions with labels and annotations are translated."""
        pm = FluxPodManager(namespace="test-ns", base_domain="volundr.example.com")
        pod_spec = PodSpecAdditions(
            labels={"app": "test", "env": "dev"},
            annotations={"prometheus.io/scrape": "true"},
        )
        spec = SessionSpec(
            values={"session": {"id": str(sample_session.id)}},
            pod_spec=pod_spec,
        )
        with patch.object(pm, "_get_api", return_value=mock_api):
            await pm.start(sample_session, spec)

        body = mock_api.create_namespaced_custom_object.call_args[1]["body"]
        values = body["spec"]["values"]
        assert values["podLabels"] == {"app": "test", "env": "dev"}
        assert values["podAnnotations"] == {"prometheus.io/scrape": "true"}


class TestFluxPodManagerStopErrorHandling:
    """Tests for stop() error re-raise path."""

    async def test_stop_reraises_non_404_error(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """Non-404/NotFound errors from delete are re-raised."""
        mock_api.delete_namespaced_custom_object.side_effect = Exception(
            "500 Internal Server Error"
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            with pytest.raises(Exception, match="500 Internal Server Error"):
                await pod_manager.stop(sample_session)

    async def test_stop_reraises_permission_error(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """403 Forbidden errors from delete are re-raised."""
        mock_api.delete_namespaced_custom_object.side_effect = Exception("403 Forbidden")
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            with pytest.raises(Exception, match="403 Forbidden"):
                await pod_manager.stop(sample_session)


class TestFluxPodManagerStatusErrorHandling:
    """Tests for status() error re-raise path."""

    async def test_status_reraises_non_404_error(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """Non-404/NotFound errors from get are re-raised."""
        mock_api.get_namespaced_custom_object.side_effect = Exception("500 Internal Server Error")
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            with pytest.raises(Exception, match="500 Internal Server Error"):
                await pod_manager.status(sample_session)

    async def test_status_reraises_permission_error(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """403 Forbidden errors from get are re-raised."""
        mock_api.get_namespaced_custom_object.side_effect = Exception("403 Forbidden")
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            with pytest.raises(Exception, match="403 Forbidden"):
                await pod_manager.status(sample_session)


class TestFluxPodManagerMapStatusReasons:
    """Tests for _map_status with various failure reasons."""

    def test_map_status_upgrade_failed(self):
        obj = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "UpgradeFailed"},
                ],
            },
        }
        assert FluxPodManager._map_status(obj) == SessionStatus.FAILED

    def test_map_status_reconciliation_failed(self):
        obj = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "ReconciliationFailed"},
                ],
            },
        }
        assert FluxPodManager._map_status(obj) == SessionStatus.FAILED

    def test_map_status_install_failed(self):
        obj = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "InstallFailed"},
                ],
            },
        }
        assert FluxPodManager._map_status(obj) == SessionStatus.FAILED

    def test_map_status_unknown_reason_returns_starting(self):
        """Non-failure reasons with status False return STARTING."""
        obj = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "ArtifactFailed"},
                ],
            },
        }
        assert FluxPodManager._map_status(obj) == SessionStatus.STARTING

    def test_map_status_no_reason_returns_starting(self):
        """Ready=False with no reason returns STARTING."""
        obj = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False"},
                ],
            },
        }
        assert FluxPodManager._map_status(obj) == SessionStatus.STARTING

    def test_map_status_skips_non_ready_conditions(self):
        """Non-Ready conditions are ignored."""
        obj = {
            "status": {
                "conditions": [
                    {"type": "Reconciling", "status": "True"},
                    {"type": "Ready", "status": "True"},
                ],
            },
        }
        assert FluxPodManager._map_status(obj) == SessionStatus.RUNNING

    def test_map_status_no_status_key(self):
        """Missing status key entirely returns STARTING."""
        assert FluxPodManager._map_status({}) == SessionStatus.STARTING

    def test_map_status_empty_conditions_list(self):
        """Empty conditions list returns STARTING."""
        obj = {"status": {"conditions": []}}
        assert FluxPodManager._map_status(obj) == SessionStatus.STARTING


class TestFluxPodManagerWaitForReadyErrors:
    """Tests for wait_for_ready watch error handling."""

    async def test_wait_reraises_watch_error(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """Watch stream errors are re-raised after logging."""
        # Make status() return STARTING so it enters the watch path
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {"conditions": []},
        }

        async def _failing_stream(*args, **kwargs):
            raise ConnectionError("stream disconnected")
            yield  # make it an async generator that raises

        mock_watch = MagicMock()
        mock_watch.stream = _failing_stream
        mock_watch.stop = MagicMock()

        mock_watch_mod = MagicMock()
        mock_watch_mod.Watch.return_value = mock_watch
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            patch.dict(
                "sys.modules",
                {
                    "kubernetes_asyncio.watch": mock_watch_mod,
                    "kubernetes_asyncio": MagicMock(watch=mock_watch_mod),
                },
            ),
        ):
            with pytest.raises(ConnectionError, match="stream disconnected"):
                await pod_manager.wait_for_ready(sample_session, timeout=5)

        mock_watch.stop.assert_called_once()

    async def test_wait_returns_current_status_on_timeout(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """A watch timeout does not override a still-progressing HelmRelease."""
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {"conditions": []},
        }

        async def _empty_stream(*args, **kwargs):
            return
            yield  # make it an async generator that yields nothing

        mock_watch = MagicMock()
        mock_watch.stream = _empty_stream
        mock_watch.stop = MagicMock()

        mock_watch_mod = MagicMock()
        mock_watch_mod.Watch.return_value = mock_watch
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            patch.dict(
                "sys.modules",
                {
                    "kubernetes_asyncio.watch": mock_watch_mod,
                    "kubernetes_asyncio": MagicMock(watch=mock_watch_mod),
                },
            ),
        ):
            result = await pod_manager.wait_for_ready(sample_session, timeout=5)

        assert result == SessionStatus.STARTING

    async def test_wait_skips_non_dict_events(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ):
        """Non-dict event objects are skipped."""
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {"conditions": []},
        }

        async def _stream_with_non_dict(*args, **kwargs):
            yield {"object": "not-a-dict"}
            yield {
                "object": {
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                },
            }

        mock_watch = MagicMock()
        mock_watch.stream = _stream_with_non_dict
        mock_watch.stop = MagicMock()

        mock_watch_mod = MagicMock()
        mock_watch_mod.Watch.return_value = mock_watch
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            patch.dict(
                "sys.modules",
                {
                    "kubernetes_asyncio.watch": mock_watch_mod,
                    "kubernetes_asyncio": MagicMock(watch=mock_watch_mod),
                },
            ),
        ):
            result = await pod_manager.wait_for_ready(sample_session, timeout=5)

        assert result == SessionStatus.RUNNING


class TestFluxPodManagerGetApi:
    """Tests for _get_api lazy initialization."""

    async def test_get_api_loads_incluster_config(self, pod_manager: FluxPodManager):
        """When incluster config succeeds, it is used."""
        mock_client_instance = MagicMock()
        mock_config = MagicMock()
        mock_config.ConfigException = Exception
        mock_config.load_kube_config = AsyncMock()
        mock_client_mod = MagicMock()
        mock_client_mod.ApiClient.return_value = mock_client_instance
        mock_custom_api = MagicMock()
        mock_client_mod.CustomObjectsApi.return_value = mock_custom_api

        with patch.dict(
            "sys.modules",
            {
                "kubernetes_asyncio": MagicMock(client=mock_client_mod, config=mock_config),
                "kubernetes_asyncio.client": mock_client_mod,
                "kubernetes_asyncio.config": mock_config,
            },
        ):
            await pod_manager._get_api()

        mock_config.load_incluster_config.assert_called_once()
        mock_config.load_kube_config.assert_not_called()
        assert pod_manager._api_client is mock_client_instance

    async def test_get_api_falls_back_to_kube_config(self):
        """When incluster config fails, falls back to kube config."""
        pm = FluxPodManager()
        mock_client_instance = MagicMock()
        mock_config = MagicMock()
        mock_config.ConfigException = Exception
        mock_config.load_incluster_config.side_effect = Exception("not in cluster")
        mock_config.load_kube_config = AsyncMock()
        mock_client_mod = MagicMock()
        mock_client_mod.ApiClient.return_value = mock_client_instance
        mock_custom_api = MagicMock()
        mock_client_mod.CustomObjectsApi.return_value = mock_custom_api

        with patch.dict(
            "sys.modules",
            {
                "kubernetes_asyncio": MagicMock(client=mock_client_mod, config=mock_config),
                "kubernetes_asyncio.client": mock_client_mod,
                "kubernetes_asyncio.config": mock_config,
            },
        ):
            await pm._get_api()

        mock_config.load_kube_config.assert_called_once()
        assert pm._api_client is mock_client_instance

    async def test_get_api_reuses_existing_client(self, pod_manager: FluxPodManager):
        """When _api_client is already set, does not re-initialize."""
        existing_client = MagicMock()
        pod_manager._api_client = existing_client
        mock_client_mod = MagicMock()
        mock_custom_api = MagicMock()
        mock_client_mod.CustomObjectsApi.return_value = mock_custom_api

        with patch.dict(
            "sys.modules",
            {
                "kubernetes_asyncio": MagicMock(client=mock_client_mod),
                "kubernetes_asyncio.client": mock_client_mod,
            },
        ):
            result = await pod_manager._get_api()

        assert pod_manager._api_client is existing_client
        assert result is mock_custom_api


class TestFluxResidentRuntimeController:
    def test_supports_only_ravn_helmrelease_profiles(self, pod_manager: FluxPodManager) -> None:
        profile = _resident_profile()

        assert pod_manager.supports(profile)
        assert not pod_manager.supports(
            profile.model_copy(update={"engine": ResidentEngine.HERMES})
        )

    async def test_deploy_uses_existing_skuld_chart_and_runtime_metadata(
        self,
        pod_manager: FluxPodManager,
        mock_api,
    ) -> None:
        runtime = _resident_runtime()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            observation = await pod_manager.deploy(runtime, _resident_profile())

        body = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]
        values = body["spec"]["values"]
        assert body["metadata"]["name"] == f"resident-{runtime.id}"
        assert body["spec"]["chart"]["spec"]["chart"] == "skuld"
        assert values["resident"]["enabled"] is True
        assert values["resident"]["persona"] == "product-steward"
        assert values["resident"]["llm"] == {"provider": "openai", "model": "gpt-5.6"}
        assert values["session"]["ownerId"] == "user-a"
        assert values["session"]["model"] == "gpt-5.6"
        assert values["persistence"]["existingClaim"] == "resident-workspaces"
        assert values["podAnnotations"]["niuu.world/tenant-id"] == "tenant-a"
        assert observation.observed_state is ResidentObservedState.DEPLOYING
        assert observation.backend_ref["kind"] == "HelmRelease"
        assert observation.endpoints[0].protocol == "skuld-v1"

    async def test_deploy_advertises_voice_when_the_profile_enables_it(
        self,
        pod_manager: FluxPodManager,
        mock_api,
    ) -> None:
        runtime = _resident_runtime()
        profile = _resident_profile()
        profile.deployment["values"]["resident"]["voice"] = {"enabled": True}
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            observation = await pod_manager.deploy(runtime, profile)

        voice = [e for e in observation.endpoints if e.kind == "voice"]
        assert [(e.protocol, e.url) for e in voice] == [
            ("agent-service-voice-v1", f"https://{runtime.id}.volundr.example.com/voice")
        ]

    async def test_deploy_without_voice_advertises_chat_only(
        self,
        pod_manager: FluxPodManager,
        mock_api,
    ) -> None:
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            observation = await pod_manager.deploy(_resident_runtime(), _resident_profile())

        assert [e.kind for e in observation.endpoints] == ["chat"]

    def test_observation_reads_voice_from_the_release_values(
        self, pod_manager: FluxPodManager
    ) -> None:
        runtime = _resident_runtime()
        pod_manager._gateway_domain = "gateway.example.com"
        helmrelease = {"spec": {"values": {"resident": {"voice": {"enabled": True}}}}}

        observation = pod_manager._resident_observation(runtime, helmrelease, None)

        assert observation.endpoints[-1].url == f"https://gateway.example.com/s/{runtime.id}/voice"

    @pytest.mark.parametrize(
        "values",
        [
            {},
            {"resident": None},
            {"resident": {"voice": "on"}},
            {"resident": {"voice": {"enabled": "true"}}},
        ],
    )
    def test_only_an_explicit_true_enables_voice(self, values: dict) -> None:
        assert FluxPodManager._values_enable_voice(values) is False

    async def test_deploy_omits_realm_values_without_realm_id(
        self,
        pod_manager: FluxPodManager,
        mock_api,
    ) -> None:
        runtime = _resident_runtime()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _resident_profile())

        body = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]
        assert "realm" not in body["spec"]["values"]["resident"]

    async def test_deploy_with_realm_id_requires_a_configured_repository(
        self,
        pod_manager: FluxPodManager,
        mock_api,
    ) -> None:
        runtime = _resident_runtime(realm_id=uuid4())
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            pytest.raises(RuntimeError, match="no realm repository configured"),
        ):
            await pod_manager.deploy(runtime, _resident_profile())

    async def test_deploy_renders_the_resolved_realm_slug(
        self,
        pod_manager: FluxPodManager,
        mock_api,
    ) -> None:
        realm_id = uuid4()

        class _FakeRealm:
            slug = "workshop"

        class _FakeRealmRepository:
            async def get_realm(self, realm_ref):
                return _FakeRealm() if realm_ref == realm_id else None

        pod_manager.set_realm_repository(_FakeRealmRepository())
        runtime = _resident_runtime(realm_id=realm_id)
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _resident_profile())

        body = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]
        assert body["spec"]["values"]["resident"]["realm"] == {"slug": "workshop"}

    async def test_reconcile_combines_flux_and_workload_state(
        self,
        pod_manager: FluxPodManager,
    ) -> None:
        runtime = _resident_runtime()
        helmrelease = {
            "spec": {
                "chart": {"spec": {"chart": "skuld", "version": "0.38.0"}},
                "values": {"replicaCount": 1},
            },
            "status": {
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "True",
                        "reason": "ReconciliationSucceeded",
                        "message": "Release reconciliation succeeded",
                    }
                ]
            },
        }
        deployment = {
            "metadata": {"name": "resident-muninn"},
            "status": {
                "available_replicas": 1,
                "conditions": [
                    {
                        "type": "Available",
                        "status": "True",
                        "reason": "MinimumReplicasAvailable",
                    }
                ],
            },
        }
        with (
            patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=helmrelease)),
            patch.object(
                pod_manager,
                "_get_resident_deployment",
                AsyncMock(return_value=deployment),
            ),
        ):
            observation = await pod_manager.reconcile(runtime, _resident_profile())

        assert observation.observed_state is ResidentObservedState.ACTIVE
        assert observation.backend_ref["deploymentName"] == "resident-muninn"
        assert observation.backend_ref["platformVersion"] == "skuld 0.38.0"
        assert {condition.type for condition in observation.conditions} == {
            "HelmReleaseReady",
            "DeploymentAvailable",
        }

    async def test_reconcile_recreates_a_missing_owned_release(
        self,
        pod_manager: FluxPodManager,
    ) -> None:
        runtime = _resident_runtime()
        pending = ResidentRuntimeObservation(observed_state=ResidentObservedState.DEPLOYING)
        with (
            patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=None)),
            patch.object(
                pod_manager,
                "deploy",
                AsyncMock(return_value=pending),
            ) as deploy,
        ):
            observation = await pod_manager.reconcile(runtime, _resident_profile())

        assert observation is pending
        deploy.assert_awaited_once_with(runtime, _resident_profile())

    async def test_reconcile_repairs_replica_drift(
        self,
        pod_manager: FluxPodManager,
    ) -> None:
        runtime = _resident_runtime(desired_state=ResidentDesiredState.SUSPENDED)
        helmrelease = {"spec": {"values": {"replicaCount": 1}}, "status": {}}
        with (
            patch.object(
                pod_manager,
                "_get_helmrelease",
                AsyncMock(return_value=helmrelease),
            ),
            patch.object(
                pod_manager,
                "_get_resident_deployment",
                AsyncMock(return_value=None),
            ),
            patch.object(pod_manager, "_set_replica_count", AsyncMock()) as set_replicas,
        ):
            observation = await pod_manager.reconcile(runtime, _resident_profile())

        set_replicas.assert_awaited_once_with(runtime, 0)
        assert observation.observed_state is ResidentObservedState.DEPLOYING

    async def test_suspend_resume_restart_and_delete_patch_owned_release(
        self,
        pod_manager: FluxPodManager,
    ) -> None:
        runtime = _resident_runtime(desired_state=ResidentDesiredState.SUSPENDED)
        observation = ResidentRuntimeObservation(
            observed_state=ResidentObservedState.SUSPENDED,
        )
        with (
            patch.object(pod_manager, "_patch_helmrelease", AsyncMock()) as patch_release,
            patch.object(
                pod_manager,
                "_get_helmrelease",
                AsyncMock(return_value={"metadata": {}, "spec": {"values": {}}}),
            ),
            patch.object(pod_manager, "_replace_helmrelease", AsyncMock()) as replace_release,
            patch.object(
                pod_manager,
                "_observe_after_lifecycle",
                AsyncMock(return_value=observation),
            ),
            patch.object(
                pod_manager,
                "_delete_helmrelease",
                AsyncMock(return_value=True),
            ) as delete_release,
        ):
            await pod_manager.suspend(runtime)
            await pod_manager.resume(runtime)
            await pod_manager.restart(
                runtime.model_copy(update={"desired_state": ResidentDesiredState.RUNNING}),
                _resident_profile(),
            )
            deleted = await pod_manager.delete(runtime)
            assert deleted

        release_name = f"resident-{runtime.id}"
        assert patch_release.await_args_list[0].args == (
            release_name,
            {"spec": {"values": {"replicaCount": 0}}},
        )
        assert patch_release.await_args_list[1].args == (
            release_name,
            {"spec": {"values": {"replicaCount": 1}}},
        )
        restart_body = replace_release.await_args.args[1]
        assert "niuu.world/restarted-at" in restart_body["spec"]["values"]["podAnnotations"]
        delete_release.assert_awaited_once_with(release_name)

    async def test_lifecycle_patch_uses_merge_patch_content_type(
        self,
        pod_manager: FluxPodManager,
        mock_api,
    ) -> None:
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager._patch_helmrelease(
                "resident-id",
                {"spec": {"values": {"replicaCount": 0}}},
            )

        kwargs = mock_api.patch_namespaced_custom_object.call_args.kwargs
        assert kwargs["body"] == {"spec": {"values": {"replicaCount": 0}}}
        assert kwargs["_content_type"] == "application/merge-patch+json"

    def test_suspend_observation_waits_for_workload_to_scale_down(
        self,
        pod_manager: FluxPodManager,
    ) -> None:
        runtime = _resident_runtime(desired_state=ResidentDesiredState.SUSPENDED)
        helmrelease = {"spec": {"values": {"replicaCount": 0}}, "status": {}}

        still_running = {
            "spec": {"replicas": 0},
            "status": {"available_replicas": 1},
        }
        scaled_down = {
            "spec": {"replicas": 0},
            "status": {"available_replicas": 0},
        }

        assert (
            pod_manager._observed_state(runtime, helmrelease, still_running)
            is ResidentObservedState.DEPLOYING
        )
        assert (
            pod_manager._observed_state(runtime, helmrelease, scaled_down)
            is ResidentObservedState.SUSPENDED
        )


class _ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"({status})")
        self.status = status


def _own_volume_profile(**deployment) -> ResidentDeploymentProfile:
    """The test profile with no claim of its own: the resident gets its own PVCs."""
    profile = _resident_profile()
    del profile.deployment["values"]["persistence"]["existingClaim"]
    profile.deployment.update(deployment)
    return profile


def _created_claims(core_api: AsyncMock) -> dict[str, dict]:
    return {
        call.kwargs["body"]["name"]: call.kwargs["body"]
        for call in core_api.create_namespaced_persistent_volume_claim.await_args_list
    }


class TestFluxResidentOwnVolumes:
    """Each resident gets its own home and workspace PVCs, created and deleted here."""

    async def test_deploy_creates_own_home_and_workspace_before_the_release(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        runtime = _resident_runtime()
        order: list[str] = []
        core_api.create_namespaced_persistent_volume_claim.side_effect = lambda **kwargs: (
            order.append(kwargs["body"]["name"])
        )
        mock_api.create_namespaced_custom_object.side_effect = lambda **kwargs: order.append(
            kwargs["body"]["metadata"]["name"]
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _own_volume_profile())

        home = f"resident-{runtime.id}-home"
        workspace = f"resident-{runtime.id}-workspace"
        assert order == [home, workspace, f"resident-{runtime.id}"]
        claims = _created_claims(core_api)
        for name, kind in ((home, "home"), (workspace, "workspace")):
            assert claims[name]["namespace"] == "test-ns"
            assert claims[name]["storage_class"] is None
            assert claims[name]["access_mode"] == "ReadWriteOnce"
            assert claims[name]["labels"] == {
                "app.kubernetes.io/managed-by": "volundr",
                "volundr/pvc-type": f"resident-{kind}",
                "niuu.world/resident-id": str(runtime.id),
                "niuu.world/owner-id": "user-a",
                "niuu.world/tenant-id": "tenant-a",
            }
        assert claims[home]["storage"] == "1Gi"
        assert claims[workspace]["storage"] == "2Gi"

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["homeVolume"] == {"enabled": True, "existingClaim": home}
        assert values["persistence"] == {
            "enabled": True,
            "emptyDir": False,
            "existingClaim": workspace,
        }
        # No pinning: nothing ties the resident to a node or to the owner's
        # session pods (whose podAffinity matches on volundr/owner).
        assert "volundr/owner" not in values["podLabels"]
        for affinity_key in ("affinity", "nodeSelector", "nodeName"):
            assert affinity_key not in values

    async def test_profile_volumes_block_and_controller_defaults_size_the_claims(
        self,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        pod_manager = FluxPodManager(
            namespace="test-ns",
            resident_home_size="5Gi",
            resident_workspace_size="8Gi",
            resident_storage_class="standard",
            resident_access_mode="ReadWriteOncePod",
        )
        runtime = _resident_runtime()
        profile = _own_volume_profile(
            volumes={"home": {"storageClass": "harvester", "size": "64Gi"}},
        )
        profile.deployment["values"]["homeVolume"] = {
            "mountPath": "/volundr/home",
            "credentialFiles": {"destDir": ".codex"},
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, profile)

        claims = _created_claims(core_api)
        home = claims[f"resident-{runtime.id}-home"]
        workspace = claims[f"resident-{runtime.id}-workspace"]
        assert (home["storage"], home["storage_class"], home["access_mode"]) == (
            "64Gi",
            "harvester",
            "ReadWriteOncePod",
        )
        assert (workspace["storage"], workspace["storage_class"], workspace["access_mode"]) == (
            "8Gi",
            "standard",
            "ReadWriteOncePod",
        )
        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["homeVolume"] == {
            "enabled": True,
            "existingClaim": f"resident-{runtime.id}-home",
            "mountPath": "/volundr/home",
            "credentialFiles": {"destDir": ".codex"},
        }

    async def test_profile_existing_claims_are_kept_and_nothing_is_created(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        profile = _resident_profile()
        profile.deployment["values"]["homeVolume"] = {
            "enabled": True,
            "existingClaim": "volundr-user-a-home",
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), profile)

        core_api.create_namespaced_persistent_volume_claim.assert_not_awaited()
        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["homeVolume"]["existingClaim"] == "volundr-user-a-home"
        assert values["persistence"]["existingClaim"] == "resident-workspaces"

    async def test_disabled_home_and_emptydir_workspace_get_no_claim(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        profile = _own_volume_profile()
        profile.deployment["values"]["homeVolume"] = {"enabled": False}
        profile.deployment["values"]["persistence"]["emptyDir"] = True
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), profile)

        core_api.create_namespaced_persistent_volume_claim.assert_not_awaited()
        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["homeVolume"] == {"enabled": False}
        assert "existingClaim" not in values["persistence"]

    async def test_redeploy_reuses_the_residents_own_claims(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        runtime = _resident_runtime()
        core_api.create_namespaced_persistent_volume_claim.side_effect = _ApiError(409)
        core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
            metadata=SimpleNamespace(
                labels={
                    "app.kubernetes.io/managed-by": "volundr",
                    "niuu.world/resident-id": str(runtime.id),
                }
            )
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _own_volume_profile())

        assert core_api.read_namespaced_persistent_volume_claim.await_count == 2
        mock_api.create_namespaced_custom_object.assert_awaited_once()

    @pytest.mark.parametrize(
        ("managed_by", "same_resident"),
        [(None, False), ("volundr", False), (None, True)],
        ids=["unmanaged-other-resident", "another-resident", "unmanaged-same-resident"],
    )
    async def test_a_foreign_claim_with_the_residents_name_fails_the_deploy(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
        managed_by: str | None,
        same_resident: bool,
    ) -> None:
        runtime = _resident_runtime()
        labels = {"niuu.world/resident-id": str(runtime.id if same_resident else uuid4())}
        if managed_by:
            labels["app.kubernetes.io/managed-by"] = managed_by
        core_api.create_namespaced_persistent_volume_claim.side_effect = _ApiError(409)
        core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
            metadata=SimpleNamespace(labels=labels)
        )
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            pytest.raises(ValueError, match="is not resident"),
        ):
            await pod_manager.deploy(runtime, _own_volume_profile())

        mock_api.create_namespaced_custom_object.assert_not_awaited()

    async def test_claim_create_errors_other_than_conflict_fail_the_deploy(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        core_api.create_namespaced_persistent_volume_claim.side_effect = _ApiError(403)
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            pytest.raises(_ApiError),
        ):
            await pod_manager.deploy(_resident_runtime(), _own_volume_profile())

        mock_api.create_namespaced_custom_object.assert_not_awaited()

    @pytest.mark.parametrize(
        ("deployment", "message"),
        [
            ({"volumes": ["home"]}, "deployment.volumes must be an object"),
            ({"volumes": {"tmp": {}}}, "unknown volumes"),
            ({"volumes": {"home": "64Gi"}}, "deployment.volumes.home must be an object"),
            ({"volumes": {"workspace": {"sizeGb": 2}}}, "unknown settings"),
            ({"volumes": {"home": {"size": ""}}}, "volumes.home.size must not be empty"),
            ({"values": ["persistence"]}, "deployment.values must be an object"),
            (
                {"values": {"resident": {}, "homeVolume": "volundr-home"}},
                "deployment.values.homeVolume must be an object",
            ),
        ],
    )
    async def test_invalid_volume_configuration_is_rejected(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
        deployment: dict,
        message: str,
    ) -> None:
        profile = _own_volume_profile(**deployment)
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            pytest.raises(ValueError, match=message),
        ):
            await pod_manager.deploy(_resident_runtime(), profile)

        core_api.create_namespaced_persistent_volume_claim.assert_not_awaited()
        mock_api.create_namespaced_custom_object.assert_not_awaited()

    async def test_delete_removes_the_release_then_both_claims(
        self,
        pod_manager: FluxPodManager,
        core_api: AsyncMock,
    ) -> None:
        runtime = _resident_runtime()
        order: list[str] = []

        async def _delete_release(name: str) -> bool:
            order.append(name)
            return True

        core_api.delete_namespaced_persistent_volume_claim.side_effect = lambda **kwargs: (
            order.append(kwargs["name"])
        )
        with patch.object(pod_manager, "_delete_helmrelease", _delete_release):
            existed = await pod_manager.delete(runtime)

        assert existed is True

        assert order == [
            f"resident-{runtime.id}",
            f"resident-{runtime.id}-home",
            f"resident-{runtime.id}-workspace",
        ]
        for call in core_api.delete_namespaced_persistent_volume_claim.await_args_list:
            assert call.kwargs["namespace"] == "test-ns"

    async def test_delete_tolerates_absent_claims_and_release(
        self,
        pod_manager: FluxPodManager,
        core_api: AsyncMock,
    ) -> None:
        core_api.delete_namespaced_persistent_volume_claim.side_effect = _ApiError(404)
        with patch.object(pod_manager, "_delete_helmrelease", AsyncMock(return_value=False)):
            existed = await pod_manager.delete(_resident_runtime())

        assert existed is False

        assert core_api.delete_namespaced_persistent_volume_claim.await_count == 2

    async def test_delete_raises_when_a_claim_cannot_be_deleted(
        self,
        pod_manager: FluxPodManager,
        core_api: AsyncMock,
    ) -> None:
        core_api.delete_namespaced_persistent_volume_claim.side_effect = _ApiError(403)
        with (
            patch.object(pod_manager, "_delete_helmrelease", AsyncMock(return_value=True)),
            pytest.raises(_ApiError),
        ):
            await pod_manager.delete(_resident_runtime())

    async def test_restart_suspend_and_resume_keep_the_claims(
        self,
        pod_manager: FluxPodManager,
        core_api: AsyncMock,
    ) -> None:
        runtime = _resident_runtime()
        observation = ResidentRuntimeObservation(observed_state=ResidentObservedState.ACTIVE)
        release = {
            "metadata": {},
            "spec": {
                "values": {
                    "homeVolume": {"enabled": True, "existingClaim": f"resident-{runtime.id}-home"},
                    "persistence": {
                        "enabled": True,
                        "existingClaim": f"resident-{runtime.id}-workspace",
                    },
                }
            },
        }
        core_api.create_namespaced_persistent_volume_claim.side_effect = _ApiError(409)
        core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
            metadata=SimpleNamespace(
                labels={
                    "app.kubernetes.io/managed-by": "volundr",
                    "niuu.world/resident-id": str(runtime.id),
                }
            )
        )
        with (
            patch.object(pod_manager, "_patch_helmrelease", AsyncMock()),
            patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=release)),
            patch.object(pod_manager, "_replace_helmrelease", AsyncMock()) as replace_release,
            patch.object(
                pod_manager,
                "_observe_after_lifecycle",
                AsyncMock(return_value=observation),
            ),
        ):
            await pod_manager.restart(runtime, _own_volume_profile())
            await pod_manager.suspend(runtime)
            await pod_manager.resume(runtime)

        core_api.delete_namespaced_persistent_volume_claim.assert_not_awaited()
        restarted = replace_release.await_args.args[1]["spec"]["values"]
        assert restarted["homeVolume"] == release["spec"]["values"]["homeVolume"]
        assert restarted["persistence"] == release["spec"]["values"]["persistence"]

    async def test_core_api_shares_the_flux_api_client(self, pod_manager: FluxPodManager) -> None:
        shared_client = MagicMock()
        pod_manager._api_client = shared_client
        mock_client_mod = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "kubernetes_asyncio": MagicMock(client=mock_client_mod),
                "kubernetes_asyncio.client": mock_client_mod,
            },
        ):
            result = await _ORIGINAL_GET_CORE_API(pod_manager)

        mock_client_mod.CoreV1Api.assert_called_once_with(shared_client)
        assert result is mock_client_mod.CoreV1Api.return_value


def test_inject_workload_exchange_env_updates_builtin_and_extra_containers():
    values = {
        "volundr": {"apiUrl": "https://niuu.noatun.asgard.niuu.world/"},
        "envVars": [{"name": "EXISTING", "value": "1"}],
        "extraContainers": [
            {"name": "ravn", "env": [{"name": "RAVN_PERSONA", "value": "framer"}]},
            {
                "name": "custom",
                "env": [
                    {
                        "name": "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
                        "value": "https://override/exchange",
                    }
                ],
            },
        ],
    }

    _inject_workload_exchange_env(values)

    expected = "https://niuu.noatun.asgard.niuu.world/api/v1/tokens/workload/exchange"
    assert {"name": "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "value": expected} in values["envVars"]
    assert {"name": "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "value": expected} in values[
        "extraContainers"
    ][0]["env"]
    assert values["extraContainers"][1]["env"][0]["value"] == "https://override/exchange"


_CLUSTER_API_URL = "https://volundr.cluster.example"
_EXCHANGE_PATH = "/api/v1/tokens/workload/exchange"


def _cluster_pod_manager() -> FluxPodManager:
    """A controller whose session pods reach this cluster's own Völundr."""
    return FluxPodManager(
        namespace="test-ns",
        session_defaults={
            "volundr": {"apiUrl": _CLUSTER_API_URL},
            "affinity": {"podAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": []}},
            "image": {"tag": "session"},
        },
    )


def _env(values: dict, name: str) -> str:
    return next(entry["value"] for entry in values["envVars"] if entry["name"] == name)


class TestFluxResidentClusterVolundr:
    """Cluster-local calls go to this cluster's Völundr; the platform keeps
    platform APIs and the workload-token exchange."""

    async def _deploy(self, pod_manager: FluxPodManager, profile, mock_api) -> dict:
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), profile)
        return mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]

    async def test_resident_gets_the_session_pods_volundr_api_url(self, mock_api) -> None:
        values = await self._deploy(_cluster_pod_manager(), _resident_profile(), mock_api)

        assert values["volundr"]["apiUrl"] == _CLUSTER_API_URL
        # Only the URL is shared; session scheduling and images stay sessions'.
        assert "affinity" not in values
        assert "image" not in values
        assert values["resident"]["platform"]["baseUrl"] == "https://niuu.example.test"

    async def test_token_exchange_stays_at_the_platform(self, mock_api) -> None:
        values = await self._deploy(_cluster_pod_manager(), _resident_profile(), mock_api)

        assert _env(values, "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL") == (
            f"https://niuu.example.test{_EXCHANGE_PATH}"
        )

    async def test_explicit_platform_exchange_url_is_used(self, mock_api) -> None:
        profile = _resident_profile()
        profile.deployment["values"]["resident"]["platform"]["workloadExchangeUrl"] = (
            "https://exchange.example/x"
        )
        values = await self._deploy(_cluster_pod_manager(), profile, mock_api)

        assert _env(values, "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL") == "https://exchange.example/x"

    async def test_a_profile_volundr_api_url_is_kept(self, mock_api) -> None:
        profile = _resident_profile()
        profile.deployment["values"]["volundr"] = {"apiUrl": "https://profile.example"}
        values = await self._deploy(_cluster_pod_manager(), profile, mock_api)

        assert values["volundr"]["apiUrl"] == "https://profile.example"

    async def test_no_cluster_url_configured_adds_none(self, pod_manager, mock_api) -> None:
        values = await self._deploy(pod_manager, _resident_profile(), mock_api)

        assert "volundr" not in values
        assert _env(values, "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL") == (
            f"https://niuu.example.test{_EXCHANGE_PATH}"
        )

    async def test_resident_without_platform_exchanges_at_its_volundr(self, mock_api) -> None:
        profile = _resident_profile()
        del profile.deployment["values"]["resident"]["platform"]
        values = await self._deploy(_cluster_pod_manager(), profile, mock_api)

        assert _env(values, "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL") == (
            f"{_CLUSTER_API_URL}{_EXCHANGE_PATH}"
        )


class TestFluxResidentHasNoForgeSession:
    """A resident is a resident runtime, not a Forge session: its skuld must not
    call the Forge session event log or activity endpoints."""

    async def test_resident_disables_forge_session_log_and_activity(
        self, pod_manager: FluxPodManager, mock_api
    ) -> None:
        profile = _resident_profile()
        profile.deployment["values"]["broker"] = {
            "cliType": "codex-ws",
            "eventLogEnabled": True,
        }
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), profile)

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["broker"]["eventLogEnabled"] is False
        assert values["broker"]["activityReportingEnabled"] is False
        # The profile's own broker settings and the brokered credentials stay.
        assert values["broker"]["cliType"] == "codex-ws"
        assert "codexAuth" in values["broker"]

    async def test_forge_sessions_keep_their_event_log_and_activity(
        self, pod_manager: FluxPodManager, sample_session: Session, mock_api
    ) -> None:
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.start(sample_session, make_spec())

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert "eventLogEnabled" not in values.get("broker", {})
        assert "activityReportingEnabled" not in values.get("broker", {})


class TestFluxPodManagerRejectsExecutionFlagsWithoutBaseUrl:
    """resident_triggers_enabled/resident_budget_enabled without
    resident_platform_base_url used to be silently ignored — _resident_values
    never renders a "platform" block at all without a base_url, so the flags
    did nothing, and an operator who set them (believing triggers/budget now
    execute) got no signal that nothing changed. Configured-but-impossible
    must raise, not silently no-op (.claude/rules/no-fallbacks.md)."""

    def test_triggers_enabled_without_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="resident_platform_base_url"):
            FluxPodManager(namespace="test-ns", resident_triggers_enabled=True)

    def test_budget_enabled_without_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="resident_platform_base_url"):
            FluxPodManager(namespace="test-ns", resident_budget_enabled=True)

    def test_both_flags_without_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="resident_platform_base_url"):
            FluxPodManager(
                namespace="test-ns",
                resident_triggers_enabled=True,
                resident_budget_enabled=True,
            )

    def test_flags_with_base_url_do_not_raise(self) -> None:
        FluxPodManager(
            namespace="test-ns",
            resident_platform_base_url="http://niuu-volundr.volundr.svc.cluster.local",
            resident_triggers_enabled=True,
            resident_budget_enabled=True,
        )  # must not raise

    def test_neither_flag_without_base_url_does_not_raise(self) -> None:
        FluxPodManager(namespace="test-ns")  # must not raise — the all-off default


class TestFluxResidentPlatformTriggersAndBudget:
    """resident_platform_base_url / resident_triggers_enabled /
    resident_budget_enabled — every managed resident gets platform
    reachability, and triggers/budget are additive opt-ins on top of it."""

    async def test_no_base_url_configured_leaves_platform_untouched(self, mock_api) -> None:
        pod_manager = FluxPodManager(namespace="test-ns")
        runtime = _resident_runtime()
        profile = _resident_profile()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, profile)

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        # The profile's own configured platform block survives unchanged —
        # this controller adds nothing when it has no base URL of its own.
        assert values["resident"]["platform"]["baseUrl"] == "https://niuu.example.test"
        assert "triggers" not in values["resident"]
        assert "budget" not in values["resident"]
        # No dedicated identity either — no platform to authenticate to.
        assert "serviceAccountName" not in values
        # And nothing tells the skuld chart to create one, either — it stays
        # at its own default (false), so a shared/Fleet-managed SA is left
        # alone.
        assert "serviceAccount" not in values["resident"]

    async def test_base_url_configured_sets_platform_for_a_profile_without_one(
        self, mock_api
    ) -> None:
        pod_manager = FluxPodManager(
            namespace="test-ns",
            resident_platform_base_url="http://niuu-volundr.volundr.svc.cluster.local:80",
        )
        runtime = _resident_runtime()
        profile = _resident_profile()
        # Strip the profile's own platform config to prove the controller
        # supplies it when the profile does not.
        del profile.deployment["values"]["resident"]["platform"]
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, profile)

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["resident"]["platform"] == {
            "enabled": True,
            "baseUrl": "http://niuu-volundr.volundr.svc.cluster.local:80",
        }
        assert "triggers" not in values["resident"]
        assert "budget" not in values["resident"]
        # A dedicated ServiceAccount, named exactly like the HelmRelease
        # itself, so the workload-identity mapping's owner_id_claim_pattern
        # (system:serviceaccount:<ns>:resident-<uuid>) resolves to this
        # runtime's own id.
        assert values["serviceAccountName"] == f"resident-{runtime.id}"
        # This release owns creating that ServiceAccount, because the name
        # is unique to this runtime and no other release can already own it
        # — see charts/skuld's resident.serviceAccount.create (default
        # false, so a shared/Fleet-managed name is never claimed here).
        assert values["resident"]["serviceAccount"] == {"create": True}

    async def test_triggers_and_budget_flags_add_their_own_enabled_blocks(self, mock_api) -> None:
        pod_manager = FluxPodManager(
            namespace="test-ns",
            resident_platform_base_url="http://niuu-volundr.volundr.svc.cluster.local:80",
            resident_triggers_enabled=True,
            resident_budget_enabled=True,
        )
        runtime = _resident_runtime()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _resident_profile())

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["resident"]["triggers"] == {"enabled": True}
        assert values["resident"]["budget"] == {"enabled": True}

    async def test_triggers_and_budget_default_off_even_with_a_base_url(self, mock_api) -> None:
        pod_manager = FluxPodManager(
            namespace="test-ns",
            resident_platform_base_url="http://niuu-volundr.volundr.svc.cluster.local:80",
        )
        runtime = _resident_runtime()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _resident_profile())

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert "triggers" not in values["resident"]
        assert "budget" not in values["resident"]


def _openclaw_profile() -> ResidentDeploymentProfile:
    profile = _resident_profile()
    profile.deployment["values"]["resident"]["openclaw"] = {"enabled": True}
    return profile


def _bind_ravn_sessions(pod_manager: FluxPodManager, store=None):
    """Wire the Ravn session controller the way composition does; returns its store."""
    from niuu.adapters.memory_credential_store import MemoryCredentialStore
    from volundr.adapters.outbound.ravn_gateway import RavnResidentSessionController

    store = store or MemoryCredentialStore()
    RavnResidentSessionController(pod_manager, store)
    return store


async def _kept_token(store, runtime) -> str | None:
    kept = await store.get_value("resident", str(runtime.id), "ravn-openclaw")
    return kept["token"] if kept else None


def _volundr_shim_secret(runtime, token: str | None = None) -> SimpleNamespace:
    """resident-<id>-openclaw as Volundr wrote it (before tokens were kept per resident)."""
    import base64

    return SimpleNamespace(
        metadata=SimpleNamespace(
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "niuu.world/resident-id": str(runtime.id),
            }
        ),
        data={"token": base64.b64encode(token.encode()).decode()} if token else None,
    )


class TestFluxResidentOpenClawToken:
    """Each Ravn resident's shim gets its own token Secret; no cluster-wide master."""

    async def test_deploy_generates_the_residents_own_token_before_the_release(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        import json

        store = _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        core_api.read_namespaced_secret.side_effect = _ApiError(404)
        order: list[str] = []
        core_api.create_namespaced_secret.side_effect = lambda **kwargs: order.append(
            kwargs["body"]["metadata"]["name"]
        )
        mock_api.create_namespaced_custom_object.side_effect = lambda **kwargs: order.append(
            kwargs["body"]["metadata"]["name"]
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _openclaw_profile())

        name = f"resident-{runtime.id}-openclaw"
        assert order == [name, f"resident-{runtime.id}"]
        secret = core_api.create_namespaced_secret.call_args.kwargs["body"]
        kept = await _kept_token(store, runtime)
        assert kept and len(kept) >= 32
        assert secret["stringData"] == {"token": kept}
        assert secret["metadata"]["namespace"] == "test-ns"
        assert secret["metadata"]["labels"] == {
            "app.kubernetes.io/managed-by": "volundr",
            "niuu.world/resident-id": str(runtime.id),
            "niuu.world/owner-id": "user-a",
            "niuu.world/tenant-id": "tenant-a",
        }
        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["resident"]["openclaw"] == {
            "enabled": True,
            "tokenSecret": {"name": name, "key": "token"},
        }
        assert kept not in json.dumps(values)

    async def test_a_running_resident_keeps_the_token_its_secret_has(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        """Migration: the HMAC-derived token Volundr wrote before is adopted, unchanged."""
        store = _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        legacy = "a" * 64
        core_api.read_namespaced_secret.return_value = _volundr_shim_secret(runtime, legacy)
        core_api.create_namespaced_secret.side_effect = _ApiError(409)
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _openclaw_profile())

        assert await _kept_token(store, runtime) == legacy
        core_api.patch_namespaced_secret.assert_awaited_once_with(
            name=f"resident-{runtime.id}-openclaw",
            namespace="test-ns",
            body={"stringData": {"token": legacy}},
        )
        # The session controller dials the shim with that same token, before any redeploy.
        assert await pod_manager.resident_openclaw_token(runtime) == legacy

    async def test_dialing_adopts_the_secrets_token_before_any_redeploy(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        store = _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        core_api.read_namespaced_secret.return_value = _volundr_shim_secret(runtime, "b" * 64)

        assert await pod_manager.resident_openclaw_token(runtime) == "b" * 64
        assert await _kept_token(store, runtime) == "b" * 64

    async def test_a_foreign_secrets_token_is_never_adopted(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        store = _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        foreign = _volundr_shim_secret(_resident_runtime(), "c" * 64)
        core_api.read_namespaced_secret.return_value = foreign

        token = await pod_manager.resident_openclaw_token(runtime)
        assert token != "c" * 64 and token == await _kept_token(store, runtime)

    async def test_the_kept_token_survives_volundr_restarts_and_redeploys(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        store = _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        core_api.read_namespaced_secret.side_effect = _ApiError(404)
        first = await pod_manager.resident_openclaw_token(runtime)
        core_api.read_namespaced_secret.reset_mock()

        # A restarted Volundr: a new controller wiring over the same credential store.
        _bind_ravn_sessions(pod_manager, store)
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _openclaw_profile())
            await pod_manager.deploy(runtime, _openclaw_profile())

        assert await pod_manager.resident_openclaw_token(runtime) == first
        for call in core_api.create_namespaced_secret.call_args_list:
            assert call.kwargs["body"]["stringData"] == {"token": first}
        # Kept: the Secret is never read to adopt a token again.
        core_api.read_namespaced_secret.assert_not_awaited()

    async def test_shim_is_on_by_default_wherever_ravn_sessions_are_wired(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        """A profile need not mention resident.openclaw: channels follow from the wiring."""
        _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        core_api.read_namespaced_secret.side_effect = _ApiError(404)
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _resident_profile())

        core_api.create_namespaced_secret.assert_awaited_once()
        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["resident"]["openclaw"] == {
            "enabled": True,
            "tokenSecret": {"name": f"resident-{runtime.id}-openclaw", "key": "token"},
        }

    async def test_a_profile_can_switch_the_shim_off(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        _bind_ravn_sessions(pod_manager)
        profile = _resident_profile()
        profile.deployment["values"]["resident"]["openclaw"] = {"enabled": False}
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), profile)

        core_api.create_namespaced_secret.assert_not_awaited()
        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["resident"]["openclaw"] == {"enabled": False}

    async def test_shim_is_explicitly_off_without_ravn_sessions(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        """The skuld chart turns the shim on by default; Volundr decides per resident."""
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), _resident_profile())

        core_api.create_namespaced_secret.assert_not_awaited()
        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["resident"]["openclaw"] == {"enabled": False}

    def test_routes_to_the_shim_port_on_the_residents_service(
        self, pod_manager: FluxPodManager
    ) -> None:
        runtime = _resident_runtime(
            backend_ref={"namespace": "skuld", "name": "resident-abc"},
        )
        target = pod_manager.resident_openclaw_target(runtime, 18790)
        host = "resident-abc-skuld.skuld.svc.cluster.local"
        assert target is not None
        assert target.service_url == f"http://{host}:18790/"
        assert (target.connect_host, target.connect_port) == (host, 18790)
        assert pod_manager.resident_openclaw_target(_resident_runtime(), 18790) is None

    async def test_openclaw_without_a_ravn_session_controller_fails_the_deploy(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            pytest.raises(ValueError, match="RavnResidentSessionController"),
        ):
            await pod_manager.deploy(_resident_runtime(), _openclaw_profile())

        core_api.create_namespaced_secret.assert_not_awaited()
        mock_api.create_namespaced_custom_object.assert_not_awaited()

    async def test_redeploy_refreshes_the_residents_own_secret(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        core_api.create_namespaced_secret.side_effect = _ApiError(409)
        core_api.read_namespaced_secret.return_value = SimpleNamespace(
            metadata=SimpleNamespace(
                labels={
                    "app.kubernetes.io/managed-by": "volundr",
                    "niuu.world/resident-id": str(runtime.id),
                }
            )
        )
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, _openclaw_profile())

        created = core_api.create_namespaced_secret.call_args.kwargs["body"]["stringData"]
        core_api.patch_namespaced_secret.assert_awaited_once_with(
            name=f"resident-{runtime.id}-openclaw",
            namespace="test-ns",
            body={"stringData": created},
        )
        mock_api.create_namespaced_custom_object.assert_awaited_once()

    async def test_a_foreign_secret_with_the_residents_name_fails_the_deploy(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        _bind_ravn_sessions(pod_manager)
        core_api.create_namespaced_secret.side_effect = _ApiError(409)
        core_api.read_namespaced_secret.return_value = SimpleNamespace(
            metadata=SimpleNamespace(labels={"niuu.world/resident-id": str(uuid4())})
        )
        with (
            patch.object(pod_manager, "_get_api", return_value=mock_api),
            pytest.raises(ValueError, match="is not resident"),
        ):
            await pod_manager.deploy(_resident_runtime(), _openclaw_profile())

        core_api.patch_namespaced_secret.assert_not_awaited()
        mock_api.create_namespaced_custom_object.assert_not_awaited()

    async def test_delete_removes_the_secret_and_tolerates_its_absence(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        runtime = _resident_runtime()
        store = _bind_ravn_sessions(pod_manager)
        core_api.read_namespaced_secret.side_effect = _ApiError(404)
        await pod_manager.resident_openclaw_token(runtime)
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.delete(runtime)
            core_api.delete_namespaced_secret.assert_awaited_once_with(
                name=f"resident-{runtime.id}-openclaw", namespace="test-ns"
            )
            assert await _kept_token(store, runtime) is None
            core_api.delete_namespaced_secret.side_effect = _ApiError(404)
            await pod_manager.delete(runtime)

            core_api.delete_namespaced_secret.side_effect = _ApiError(403)
            with pytest.raises(_ApiError):
                await pod_manager.delete(runtime)

    async def test_delete_leaves_secrets_alone_without_ravn_sessions(
        self,
        pod_manager: FluxPodManager,
        mock_api,
        core_api: AsyncMock,
    ) -> None:
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.delete(_resident_runtime())

        core_api.delete_namespaced_secret.assert_not_awaited()


def _container_status(name: str, *, running: bool = True, restarts: int = 0, last_run=False):
    state = SimpleNamespace(
        running=SimpleNamespace() if running else None,
        terminated=None,
        waiting=None if running else SimpleNamespace(reason="ContainerCreating"),
    )
    last_state = SimpleNamespace(terminated=SimpleNamespace() if last_run else None)
    return SimpleNamespace(name=name, state=state, last_state=last_state, restart_count=restarts)


def _pod(name: str, containers: dict[str, dict], init_containers: dict[str, dict] | None = None):
    init_containers = init_containers or {}
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(
            containers=[SimpleNamespace(name=container) for container in containers],
            init_containers=[SimpleNamespace(name=container) for container in init_containers],
        ),
        status=SimpleNamespace(
            container_statuses=[
                _container_status(container, **state) for container, state in containers.items()
            ],
            init_container_statuses=[
                _container_status(container, **state)
                for container, state in init_containers.items()
            ],
        ),
    )


def _with_logs(core_api: AsyncMock, pods: list, logs: dict[tuple[str, str], str]) -> None:
    core_api.list_namespaced_pod.return_value = SimpleNamespace(items=pods)

    async def read_log(*, name, namespace, container, tail_lines, timestamps):
        return logs[(name, container)]

    core_api.read_namespaced_pod_log.side_effect = read_log


class TestFluxResidentLogs:
    """The resident Logs action on Flux: each container's pod log, by container name."""

    async def test_reads_each_container_of_the_resident_pod_as_a_source(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        runtime = _resident_runtime()
        pod = f"resident-{runtime.id}-skuld-7c9d-abcde"
        _with_logs(
            core_api,
            [_pod(pod, {"skuld": {}, "ravn": {}})],
            {
                (pod, "skuld"): (
                    "2026-10-15T10:00:02.000000000Z 2026-10-15 10:00:02,000 - skuld.broker - "
                    "WARNING - slow turn\n"
                    '2026-10-15T10:00:00.123456789Z {"time":"t","level":"INFO",'
                    '"logger":"skuld","message":"broker ready"}\n'
                ),
                (pod, "ravn"): "2026-10-15T10:00:01Z 2026-10-15 ravn.agent ERROR tool failed\n",
            },
        )

        page = await pod_manager.logs(runtime, lines=50, sources=(), min_level="")

        assert [(e.source, e.level, e.message) for e in page.entries] == [
            ("skuld", "info", "broker ready"),
            ("ravn", "error", "2026-10-15 ravn.agent ERROR tool failed"),
            ("skuld", "warning", "2026-10-15 10:00:02,000 - skuld.broker - WARNING - slow turn"),
        ]
        # The kubelet's nanoseconds, kept to the millisecond.
        assert page.entries[0].timestamp_ms == 1_792_058_400_123
        assert page.entries[0].fields == {"pod": pod}
        assert page.buffer_total == 3
        core_api.list_namespaced_pod.assert_awaited_once_with(
            namespace="test-ns",
            label_selector=f"app.kubernetes.io/instance=resident-{runtime.id}",
        )
        core_api.read_namespaced_pod_log.assert_any_await(
            name=pod,
            namespace="test-ns",
            container="ravn",
            tail_lines=50,
            timestamps=True,
        )

    async def test_honours_sources_min_level_and_lines(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        runtime = _resident_runtime()
        _with_logs(
            core_api,
            [_pod("p", {"skuld": {}, "ravn": {}})],
            {
                ("p", "ravn"): (
                    "2026-10-15T10:00:00Z ravn DEBUG noise\n"
                    "2026-10-15T10:00:01Z ravn WARN first\n"
                    "2026-10-15T10:00:02Z ravn CRITICAL second\n"
                    "2026-10-15T10:00:03Z ravn ERROR third\n"
                ),
            },
        )

        page = await pod_manager.logs(runtime, lines=2, sources=("ravn",), min_level="WARNING")

        assert [(e.level, e.message) for e in page.entries] == [
            ("critical", "ravn CRITICAL second"),
            ("error", "ravn ERROR third"),
        ]
        assert page.buffer_total == 3
        assert [
            call.kwargs["container"] for call in core_api.read_namespaced_pod_log.await_args_list
        ] == ["ravn"]

    async def test_skips_containers_that_have_not_started_and_reads_every_pod(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        runtime = _resident_runtime()
        _with_logs(
            core_api,
            [
                _pod("old", {"skuld": {"running": False, "restarts": 3, "last_run": True}}),
                _pod(
                    "new",
                    {"skuld": {"running": False}},
                    init_containers={"setup": {"running": False, "last_run": True}},
                ),
            ],
            {
                ("old", "skuld"): "2026-10-15T10:00:00Z Traceback: crashed\n",
                ("new", "setup"): "2026-10-15T10:00:01Z copying workspace\n",
            },
        )

        page = await pod_manager.logs(runtime, lines=10, sources=(), min_level="")

        assert [(e.fields["pod"], e.source, e.level) for e in page.entries] == [
            ("old", "skuld", "info"),
            ("new", "setup", "info"),
        ]

    async def test_a_resident_without_a_pod_has_no_logs_to_read(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[])

        with pytest.raises(ResidentLogsUnavailableError, match="suspended or not scheduled"):
            await pod_manager.logs(_resident_runtime(), lines=10, sources=(), min_level="")

    async def test_a_pod_whose_containers_never_started_has_no_logs_to_read(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        _with_logs(core_api, [_pod("p", {"skuld": {"running": False}})], {})

        with pytest.raises(ResidentLogsUnavailableError, match="still starting"):
            await pod_manager.logs(_resident_runtime(), lines=10, sources=(), min_level="")
        core_api.read_namespaced_pod_log.assert_not_awaited()

    @pytest.mark.parametrize("failing_call", ["list_namespaced_pod", "read_namespaced_pod_log"])
    async def test_forbidden_names_the_missing_rbac(
        self, pod_manager: FluxPodManager, core_api: AsyncMock, failing_call: str
    ) -> None:
        _with_logs(core_api, [_pod("p", {"skuld": {}})], {("p", "skuld"): ""})
        getattr(core_api, failing_call).side_effect = _ApiError(403)

        with pytest.raises(PermissionError, match="pods/log there") as raised:
            await pod_manager.logs(_resident_runtime(), lines=10, sources=(), min_level="")
        assert "namespace test-ns" in str(raised.value)
        assert "resident-logs Role" in str(raised.value)

    async def test_other_api_errors_propagate(
        self, pod_manager: FluxPodManager, core_api: AsyncMock
    ) -> None:
        core_api.list_namespaced_pod.side_effect = _ApiError(500)

        with pytest.raises(_ApiError):
            await pod_manager.logs(_resident_runtime(), lines=10, sources=(), min_level="")


class TestParsePodLogs:
    def test_json_lines_without_a_known_level_infer_one(self) -> None:
        entries = parse_pod_logs(
            '2026-10-15T10:00:00Z {"event": "tool exception raised"}\n'
            "2026-10-15T10:00:01Z plain debug output\n"
            "not-a-timestamp skipped\n"
            "2026-10-15T10:00:02Z \n",
            pod="p",
            container="ravn",
            min_level="",
        )

        assert [(e.level, e.message) for e in entries] == [
            ("error", "tool exception raised"),
            ("debug", "plain debug output"),
        ]

    def test_fatal_and_warn_map_to_the_shared_levels(self) -> None:
        entries = parse_pod_logs(
            '2026-10-15T10:00:00Z {"level": "warn", "msg": "low disk"}\n'
            "2026-10-15T10:00:01Z FATAL out of memory\n",
            pod="p",
            container="ravn",
            min_level="warn",
        )

        assert [(e.level, e.message) for e in entries] == [
            ("warning", "low disk"),
            ("critical", "FATAL out of memory"),
        ]


_OLD_IMAGE = {"repository": "ghcr.io/niuulabs/skuld", "tag": "dev-6d5a0ce"}
_NEW_IMAGE = {"repository": "ghcr.io/niuulabs/skuld", "tag": "dev-b765a3f"}
_OLD_VERSION = "skuld 2.0.0-dev.258, ghcr.io/niuulabs/skuld:dev-6d5a0ce"
_NEW_VERSION = "skuld 2.0.0-dev.264, ghcr.io/niuulabs/skuld:dev-b765a3f"


def _platform_manager() -> FluxPodManager:
    """A Völundr deployed with a newer Skuld chart than its residents run."""
    return FluxPodManager(namespace="test-ns", chart_name="skuld", chart_version="2.0.0-dev.264")


def _platform_profile() -> ResidentDeploymentProfile:
    """The resident's profile as this Völundr renders it: its own PVCs, the new image."""
    profile = _own_volume_profile()
    profile.deployment["values"]["image"] = dict(_NEW_IMAGE)
    return profile


def _running_release(runtime: ResidentRuntime, **annotations: str) -> dict:
    """resident-<id> as an older Völundr created it."""
    return {
        "apiVersion": f"{HELMRELEASE_GROUP}/{HELMRELEASE_VERSION}",
        "kind": "HelmRelease",
        "metadata": {
            "name": f"resident-{runtime.id}",
            "namespace": "test-ns",
            "resourceVersion": "4711",
            "finalizers": ["finalizers.fluxcd.io"],
            "labels": {"app.kubernetes.io/managed-by": "volundr"},
            "annotations": {"niuu.world/profile-id": "ravn-helm", **annotations},
        },
        "spec": {
            "interval": "5m",
            "chart": {"spec": {"chart": "skuld", "version": "2.0.0-dev.258"}},
            "values": {
                "replicaCount": 1,
                "image": dict(_OLD_IMAGE),
                "homeVolume": {"enabled": True, "existingClaim": f"resident-{runtime.id}-home"},
                "persistence": {
                    "enabled": True,
                    "existingClaim": f"resident-{runtime.id}-workspace",
                },
                "resident": {"openclaw": {"enabled": False}},
                "podAnnotations": {"niuu.world/restarted-at": "2026-10-01T00:00:00+00:00"},
            },
        },
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def _own_claims_exist(core_api: AsyncMock, runtime: ResidentRuntime) -> None:
    core_api.create_namespaced_persistent_volume_claim.side_effect = _ApiError(409)
    core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "niuu.world/resident-id": str(runtime.id),
            }
        )
    )


class _Cluster:
    """The resident's HelmRelease as the Kubernetes API keeps it."""

    def __init__(self, release: dict | None) -> None:
        self.release = release
        self.api = AsyncMock()
        self.api.get_namespaced_custom_object.side_effect = self._get
        self.api.replace_namespaced_custom_object.side_effect = self._replace

    async def _get(self, **_kwargs):
        if self.release is None:
            raise _ApiError(404)
        return self.release

    async def _replace(self, **kwargs):
        self.release = {**kwargs["body"], "status": {}}
        return self.release

    @property
    def replaces(self) -> list[dict]:
        calls = self.api.replace_namespaced_custom_object.await_args_list
        return [call.kwargs["body"] for call in calls]


def _on_cluster(pod_manager, cluster):
    stack = ExitStack()
    stack.enter_context(patch.object(pod_manager, "_get_api", return_value=cluster.api))
    stack.enter_context(
        patch.object(pod_manager, "_get_resident_deployment", AsyncMock(return_value=None))
    )
    return stack


async def _reconcile(pod_manager, cluster, runtime, profile):
    with _on_cluster(pod_manager, cluster):
        return await pod_manager.reconcile(runtime, profile)


def test_platform_version_names_the_chart_and_every_image() -> None:
    from volundr.adapters.outbound.flux import resident_platform_version

    values = {
        "nginx": {"image": {"repository": "nginx", "tag": "alpine"}},
        "image": {"repository": "ghcr.io/niuulabs/skuld", "tag": "dev-1", "digest": "sha256:ab"},
        "extraContainers": [{"name": "ravn", "image": "ghcr.io/niuulabs/ravn:dev-1"}],
        "global": {"image": {"repository": ""}},
    }

    assert resident_platform_version("skuld", "2.0.0-dev.264", values) == (
        "skuld 2.0.0-dev.264, ghcr.io/niuulabs/skuld:dev-1@sha256:ab, "
        "extraContainers[0] ghcr.io/niuulabs/ravn:dev-1, nginx nginx:alpine"
    )


class TestFluxResidentFollowsPlatform:
    """A running resident moves to the chart and image Völundr renders now, once."""

    async def test_reconcile_upgrades_chart_and_image_and_keeps_disks_and_token_secret(
        self,
        core_api: AsyncMock,
        caplog,
    ) -> None:
        pod_manager = _platform_manager()
        store = _bind_ravn_sessions(pod_manager)
        runtime = _resident_runtime()
        await store.store(
            "resident", str(runtime.id), "ravn-openclaw", "generic", {"token": "kept-token"}
        )
        _own_claims_exist(core_api, runtime)
        core_api.create_namespaced_secret.side_effect = _ApiError(409)
        core_api.read_namespaced_secret.return_value = _volundr_shim_secret(runtime, "kept-token")
        cluster = _Cluster(_running_release(runtime))
        profile = _platform_profile()
        profile.deployment["values"]["resident"]["openclaw"] = {"enabled": True}

        with caplog.at_level("INFO", logger="volundr.adapters.outbound.flux"):
            observation = await _reconcile(pod_manager, cluster, runtime, profile)

        [rolled] = cluster.replaces
        assert rolled["spec"]["chart"]["spec"]["version"] == "2.0.0-dev.264"
        assert rolled["spec"]["values"]["image"] == _NEW_IMAGE
        # The same release, guarded by its resourceVersion; Flux's finalizer stays.
        assert rolled["metadata"]["resourceVersion"] == "4711"
        assert rolled["metadata"]["finalizers"] == ["finalizers.fluxcd.io"]
        assert "status" not in rolled
        annotations = rolled["metadata"]["annotations"]
        assert annotations["niuu.world/platform-version"] == _NEW_VERSION
        assert annotations["niuu.world/replaced-platform-version"] == _OLD_VERSION
        # Its own disks, and its own shim token Secret with the token it had.
        values = rolled["spec"]["values"]
        assert values["homeVolume"]["existingClaim"] == f"resident-{runtime.id}-home"
        assert values["persistence"]["existingClaim"] == f"resident-{runtime.id}-workspace"
        assert values["resident"]["openclaw"]["tokenSecret"]["name"] == (
            f"resident-{runtime.id}-openclaw"
        )
        core_api.delete_namespaced_persistent_volume_claim.assert_not_awaited()
        core_api.delete_namespaced_secret.assert_not_awaited()
        assert core_api.patch_namespaced_secret.await_args.kwargs["body"] == {
            "stringData": {"token": "kept-token"}
        }
        cluster.api.delete_namespaced_custom_object.assert_not_awaited()
        assert observation.backend_ref["platformVersion"] == _NEW_VERSION
        assert f"Upgrading resident Muninn from {_OLD_VERSION} to {_NEW_VERSION}" in caplog.messages

    async def test_a_resident_older_than_its_own_pvcs_keeps_the_claim_it_runs_on(
        self,
        core_api: AsyncMock,
    ) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime()
        release = _running_release(runtime)
        del release["spec"]["values"]["homeVolume"]
        release["spec"]["values"]["persistence"] = {
            "enabled": True,
            "existingClaim": "volundr-sessions",
        }
        cluster = _Cluster(release)

        await _reconcile(pod_manager, cluster, runtime, _platform_profile())

        [rolled] = cluster.replaces
        assert rolled["spec"]["values"]["persistence"]["existingClaim"] == "volundr-sessions"
        assert "homeVolume" not in rolled["spec"]["values"]
        core_api.create_namespaced_persistent_volume_claim.assert_not_awaited()

    async def test_reconcile_rolls_once_per_version(self, core_api: AsyncMock) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime()
        _own_claims_exist(core_api, runtime)
        cluster = _Cluster(_running_release(runtime))

        await _reconcile(pod_manager, cluster, runtime, _platform_profile())
        await _reconcile(pod_manager, cluster, runtime, _platform_profile())
        # A Völundr that restarted (or another replica) finds it on the version.
        await _reconcile(_platform_manager(), cluster, runtime, _platform_profile())

        assert len(cluster.replaces) == 1

    async def test_an_edited_release_is_not_rolled_again_for_the_same_version(
        self,
        core_api: AsyncMock,
    ) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime()
        release = _running_release(runtime, **{"niuu.world/platform-version": _NEW_VERSION})
        cluster = _Cluster(release)

        await _reconcile(pod_manager, cluster, runtime, _platform_profile())

        assert cluster.replaces == []

    async def test_an_older_volundr_does_not_move_the_resident_back(
        self,
        core_api: AsyncMock,
    ) -> None:
        """While Völundr itself rolls out, its old replica still renders the old version."""
        runtime = _resident_runtime()
        _own_claims_exist(core_api, runtime)
        cluster = _Cluster(_running_release(runtime))
        await _reconcile(_platform_manager(), cluster, runtime, _platform_profile())

        old_volundr = FluxPodManager(
            namespace="test-ns", chart_name="skuld", chart_version="2.0.0-dev.258"
        )
        old_profile = _own_volume_profile()
        old_profile.deployment["values"]["image"] = dict(_OLD_IMAGE)
        await _reconcile(old_volundr, cluster, runtime, old_profile)

        assert len(cluster.replaces) == 1
        assert cluster.release["spec"]["chart"]["spec"]["version"] == "2.0.0-dev.264"

    async def test_restart_rerenders_on_the_current_version(self, core_api: AsyncMock) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime()
        _own_claims_exist(core_api, runtime)
        cluster = _Cluster(_running_release(runtime))

        with _on_cluster(pod_manager, cluster):
            await pod_manager.restart(runtime, _platform_profile())

        [restarted] = cluster.replaces
        assert restarted["spec"]["chart"]["spec"]["version"] == "2.0.0-dev.264"
        assert restarted["spec"]["values"]["image"] == _NEW_IMAGE
        restarted_at = restarted["spec"]["values"]["podAnnotations"]["niuu.world/restarted-at"]
        assert restarted_at != "2026-10-01T00:00:00+00:00"
        assert restarted["metadata"]["annotations"]["niuu.world/platform-version"] == (_NEW_VERSION)

    async def test_a_suspended_resident_is_left_alone_until_resumed(
        self,
        core_api: AsyncMock,
    ) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime(desired_state=ResidentDesiredState.SUSPENDED)
        _own_claims_exist(core_api, runtime)
        release = _running_release(runtime)
        release["spec"]["values"]["replicaCount"] = 0
        cluster = _Cluster(release)

        await _reconcile(pod_manager, cluster, runtime, _platform_profile())

        assert cluster.replaces == []
        cluster.api.patch_namespaced_custom_object.assert_not_awaited()

        resumed = runtime.model_copy(update={"desired_state": ResidentDesiredState.RUNNING})
        with _on_cluster(pod_manager, cluster):
            await pod_manager.resume(resumed, _platform_profile())

        # One start, on the new version: no scale-up of the old pod spec first.
        [rolled] = cluster.replaces
        assert rolled["spec"]["values"]["replicaCount"] == 1
        assert rolled["spec"]["chart"]["spec"]["version"] == "2.0.0-dev.264"
        assert rolled["spec"]["values"]["image"] == _NEW_IMAGE
        cluster.api.patch_namespaced_custom_object.assert_not_awaited()

    async def test_a_failed_roll_marks_the_resident_failed_without_retrying(
        self,
        core_api: AsyncMock,
    ) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime()
        _own_claims_exist(core_api, runtime)
        cluster = _Cluster(_running_release(runtime))
        cluster.api.replace_namespaced_custom_object.side_effect = _ApiError(422)

        first = await _reconcile(pod_manager, cluster, runtime, _platform_profile())
        second = await _reconcile(pod_manager, cluster, runtime, _platform_profile())

        assert cluster.api.replace_namespaced_custom_object.await_count == 1
        for observation in (first, second):
            assert observation.observed_state is ResidentObservedState.FAILED
            condition = observation.conditions[0]
            assert condition.type == "PlatformUpgrade"
            assert condition.reason == "UpgradeFailed"
            assert _OLD_VERSION in condition.message and _NEW_VERSION in condition.message
            assert "Restart the resident to retry" in condition.message
            # It still runs, and says what it runs.
            assert observation.backend_ref["platformVersion"] == _OLD_VERSION

        # An explicit restart tries again.
        cluster.api.replace_namespaced_custom_object.side_effect = cluster._replace
        with _on_cluster(pod_manager, cluster):
            await pod_manager.restart(runtime, _platform_profile())
        third = await _reconcile(pod_manager, cluster, runtime, _platform_profile())
        assert third.observed_state is not ResidentObservedState.FAILED
        assert third.backend_ref["platformVersion"] == _NEW_VERSION

    async def test_a_concurrent_upgrade_is_not_a_failure(self, core_api: AsyncMock) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime()
        _own_claims_exist(core_api, runtime)
        cluster = _Cluster(_running_release(runtime))
        cluster.api.replace_namespaced_custom_object.side_effect = _ApiError(409)

        observation = await _reconcile(pod_manager, cluster, runtime, _platform_profile())

        assert observation.observed_state is not ResidentObservedState.FAILED
        assert all(condition.type != "PlatformUpgrade" for condition in observation.conditions)

    async def test_deploy_records_the_version_it_renders(self, mock_api, core_api) -> None:
        pod_manager = _platform_manager()
        runtime = _resident_runtime()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            observation = await pod_manager.deploy(runtime, _platform_profile())

        body = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]
        assert body["metadata"]["annotations"]["niuu.world/platform-version"] == _NEW_VERSION
        assert observation.backend_ref["platformVersion"] == _NEW_VERSION


def _pod_event(name: str, reason: str, message: str, minute: int) -> SimpleNamespace:
    return SimpleNamespace(
        involved_object=SimpleNamespace(name=name),
        reason=reason,
        message=message,
        last_timestamp=datetime(2026, 10, 24, 16, minute, tzinfo=UTC),
        event_time=None,
        first_timestamp=None,
    )


class TestStatusDetail:
    """Why a session's pods are not running, read from the cluster."""

    async def test_reports_the_latest_pod_warning_and_the_release_message(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        release = f"skuld-{sample_session.id}"
        core_api.list_namespaced_event.return_value = SimpleNamespace(
            items=[
                _pod_event(
                    f"{release}-abc", "FailedScheduling", "0/10 nodes: 1 Insufficient memory", 40
                ),
                _pod_event(
                    f"{release}-abc", "FailedScheduling", "0/10 nodes: 2 Insufficient memory", 41
                ),
                _pod_event("skuld-someone-else-abc", "BackOff", "not mine", 59),
            ]
        )
        helmrelease = {
            "status": {
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "False",
                        "reason": "InstallFailed",
                        "message": "context deadline exceeded",
                    }
                ]
            }
        }
        with patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=helmrelease)):
            detail = await pod_manager.status_detail(sample_session)

        assert detail == (
            f"pod {release}-abc: FailedScheduling: 0/10 nodes: 2 Insufficient memory; "
            "HelmRelease InstallFailed: context deadline exceeded"
        )
        core_api.list_namespaced_event.assert_awaited_once_with(
            "test-ns", field_selector="type=Warning,involvedObject.kind=Pod"
        )

    async def test_events_survive_the_uninstalled_release(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        release = f"skuld-{sample_session.id}"
        core_api.list_namespaced_event.return_value = SimpleNamespace(
            items=[_pod_event(f"{release}-abc", "FailedScheduling", "Insufficient memory", 40)]
        )
        with patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=None)):
            detail = await pod_manager.status_detail(sample_session)

        assert detail == f"pod {release}-abc: FailedScheduling: Insufficient memory"

    async def test_a_ready_release_has_no_detail_and_reads_no_events(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        ready = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        with patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=ready)):
            assert await pod_manager.status_detail(sample_session) is None
        core_api.list_namespaced_event.assert_not_awaited()

    async def test_nothing_known_is_none(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        core_api.list_namespaced_event.return_value = SimpleNamespace(items=[])
        with patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=None)):
            assert await pod_manager.status_detail(sample_session) is None

    async def test_forbidden_events_name_the_missing_grant(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        forbidden = Exception("Forbidden")
        forbidden.status = 403
        core_api.list_namespaced_event.side_effect = forbidden
        with (
            patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=None)),
            pytest.raises(RuntimeError, match="grant events get/list"),
        ):
            await pod_manager.status_detail(sample_session)


class TestResidentCredentials:
    """deployment.credentials: the owner's credentials, injected into the ravn container."""

    _DECLARED = [{"integration": "xai", "files": {"/run/secrets/xai-api-key": "api_key"}}]

    def _wire(
        self,
        pod_manager: FluxPodManager,
        *,
        connections: list | None = None,
        stored_keys: list[str] | None = None,
        supports: bool = True,
    ) -> MagicMock:
        from volundr.domain.models import PodSpecAdditions

        injection = MagicMock()
        injection.supports_workloads = supports
        injection.workload_pod_spec_additions = AsyncMock(
            return_value=PodSpecAdditions(annotations={"vault.hashicorp.com/role": "r"})
        )
        injection.cleanup_workload = AsyncMock()
        integrations = AsyncMock()
        integrations.list_connections.return_value = (
            connections
            if connections is not None
            else [MagicMock(slug="xai", enabled=True, credential_name="xai-key")]
        )
        store = AsyncMock()
        store.get.return_value = MagicMock(keys=stored_keys or ["api_key"])
        pod_manager.set_resident_credentials(
            secret_injection=injection, integrations=integrations, credential_store=store
        )
        pod_manager._resident_platform_base_url = "https://niuu.example.test"
        return injection

    def _profile(self, credentials: object) -> ResidentDeploymentProfile:
        profile = _resident_profile()
        profile.deployment["credentials"] = credentials
        return profile

    async def test_owner_integration_is_injected_into_the_ravn_container(
        self, pod_manager: FluxPodManager, mock_api
    ) -> None:
        injection = self._wire(pod_manager)
        runtime = _resident_runtime()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(runtime, self._profile(self._DECLARED))

        values = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]["values"]
        assert values["podAnnotations"]["vault.hashicorp.com/role"] == "r"
        args, kwargs = injection.workload_pod_spec_additions.await_args
        assert args[0] == "user-a"
        assert args[1][0].credential_name == "xai-key"
        assert args[1][0].file_mappings == {"/run/secrets/xai-api-key": "api_key"}
        assert kwargs == {
            "workload_id": str(runtime.id),
            "service_account_name": f"resident-{runtime.id}",
            "inject_containers": "ravn",
            "tenant_id": "tenant-a",
        }

    async def test_a_named_credential_needs_no_integration(
        self, pod_manager: FluxPodManager, mock_api
    ) -> None:
        injection = self._wire(pod_manager)
        declared = [{"credential": "my-key", "files": {"/run/secrets/k": "api_key"}}]
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), self._profile(declared))

        assert (
            injection.workload_pod_spec_additions.await_args.args[1][0].credential_name == "my-key"
        )

    async def test_no_declared_credentials_inject_nothing(
        self, pod_manager: FluxPodManager, mock_api
    ) -> None:
        injection = self._wire(pod_manager)
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), _resident_profile())

        injection.workload_pod_spec_additions.assert_not_awaited()

    @pytest.mark.parametrize(
        ("credentials", "message"),
        [
            ("xai", "must be a list"),
            (["xai"], "must be objects"),
            ([{"files": {"/run/secrets/k": "f"}}], "exactly one of integration or credential"),
            (
                [{"integration": "xai", "credential": "k", "files": {"/run/secrets/k": "f"}}],
                "exactly one of integration or credential",
            ),
            ([{"integration": "xai"}], "map files"),
            ([{"integration": "xai", "files": {"/home/k": "api_key"}}], "under /run/secrets/"),
            (
                [{"integration": "xai", "files": {"/run/secrets/k": "token"}}],
                r"no field\(s\) \['token'\]",
            ),
        ],
    )
    async def test_bad_declarations_are_fatal(
        self, pod_manager: FluxPodManager, credentials: object, message: str
    ) -> None:
        self._wire(pod_manager)
        with pytest.raises(ValueError, match=message):
            await pod_manager._render_resident_values(
                _resident_runtime(), self._profile(credentials)
            )

    @pytest.mark.parametrize("count", [0, 2])
    async def test_the_owner_needs_exactly_one_connection(
        self, pod_manager: FluxPodManager, count: int
    ) -> None:
        connections = [
            MagicMock(slug="xai", enabled=True, credential_name=f"k{i}") for i in range(count)
        ]
        self._wire(pod_manager, connections=connections)
        with pytest.raises(
            ValueError, match=f"exactly one enabled xai connection, and has {count}"
        ):
            await pod_manager._render_resident_values(
                _resident_runtime(), self._profile(self._DECLARED)
            )

    async def test_a_missing_owner_credential_is_fatal(self, pod_manager: FluxPodManager) -> None:
        self._wire(pod_manager)
        pod_manager._credential_store.get.return_value = None
        with pytest.raises(ValueError, match="owner has no credential 'xai-key'"):
            await pod_manager._render_resident_values(
                _resident_runtime(), self._profile(self._DECLARED)
            )

    async def test_without_workload_injection_it_is_fatal(
        self, pod_manager: FluxPodManager
    ) -> None:
        self._wire(pod_manager, supports=False)
        with pytest.raises(ValueError, match="no secret injection that serves residents"):
            await pod_manager._render_resident_values(
                _resident_runtime(), self._profile(self._DECLARED)
            )

    async def test_without_the_residents_own_service_account_it_is_fatal(
        self, pod_manager: FluxPodManager
    ) -> None:
        self._wire(pod_manager)
        pod_manager._resident_platform_base_url = ""
        with pytest.raises(ValueError, match="resident platform base URL"):
            await pod_manager._render_resident_values(
                _resident_runtime(), self._profile(self._DECLARED)
            )

    async def test_deploy_records_which_credential_each_integration_bound(
        self, pod_manager: FluxPodManager, mock_api
    ) -> None:
        self._wire(pod_manager)
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.deploy(_resident_runtime(), self._profile(self._DECLARED))

        body = mock_api.create_namespaced_custom_object.call_args.kwargs["body"]
        assert body["metadata"]["annotations"]["niuu.world/resident-integrations"] == (
            '{"xai": "xai-key"}'
        )

    @pytest.mark.parametrize("rerender", ["reconcile", "restart"])
    async def test_a_rerender_keeps_the_recorded_credential_without_the_owners_login(
        self, core_api: AsyncMock, rerender: str
    ) -> None:
        # Following the platform and restart run without the owner's bearer token,
        # which reading the owner's connections needs.
        pod_manager = _platform_manager()
        injection = self._wire(pod_manager)
        pod_manager._integrations.list_connections.side_effect = RuntimeError(
            "Shared integrations require an authenticated caller bearer token"
        )
        runtime = _resident_runtime()
        _own_claims_exist(core_api, runtime)
        cluster = _Cluster(
            _running_release(runtime, **{"niuu.world/resident-integrations": '{"xai": "xai-key"}'})
        )
        profile = _platform_profile()
        profile.deployment["credentials"] = self._DECLARED

        with _on_cluster(pod_manager, cluster):
            if rerender == "reconcile":
                await pod_manager.reconcile(runtime, profile)
            else:
                await pod_manager.restart(runtime, profile)

        [rolled] = cluster.replaces
        assert rolled["spec"]["values"]["image"] == _NEW_IMAGE
        assert rolled["metadata"]["annotations"]["niuu.world/resident-integrations"] == (
            '{"xai": "xai-key"}'
        )
        assert injection.workload_pod_spec_additions.await_args.args[1][0].credential_name == (
            "xai-key"
        )
        pod_manager._integrations.list_connections.assert_not_awaited()

    async def test_a_release_without_a_recorded_credential_reads_the_owners(
        self, core_api: AsyncMock
    ) -> None:
        pod_manager = _platform_manager()
        self._wire(pod_manager)
        runtime = _resident_runtime()
        _own_claims_exist(core_api, runtime)
        cluster = _Cluster(_running_release(runtime))
        profile = _platform_profile()
        profile.deployment["credentials"] = self._DECLARED

        with _on_cluster(pod_manager, cluster):
            await pod_manager.restart(runtime, profile)

        [rolled] = cluster.replaces
        assert rolled["metadata"]["annotations"]["niuu.world/resident-integrations"] == (
            '{"xai": "xai-key"}'
        )
        pod_manager._integrations.list_connections.assert_awaited_once_with("user-a")

    @pytest.mark.parametrize("recorded", ['["xai-key"]', '{"xai": ""}', '{"xai": 3}'])
    async def test_a_malformed_recorded_credential_is_fatal(
        self, core_api: AsyncMock, recorded: str
    ) -> None:
        pod_manager = _platform_manager()
        self._wire(pod_manager)
        runtime = _resident_runtime()
        cluster = _Cluster(
            _running_release(runtime, **{"niuu.world/resident-integrations": recorded})
        )
        profile = _platform_profile()
        profile.deployment["credentials"] = self._DECLARED

        with _on_cluster(pod_manager, cluster), pytest.raises(ValueError, match="must map"):
            await pod_manager.restart(runtime, profile)

    async def test_delete_removes_the_residents_credential_access(
        self, pod_manager: FluxPodManager, mock_api
    ) -> None:
        injection = self._wire(pod_manager)
        runtime = _resident_runtime()
        with patch.object(pod_manager, "_get_api", return_value=mock_api):
            await pod_manager.delete(runtime)

        injection.cleanup_workload.assert_awaited_once_with(str(runtime.id))
