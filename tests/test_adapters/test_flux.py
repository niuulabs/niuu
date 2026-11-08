"""Tests for Flux HelmRelease pod manager adapter."""

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
)
from volundr.domain.models import (
    GitSource,
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
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
    """CoreV1Api for the session's pod and event reads."""
    api = AsyncMock()
    monkeypatch.setattr(FluxPodManager, "_get_core_api", AsyncMock(return_value=api))
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

    def test_accepts_the_connection_settings_every_pod_manager_is_given(self):
        pm = FluxPodManager(
            namespace="test",
            server_public_host="forge.example.com",
            server_host="0.0.0.0",
            server_port=8080,
            gateway_endpoint="gateway:8080",
            token_url="https://idp.example.com/token",
            client_id="forge",
        )
        assert pm._namespace == "test"

    @pytest.mark.parametrize(
        "removed",
        [
            "resident_platform_base_url",
            "resident_triggers_enabled",
            "resident_budget_enabled",
            "resident_home_size",
            "resident_workspace_size",
            "resident_storage_class",
            "resident_access_mode",
        ],
    )
    def test_refuses_a_kwarg_of_the_removed_resident_controller(self, removed: str):
        with pytest.raises(ValueError, match="resident controller was removed") as raised:
            FluxPodManager(namespace="test", **{removed: "anything"})

        assert removed in str(raised.value)
        assert "podManager.kwargs" in str(raised.value)

    def test_names_every_removed_resident_kwarg_it_refuses(self):
        with pytest.raises(ValueError) as raised:
            FluxPodManager(
                resident_platform_base_url="https://niuu.example.test",
                resident_budget_enabled=True,
                server_host="0.0.0.0",
            )

        message = str(raised.value)
        assert "resident_platform_base_url" in message
        assert "resident_budget_enabled" in message
        assert "server_host" not in message


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


def _owned_pod(
    release: str,
    suffix: str,
    *,
    minute: int,
    template_hash: str | None = "abc",
    owner_kind: str = "ReplicaSet",
    owner_name: str | None = None,
    deleting: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=f"{release}-{suffix}",
            creation_timestamp=datetime(2026, 11, 6, 12, minute, tzinfo=UTC),
            deletion_timestamp=datetime(2026, 11, 6, 13, 0, tzinfo=UTC) if deleting else None,
            labels={"pod-template-hash": template_hash} if template_hash else {},
            owner_references=[
                SimpleNamespace(
                    kind=owner_kind,
                    name=owner_name or f"{release}-{template_hash}",
                )
            ],
        )
    )


class TestStartupStages:
    """The launch stages of a Flux session, read from its release, pod and events."""

    def test_flux_reports_startup_stages(self, pod_manager: FluxPodManager) -> None:
        assert pod_manager.reports_startup_stages is True

    async def test_no_helmrelease_is_an_empty_launch(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        with patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=None)):
            assert await pod_manager.startup_stages(sample_session) == ()
        core_api.list_namespaced_pod.assert_not_awaited()
        core_api.list_namespaced_event.assert_not_awaited()

    async def test_release_without_a_pod_reads_no_events(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        release = {"metadata": {"creationTimestamp": "2026-11-06T12:00:00Z"}}
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[])
        with patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=release)):
            stages = await pod_manager.startup_stages(sample_session)

        assert [s.key for s in stages] == ["release", "schedule", "containers", "release_ready"]
        assert stages[0].state.value == "active"
        core_api.list_namespaced_event.assert_not_awaited()

    async def test_stages_come_from_the_newest_session_pod_and_its_events(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        name = f"skuld-{sample_session.id}"
        older = _owned_pod(name, "old", minute=1)
        newest = _owned_pod(name, "new", minute=5)
        events = [object()]
        release = {"metadata": {"creationTimestamp": "2026-11-06T12:00:00Z"}}
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[older, newest])
        core_api.list_namespaced_event.return_value = SimpleNamespace(items=events)
        sentinel = (object(),)
        with (
            patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value=release)),
            patch(
                "volundr.adapters.outbound.flux.pod_startup_stages", return_value=sentinel
            ) as stages,
        ):
            result = await pod_manager.startup_stages(sample_session)

        assert result is sentinel
        stages.assert_called_once_with(release, newest, events)
        core_api.list_namespaced_pod.assert_awaited_once_with(
            "test-ns", label_selector=f"app.kubernetes.io/instance={name}"
        )
        core_api.list_namespaced_event.assert_awaited_once_with(
            "test-ns",
            field_selector=f"involvedObject.kind=Pod,involvedObject.name={name}-new",
        )

    async def test_session_pod_ignores_gateway_deleting_and_foreign_pods(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        name = f"skuld-{sample_session.id}"
        mine = _owned_pod(name, "mine", minute=1)
        core_api.list_namespaced_pod.return_value = SimpleNamespace(
            items=[
                mine,
                # A pod of a different Deployment of the same release.
                _owned_pod(
                    name, "gw", minute=9, template_hash="def", owner_name=f"{name}-gateway-def"
                ),
                _owned_pod(name, "dying", minute=8, deleting=True),
                _owned_pod(name, "other", minute=7, owner_name="skuld-someone-else-abc"),
                _owned_pod(name, "job", minute=6, owner_kind="Job"),
                _owned_pod(name, "unhashed", minute=5, template_hash=None),
            ]
        )

        assert await pod_manager._session_pod(core_api, name) is mine

    async def test_session_pod_without_a_match_is_none(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        name = f"skuld-{sample_session.id}"
        core_api.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_owned_pod(name, "dying", minute=1, deleting=True)]
        )

        assert await pod_manager._session_pod(core_api, name) is None

    async def test_forbidden_events_name_the_missing_grant(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        name = f"skuld-{sample_session.id}"
        forbidden = Exception("Forbidden")
        forbidden.status = 403
        core_api.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_owned_pod(name, "mine", minute=1)]
        )
        core_api.list_namespaced_event.side_effect = forbidden
        with (
            patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value={})),
            pytest.raises(RuntimeError, match="grant events get/list"),
        ):
            await pod_manager.startup_stages(sample_session)

    async def test_other_event_errors_are_not_retyped(
        self, pod_manager: FluxPodManager, sample_session: Session, core_api: AsyncMock
    ) -> None:
        name = f"skuld-{sample_session.id}"
        broken = Exception("etcd timeout")
        broken.status = 500
        core_api.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_owned_pod(name, "mine", minute=1)]
        )
        core_api.list_namespaced_event.side_effect = broken
        with (
            patch.object(pod_manager, "_get_helmrelease", AsyncMock(return_value={})),
            pytest.raises(Exception, match="etcd timeout") as raised,
        ):
            await pod_manager.startup_stages(sample_session)

        assert raised.value is broken


class TestFluxSessionProxyTarget:
    """The in-cluster hop to a session's Skuld (live-preview router)."""

    def test_a_running_session_targets_its_own_service(self, pod_manager, sample_session):
        sample_session.status = SessionStatus.RUNNING

        target = pod_manager.session_proxy_target(sample_session)

        host = f"skuld-{sample_session.id}.test-ns.svc.cluster.local"
        assert target is not None
        assert target.service_url == f"http://{host}:8080/"
        assert target.connect_host == host
        assert target.connect_port == 8080

    def test_the_chart_service_port_is_configurable(self, sample_session):
        manager = FluxPodManager(namespace="ns", session_service_port=9090)
        sample_session.status = SessionStatus.RUNNING

        target = manager.session_proxy_target(sample_session)

        assert target is not None
        assert target.service_url.endswith(":9090/")
        assert target.connect_port == 9090

    @pytest.mark.parametrize(
        "status",
        [s for s in SessionStatus if s is not SessionStatus.RUNNING],
    )
    def test_only_a_running_session_has_a_target(self, pod_manager, sample_session, status):
        sample_session.status = status

        assert pod_manager.session_proxy_target(sample_session) is None
