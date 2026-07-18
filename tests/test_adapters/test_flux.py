"""Tests for Flux HelmRelease pod manager adapter."""

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
from volundr.domain.ports import ResidentRuntimeObservation


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
        assert values["envVars"] == [{"name": "SKULD__MESH__ENABLED", "value": "true"}]
        assert len(values["extraVolumes"]) == 1
        assert values["extraVolumes"][0]["name"] == "csi-vol"
        assert len(values["extraVolumeMounts"]) == 1
        assert values["extraVolumeMounts"][0]["mountPath"] == "/run/secrets/user"
        assert values["extraInitContainers"] == [
            {"name": "write-ravn-cfg-coder", "image": "busybox"}
        ]
        assert values["extraContainers"] == [
            {"name": "ravn-coder", "image": "ghcr.io/niuulabs/ravn:test"}
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

    async def test_reconcile_combines_flux_and_workload_state(
        self,
        pod_manager: FluxPodManager,
    ) -> None:
        runtime = _resident_runtime()
        helmrelease = {
            "spec": {"values": {"replicaCount": 1}},
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
            await pod_manager.restart(runtime, _resident_profile())
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
        restart_body = patch_release.await_args_list[2].args[1]
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
