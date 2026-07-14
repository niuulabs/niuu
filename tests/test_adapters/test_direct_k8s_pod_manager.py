"""Tests for DirectK8sPodManager adapter.

Tests against the kubernetes-asyncio client with mocked API calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.conftest import make_spec
from volundr.adapters.outbound.direct_k8s_pod_manager import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_READINESS_TIMEOUT,
    MANAGED_BY_LABEL,
    MANAGED_BY_VALUE,
    SESSION_LABEL,
    SESSION_SERVICE_PORT,
    DirectK8sPodManager,
)
from volundr.domain.models import GitSource, Session, SessionStatus


@pytest.fixture
def sample_session() -> Session:
    """Create a sample session for testing."""
    return Session(
        id=uuid4(),
        name="Test K3s Session",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
    )


@pytest.fixture
def pod_manager() -> DirectK8sPodManager:
    """Create a DirectK8sPodManager for testing."""
    return DirectK8sPodManager(
        namespace="test-ns",
        kubeconfig="/test/kubeconfig",
        base_path="/s",
        ingress_class="traefik",
        skuld_image="ghcr.io/niuulabs/skuld:test",
        nginx_image="nginx:test",
        devrunner_image="ghcr.io/niuulabs/devrunner:test",
        db_host="host.k3d.internal",
        db_port=5433,
        db_user="volundr",
        db_password="testpass",
        db_name="volundr",
    )


class TestConstructor:
    """Test DirectK8sPodManager constructor."""

    def test_default_values(self) -> None:
        pm = DirectK8sPodManager()
        assert pm._namespace == "volundr"
        assert pm._kubeconfig == ""
        assert pm._base_path == "/s"
        assert pm._ingress_class == "traefik"
        assert pm._poll_interval == DEFAULT_POLL_INTERVAL
        assert pm._readiness_timeout == DEFAULT_READINESS_TIMEOUT

    def test_custom_values(self, pod_manager: DirectK8sPodManager) -> None:
        assert pod_manager._namespace == "test-ns"
        assert pod_manager._kubeconfig == "/test/kubeconfig"
        assert pod_manager._base_path == "/s"
        assert pod_manager._skuld_image == "ghcr.io/niuulabs/skuld:test"
        assert pod_manager._db_host == "host.k3d.internal"
        assert pod_manager._db_port == 5433
        assert pod_manager._db_password == "testpass"

    def test_custom_home_mount_path(self) -> None:
        pm = DirectK8sPodManager(home_mount_path="/custom/home")
        assert pm._home_mount_path == "/custom/home"

    def test_default_home_mount_path(self) -> None:
        pm = DirectK8sPodManager()
        assert pm._home_mount_path == "/volundr/home"

    def test_extra_kwargs_ignored(self) -> None:
        pm = DirectK8sPodManager(
            namespace="test",
            unknown_key="should-not-fail",
            another_key=42,
        )
        assert pm._namespace == "test"


class TestWaitForReady:
    async def test_timeout_returns_current_deployment_status(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        pod_manager.status = AsyncMock(return_value=SessionStatus.STARTING)  # type: ignore[method-assign]

        result = await pod_manager.wait_for_ready(sample_session, timeout=0)

        assert result == SessionStatus.STARTING


class TestResourceNames:
    """Test resource name generation."""

    def test_release_name(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        name = pod_manager._release_name(sample_session)
        assert name == f"skuld-{sample_session.id}"

    def test_session_path(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        path = pod_manager._session_path(sample_session)
        assert path == f"/s/{sample_session.id}"

    def test_chat_endpoint(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        endpoint = pod_manager._chat_endpoint(sample_session)
        assert endpoint == f"ws://k3d-volundr-serverlb/s/{sample_session.id}/session"

    def test_code_endpoint(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        endpoint = pod_manager._code_endpoint(sample_session)
        assert endpoint == f"http://k3d-volundr-serverlb/s/{sample_session.id}/"

    def test_middleware_name(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        name = pod_manager._middleware_name(sample_session)
        assert name == f"test-ns-strip-{sample_session.id}"


class TestLabels:
    """Test label generation."""

    def test_build_labels(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        labels = pod_manager._build_labels(sample_session)
        assert labels[MANAGED_BY_LABEL] == MANAGED_BY_VALUE
        assert labels[SESSION_LABEL] == str(sample_session.id)
        assert labels["app.kubernetes.io/name"] == "skuld"
        assert labels["app.kubernetes.io/instance"] == f"skuld-{sample_session.id}"


class TestEnvironment:
    """Test environment variable building."""

    def test_build_env_basic(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec()
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["SESSION_ID"] == str(sample_session.id)
        assert env_dict["SESSION_NAME"] == sample_session.name
        assert env_dict["DATABASE__HOST"] == "host.k3d.internal"
        assert env_dict["DATABASE__PORT"] == "5433"
        assert env_dict["DATABASE__USER"] == "volundr"
        assert env_dict["DATABASE__PASSWORD"] == "testpass"
        assert env_dict["DATABASE__NAME"] == "volundr"

    def test_build_env_with_git(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(
            git={"cloneUrl": "https://github.com/org/repo", "branch": "develop"},
        )
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["GIT_CLONE_URL"] == "https://github.com/org/repo"
        assert env_dict["GIT_BRANCH"] == "develop"

    def test_build_env_with_session_model(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(session={"model": "claude-opus-4-20250514"})
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["SKULD__SESSION__MODEL"] == "claude-opus-4-20250514"
        assert env_dict["MODEL"] == "claude-opus-4-20250514"

    def test_build_env_with_extra_env(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(env={"CUSTOM_VAR": "custom_value", "PORT": 3000})
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["CUSTOM_VAR"] == "custom_value"
        assert env_dict["PORT"] == "3000"

    def test_build_env_with_system_prompt(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(session={"systemPrompt": "You are an agent."})
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["SKULD__SESSION__SYSTEM_PROMPT"] == "You are an agent."

    def test_build_env_with_initial_prompt(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(session={"initialPrompt": "Fix the auth bug."})
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["SKULD__SESSION__INITIAL_PROMPT"] == "Fix the auth bug."

    def test_build_env_with_broker_telegram(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(
            broker={
                "telegram": {
                    "enabled": True,
                    "botToken": "bot-token",
                    "chatId": "chat-123",
                    "notifyOnly": True,
                    "topicMode": "topic_per_session",
                }
            }
        )
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["SKULD__TELEGRAM__ENABLED"] == "true"
        assert env_dict["SKULD__TELEGRAM__BOT_TOKEN"] == "bot-token"
        assert env_dict["SKULD__TELEGRAM__CHAT_ID"] == "chat-123"
        assert env_dict["SKULD__TELEGRAM__NOTIFY_ONLY"] == "true"
        assert env_dict["SKULD__TELEGRAM__TOPIC_MODE"] == "topic_per_session"

    def test_build_env_with_broker_telegram_fixed_topic(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(
            broker={
                "telegram": {
                    "enabled": True,
                    "botToken": "bot-token",
                    "chatId": "chat-123",
                    "notifyOnly": True,
                    "topicMode": "fixed_topic",
                    "messageThreadId": 77,
                }
            }
        )
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert env_dict["SKULD__TELEGRAM__TOPIC_MODE"] == "fixed_topic"
        assert env_dict["SKULD__TELEGRAM__MESSAGE_THREAD_ID"] == "77"

    def test_build_env_with_mcp_servers(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(
            mcpServers=[
                {
                    "name": "mimir-local",
                    "type": "stdio",
                    "command": "python3",
                    "args": ["-m", "mimir", "mcp", "--path", "/mimir/local", "--name", "local"],
                }
            ]
        )
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert json.loads(env_dict["SKULD__MCP_SERVERS"]) == [
            {
                "name": "mimir-local",
                "type": "stdio",
                "command": "python3",
                "args": ["-m", "mimir", "mcp", "--path", "/mimir/local", "--name", "local"],
            }
        ]

    def test_build_env_without_prompts_omits_vars(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec()
        env = pod_manager._build_env(sample_session, spec)

        env_dict = {e["name"]: e["value"] for e in env if "value" in e}
        assert "SKULD__SESSION__SYSTEM_PROMPT" not in env_dict
        assert "SKULD__SESSION__INITIAL_PROMPT" not in env_dict


class TestManifests:
    """Test manifest generation."""

    def test_build_deployment_manifest(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec()
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)

        assert manifest["apiVersion"] == "apps/v1"
        assert manifest["kind"] == "Deployment"
        assert manifest["metadata"]["name"] == f"skuld-{sample_session.id}"
        assert manifest["metadata"]["namespace"] == "test-ns"
        assert manifest["spec"]["replicas"] == 1

        containers = manifest["spec"]["template"]["spec"]["containers"]
        container_names = [c["name"] for c in containers]
        assert "nginx" in container_names
        assert "skuld" in container_names
        assert "vscode-reh" in container_names
        assert "devrunner" in container_names

        # Check skuld env vars.
        skuld = next(c for c in containers if c["name"] == "skuld")
        env_dict = {e["name"]: e["value"] for e in skuld["env"] if "value" in e}
        assert env_dict["SESSION_ID"] == str(sample_session.id)
        assert env_dict["DATABASE__HOST"] == "host.k3d.internal"

        # Without homeVolume in spec, HOME should not be set
        assert "HOME" not in env_dict

    def test_build_deployment_with_home_volume(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(
            homeVolume={
                "enabled": True,
                "existingClaim": "volundr-user-home",
                "mountPath": "/volundr/home",
            },
        )
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        containers = manifest["spec"]["template"]["spec"]["containers"]

        # Check HOME is set on all workload containers
        for name in ("skuld", "vscode-reh", "devrunner"):
            container = next(c for c in containers if c["name"] == name)
            cenv = {e["name"]: e["value"] for e in container["env"] if "value" in e}
            assert cenv["HOME"] == "/volundr/home"

        # Check home PVC volume is added
        volumes = manifest["spec"]["template"]["spec"]["volumes"]
        home_vol = next((v for v in volumes if v["name"] == "home"), None)
        assert home_vol is not None
        assert home_vol["persistentVolumeClaim"]["claimName"] == "volundr-user-home"

        # Check home mount is added to workload containers
        skuld = next(c for c in containers if c["name"] == "skuld")
        mount_names = [m["name"] for m in skuld["volumeMounts"]]
        assert "home" in mount_names

    def test_build_service_manifest(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        manifest = pod_manager._build_service_manifest(sample_session)

        assert manifest["apiVersion"] == "v1"
        assert manifest["kind"] == "Service"
        assert manifest["metadata"]["name"] == f"skuld-{sample_session.id}"
        assert manifest["spec"]["type"] == "ClusterIP"
        assert manifest["spec"]["ports"][0]["port"] == SESSION_SERVICE_PORT

    def test_build_ingress_manifest(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        manifest = pod_manager._build_ingress_manifest(sample_session)

        assert manifest["apiVersion"] == "networking.k8s.io/v1"
        assert manifest["kind"] == "Ingress"
        assert manifest["spec"]["ingressClassName"] == "traefik"

        # Verify path-based routing with no host.
        rule = manifest["spec"]["rules"][0]
        assert "host" not in rule
        path = rule["http"]["paths"][0]
        assert path["path"] == f"/s/{sample_session.id}"
        assert path["pathType"] == "Prefix"
        assert path["backend"]["service"]["name"] == f"skuld-{sample_session.id}"
        assert path["backend"]["service"]["port"]["number"] == SESSION_SERVICE_PORT

        # Verify Traefik strip-prefix annotation.
        annotations = manifest["metadata"]["annotations"]
        expected_mw = f"test-ns-skuld-{sample_session.id}-strip@kubernetescrd"
        assert annotations["traefik.ingress.kubernetes.io/router.middlewares"] == expected_mw


class TestResourceOverrides:
    """Test that user-specified resources are applied to the deployment manifest."""

    def test_default_resources_when_no_overrides(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec()
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        containers = {c["name"]: c for c in manifest["spec"]["template"]["spec"]["containers"]}
        # Defaults should be present
        assert containers["devrunner"]["resources"]["requests"]["memory"] == "512Mi"
        assert containers["devrunner"]["resources"]["limits"]["cpu"] == "2000m"
        assert containers["skuld"]["resources"]["limits"]["memory"] == "1Gi"
        assert containers["nginx"]["resources"]["requests"]["cpu"] == "10m"

    def test_user_cpu_memory_applied_to_devrunner(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(
            resources={
                "requests": {"cpu": "4", "memory": "16Gi"},
                "limits": {"cpu": "4", "memory": "16Gi"},
            },
            localServices={
                "devrunner": {
                    "resources": {
                        "requests": {"cpu": "4", "memory": "16Gi"},
                        "limits": {"cpu": "4", "memory": "16Gi"},
                    }
                }
            },
        )
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        containers = {c["name"]: c for c in manifest["spec"]["template"]["spec"]["containers"]}
        # Devrunner should have user-specified resources
        assert containers["devrunner"]["resources"]["requests"]["cpu"] == "4"
        assert containers["devrunner"]["resources"]["requests"]["memory"] == "16Gi"
        assert containers["devrunner"]["resources"]["limits"]["cpu"] == "4"
        assert containers["devrunner"]["resources"]["limits"]["memory"] == "16Gi"
        # Skuld should also reflect the override (top-level resources)
        assert containers["skuld"]["resources"]["requests"]["cpu"] == "4"
        # Nginx stays at defaults
        assert containers["nginx"]["resources"]["requests"]["cpu"] == "10m"

    def test_gpu_applied_to_devrunner_limits(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(
            localServices={
                "devrunner": {
                    "resources": {
                        "limits": {"nvidia.com/gpu": "2"},
                    }
                }
            },
        )
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        containers = {c["name"]: c for c in manifest["spec"]["template"]["spec"]["containers"]}
        assert containers["devrunner"]["resources"]["limits"]["nvidia.com/gpu"] == "2"
        # Other containers should not have GPU
        assert "nvidia.com/gpu" not in containers["skuld"]["resources"]["limits"]

    def test_node_selector_applied_to_pod_spec(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(nodeSelector={"nvidia.com/gpu.product": "A100"})
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        pod_spec = manifest["spec"]["template"]["spec"]
        assert pod_spec["nodeSelector"] == {"nvidia.com/gpu.product": "A100"}

    def test_tolerations_applied_to_pod_spec(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        toleration = {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
        spec = make_spec(tolerations=[toleration])
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        pod_spec = manifest["spec"]["template"]["spec"]
        assert pod_spec["tolerations"] == [toleration]

    def test_runtime_class_name_applied_to_pod_spec(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec(runtimeClassName="nvidia")
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        pod_spec = manifest["spec"]["template"]["spec"]
        assert pod_spec["runtimeClassName"] == "nvidia"

    def test_no_scheduling_fields_when_not_specified(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        spec = make_spec()
        manifest = pod_manager._build_deployment_manifest(sample_session, spec)
        pod_spec = manifest["spec"]["template"]["spec"]
        assert "nodeSelector" not in pod_spec
        assert "tolerations" not in pod_spec
        assert "runtimeClassName" not in pod_spec


class TestStripPrefixMiddleware:
    """Test Traefik middleware generation."""

    def test_build_strip_prefix_middleware(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        mw = pod_manager._build_strip_prefix_middleware(sample_session)

        assert mw["apiVersion"] == "traefik.io/v1alpha1"
        assert mw["kind"] == "Middleware"
        assert mw["metadata"]["name"] == f"skuld-{sample_session.id}-strip"
        assert mw["metadata"]["namespace"] == "test-ns"
        assert mw["spec"]["stripPrefix"]["prefixes"] == [f"/s/{sample_session.id}"]


class TestDeploymentStatus:
    """Test deployment status mapping."""

    def test_running(self) -> None:
        deployment = MagicMock()
        deployment.spec.replicas = 1
        deployment.status.ready_replicas = 1
        deployment.status.conditions = []

        status = DirectK8sPodManager._map_deployment_status(deployment)
        assert status == SessionStatus.RUNNING

    def test_starting_no_ready(self) -> None:
        deployment = MagicMock()
        deployment.spec.replicas = 1
        deployment.status.ready_replicas = 0
        deployment.status.conditions = []

        status = DirectK8sPodManager._map_deployment_status(deployment)
        assert status == SessionStatus.STARTING

    def test_starting_none_ready(self) -> None:
        deployment = MagicMock()
        deployment.spec.replicas = 1
        deployment.status.ready_replicas = None
        deployment.status.conditions = []

        status = DirectK8sPodManager._map_deployment_status(deployment)
        assert status == SessionStatus.STARTING

    def test_no_status(self) -> None:
        deployment = MagicMock()
        deployment.status = None

        status = DirectK8sPodManager._map_deployment_status(deployment)
        assert status == SessionStatus.STARTING

    def test_failed_progressing_false(self) -> None:
        condition = MagicMock()
        condition.type = "Progressing"
        condition.status = "False"

        deployment = MagicMock()
        deployment.spec.replicas = 1
        deployment.status.ready_replicas = 0
        deployment.status.conditions = [condition]

        status = DirectK8sPodManager._map_deployment_status(deployment)
        assert status == SessionStatus.FAILED


class TestStart:
    """Test the start method with mocked K8s client."""

    @pytest.mark.asyncio
    async def test_start_creates_resources(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        pod_manager._api_client = MagicMock()
        spec = make_spec()

        mock_cr = AsyncMock()
        mock_apply = AsyncMock()
        with (
            patch.object(
                pod_manager,
                "_apply_custom_resource",
                mock_cr,
            ),
            patch.object(
                pod_manager,
                "_apply_resource",
                mock_apply,
            ),
        ):
            result = await pod_manager.start(sample_session, spec)

        mock_cr.assert_called_once()

        assert mock_apply.call_count == 4
        call_classes = [c.kwargs["api_class"] for c in mock_apply.call_args_list]
        assert "AppsV1Api" in call_classes
        assert "NetworkingV1Api" in call_classes
        # CoreV1Api appears twice: ConfigMap + Service
        assert call_classes.count("CoreV1Api") == 2

        assert result.chat_endpoint == f"ws://k3d-volundr-serverlb/s/{sample_session.id}/session"
        assert result.code_endpoint == f"http://k3d-volundr-serverlb/s/{sample_session.id}/"
        assert result.pod_name == f"skuld-{sample_session.id}"


class TestStop:
    """Test the stop method with mocked K8s client."""

    @pytest.mark.asyncio
    async def test_stop_deletes_resources(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        pod_manager._api_client = MagicMock()

        mock_delete = AsyncMock(return_value=True)
        mock_delete_cr = AsyncMock()
        with (
            patch.object(
                pod_manager,
                "_delete_resource",
                mock_delete,
            ),
            patch.object(
                pod_manager,
                "_delete_custom_resource",
                mock_delete_cr,
            ),
        ):
            result = await pod_manager.stop(sample_session)

        assert result is True
        assert mock_delete.call_count == 4
        call_classes = [c.args[0] for c in mock_delete.call_args_list]
        assert "AppsV1Api" in call_classes
        assert "NetworkingV1Api" in call_classes
        # CoreV1Api appears twice: Service + ConfigMap
        assert call_classes.count("CoreV1Api") == 2
        mock_delete_cr.assert_called_once()


class TestStatus:
    """Test the status method with mocked K8s client."""

    @pytest.mark.asyncio
    async def test_status_running(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        deployment = MagicMock()
        deployment.spec.replicas = 1
        deployment.status.ready_replicas = 1
        deployment.status.conditions = []

        pod_manager._api_client = MagicMock()

        with patch.object(
            pod_manager,
            "_read_deployment",
            new_callable=AsyncMock,
            return_value=deployment,
        ):
            status = await pod_manager.status(sample_session)

        assert status == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_status_not_found(
        self,
        pod_manager: DirectK8sPodManager,
        sample_session: Session,
    ) -> None:
        pod_manager._api_client = MagicMock()

        with patch.object(
            pod_manager,
            "_read_deployment",
            new_callable=AsyncMock,
            return_value=None,
        ):
            status = await pod_manager.status(sample_session)

        assert status == SessionStatus.STOPPED


class TestCredentialStore:
    """Test credential store injection."""

    def test_set_credential_store(
        self,
        pod_manager: DirectK8sPodManager,
    ) -> None:
        assert pod_manager._credential_store is None

        mock_store = MagicMock()
        pod_manager.set_credential_store(mock_store)
        assert pod_manager._credential_store is mock_store


class TestClose:
    """Test client cleanup."""

    @pytest.mark.asyncio
    async def test_close_clears_clients(
        self,
        pod_manager: DirectK8sPodManager,
    ) -> None:
        mock_client = AsyncMock()
        pod_manager._api_client = mock_client

        await pod_manager.close()

        assert pod_manager._api_client is None
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_noop_when_not_initialized(
        self,
        pod_manager: DirectK8sPodManager,
    ) -> None:
        # Should not raise.
        await pod_manager.close()
        assert pod_manager._api_client is None
