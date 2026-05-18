"""Tests for the OpenBao injector-based secret injection adapter."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from volundr.adapters.outbound.openbao_secret_injection import OpenBaoAgentInjectionAdapter
from volundr.domain.models import CredentialMapping


def _install_fake_kubernetes(monkeypatch: pytest.MonkeyPatch):
    class ConfigError(Exception):
        pass

    class V1ObjectMeta:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class V1ServiceAccount:
        def __init__(self, metadata):
            self.metadata = metadata

    class V1ConfigMap:
        def __init__(self, metadata, data):
            self.metadata = metadata
            self.data = data

    class ApiClient:
        async def close(self):
            return None

    class CoreV1Api:
        def __init__(self, api_client):
            self.api_client = api_client

    client_module = ModuleType("kubernetes_asyncio.client")
    client_module.V1ObjectMeta = V1ObjectMeta
    client_module.V1ServiceAccount = V1ServiceAccount
    client_module.V1ConfigMap = V1ConfigMap
    client_module.ApiClient = ApiClient
    client_module.CoreV1Api = CoreV1Api

    async def load_kube_config():
        return None

    config_module = ModuleType("kubernetes_asyncio.config")
    config_module.ConfigException = ConfigError
    config_module.load_incluster_config = MagicMock(return_value=None)
    config_module.load_kube_config = AsyncMock(side_effect=load_kube_config)

    package = ModuleType("kubernetes_asyncio")
    package.client = client_module
    package.config = config_module

    monkeypatch.setitem(sys.modules, "kubernetes_asyncio", package)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio.client", client_module)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio.config", config_module)
    return client_module, config_module


@pytest.fixture()
def adapter() -> OpenBaoAgentInjectionAdapter:
    return OpenBaoAgentInjectionAdapter(
        openbao_url="https://openbao.ymir.niuu.world",
        namespace="skuld",
        openbao_namespace="apps",
        mount_path="volundr",
        auth_path="jwt-valhalla",
        audience="https://k8s-issuer.valhalla.asgard.niuu.world",
        token="root-token",
        agent_image="openbao/openbao:2.5.3",
    )


class TestOpenBaoAgentInjectionAdapter:
    @pytest.mark.asyncio()
    async def test_pod_spec_additions_returns_service_account_and_annotations(self, adapter):
        result = await adapter.pod_spec_additions("alice", "session-123")

        assert result.service_account == "openbao-session-session-123"
        assert result.annotations["vault.hashicorp.com/agent-inject"] == "true"
        assert (
            result.annotations["vault.hashicorp.com/agent-configmap"]
            == "openbao-agent-session-123"
        )
        assert result.annotations["vault.hashicorp.com/agent-pre-populate-only"] == "true"
        assert result.annotations["vault.hashicorp.com/auth-type"] == "jwt"
        assert result.annotations["vault.hashicorp.com/auth-path"] == "auth/jwt-valhalla"
        assert result.annotations["vault.hashicorp.com/secret-volume-path"] == "/run/secrets"
        assert result.annotations["vault.hashicorp.com/namespace"] == "apps"
        assert result.annotations["vault.hashicorp.com/agent-image"] == "openbao/openbao:2.5.3"

    @pytest.mark.asyncio()
    async def test_pod_spec_additions_omits_optional_annotations(self):
        adapter = OpenBaoAgentInjectionAdapter(
            openbao_url="https://openbao.ymir.niuu.world",
            namespace="skuld",
            mount_path="volundr",
            auth_path="auth/jwt",
            token="root-token",
            agent_image="",
            copy_volume_mounts_from="",
            inject_containers="",
        )

        result = await adapter.pod_spec_additions("alice", "session-123")

        assert "vault.hashicorp.com/namespace" not in result.annotations
        assert "vault.hashicorp.com/agent-image" not in result.annotations
        assert "vault.hashicorp.com/agent-copy-volume-mounts" not in result.annotations
        assert "vault.hashicorp.com/agent-inject-containers" not in result.annotations

    @pytest.mark.asyncio()
    async def test_ensure_secret_provider_class_creates_runtime_access(self, adapter):
        mappings = [
            CredentialMapping(
                credential_name="github",
                env_mappings={"GITHUB_TOKEN": "token"},
                file_mappings={"/home/volundr/.git-credentials": "token"},
            ),
        ]

        with (
            patch.object(adapter, "_ensure_service_account", new=AsyncMock()) as mock_sa,
            patch.object(adapter, "_create_or_update_configmap", new=AsyncMock()) as mock_cm,
            patch.object(
                adapter._admin,
                "ensure_service_account_access",
                new=AsyncMock(),
            ) as mock_access,
        ):
            await adapter.ensure_secret_provider_class(
                "alice",
                mappings,
                session_id="session-123",
                tenant_id="acme",
            )

        mock_sa.assert_awaited_once_with("openbao-session-session-123", "session-123", "alice")
        mock_access.assert_awaited_once_with(
            mount_path="volundr",
            user_id="alice",
            tenant_id="acme",
            auth_path="jwt-valhalla",
            audience="https://k8s-issuer.valhalla.asgard.niuu.world",
            service_account_namespace="skuld",
            service_account_name="openbao-session-session-123",
            policy_name="volundr-user-alice",
            role_name="volundr-session-session-123",
            ttl="1h",
        )
        mock_cm.assert_awaited_once()
        cm_kwargs = mock_cm.await_args.kwargs
        assert cm_kwargs["name"] == "openbao-agent-session-123"
        assert "config.hcl" in cm_kwargs["data"]
        assert "config-init.hcl" in cm_kwargs["data"]
        assert (
            cm_kwargs["annotations"]["volundr.niuu.io/openbao-role"]
            == "volundr-session-session-123"
        )

    @pytest.mark.asyncio()
    async def test_ensure_skips_without_mappings(self, adapter):
        with patch.object(adapter, "_ensure_service_account", new=AsyncMock()) as mock_sa:
            await adapter.ensure_secret_provider_class("alice", [], session_id="session-123")
        mock_sa.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_ensure_skips_without_session_id(self, adapter):
        mappings = [CredentialMapping(credential_name="github")]
        with patch.object(adapter, "_ensure_service_account", new=AsyncMock()) as mock_sa:
            await adapter.ensure_secret_provider_class("alice", mappings)
        mock_sa.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_provision_and_deprovision_user_are_noops(self, adapter):
        await adapter.provision_user("alice")
        await adapter.deprovision_user("alice")

    def test_build_configmap_data_uses_jwt_auto_auth_and_templates(self, adapter):
        data = adapter._build_configmap_data(
            user_id="alice",
            credential_mappings=[
                CredentialMapping(
                    credential_name="github",
                    env_mappings={"GITHUB_TOKEN": "token"},
                    file_mappings={"/home/volundr/.git-credentials": "token"},
                ),
            ],
            role_name="volundr-session-session-123",
        )

        assert "config.hcl" in data
        assert "config-init.hcl" in data
        config_hcl = data["config.hcl"]
        assert 'method "jwt"' in config_hcl
        assert 'mount_path = "auth/jwt-valhalla"' in config_hcl
        assert 'role = "volundr-session-session-123"' in config_hcl
        assert 'destination = "/run/secrets/env.sh"' in config_hcl
        assert 'destination = "/home/volundr/.git-credentials"' in config_hcl
        assert 'secret "volundr/data/users/alice/github"' in config_hcl

    def test_build_agent_config_includes_namespace_and_empty_templates(self, adapter):
        config_hcl = adapter._build_agent_config_hcl(
            user_id="alice",
            credential_mappings=[],
            role_name="volundr-session-session-123",
        )

        assert 'namespace = "apps"' in config_hcl
        assert "template {" not in config_hcl

    def test_helper_paths_and_names(self, adapter):
        assert adapter._auth_mount_path() == "auth/jwt-valhalla"
        assert (
            adapter._credential_path("alice", "github")
            == "volundr/data/users/alice/github"
        )
        assert adapter._sanitize_name("Session_ABC/123") == "session-abc-123"
        assert adapter._k8s_name("openbao/session", "A" * 80).startswith("openbao-session-")

    @pytest.mark.asyncio()
    async def test_cleanup_session_deletes_role_and_kubernetes_resources(self, adapter):
        with (
            patch.object(adapter._admin, "delete_jwt_role", new=AsyncMock()) as mock_role,
            patch.object(adapter, "_delete_configmap", new=AsyncMock()) as mock_cm,
            patch.object(adapter, "_delete_service_account", new=AsyncMock()) as mock_sa,
        ):
            await adapter.cleanup_session("session-123")

        mock_role.assert_awaited_once_with("volundr-session-session-123", auth_path="jwt-valhalla")
        mock_cm.assert_awaited_once_with("openbao-agent-session-123")
        mock_sa.assert_awaited_once_with("openbao-session-session-123")

    @pytest.mark.asyncio()
    async def test_cleanup_session_logs_role_delete_error(self, adapter):
        with (
            patch.object(
                adapter._admin,
                "delete_jwt_role",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(adapter, "_delete_configmap", new=AsyncMock()) as mock_cm,
            patch.object(adapter, "_delete_service_account", new=AsyncMock()) as mock_sa,
        ):
            await adapter.cleanup_session("session-123")

        mock_cm.assert_awaited_once()
        mock_sa.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_ensure_service_account_replaces_on_conflict(
        self, adapter, monkeypatch: pytest.MonkeyPatch
    ):
        _install_fake_kubernetes(monkeypatch)
        api_client = AsyncMock()
        core_api = AsyncMock()
        core_api.create_namespaced_service_account.side_effect = RuntimeError("409 AlreadyExists")
        adapter._core_api = AsyncMock(return_value=(api_client, core_api))  # type: ignore[method-assign]

        await adapter._ensure_service_account("sa-name", "session-1", "alice")

        core_api.replace_namespaced_service_account.assert_awaited_once()
        api_client.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_create_or_update_configmap_replaces_on_conflict(
        self, adapter, monkeypatch: pytest.MonkeyPatch
    ):
        _install_fake_kubernetes(monkeypatch)
        api_client = AsyncMock()
        core_api = AsyncMock()
        core_api.create_namespaced_config_map.side_effect = RuntimeError("409 AlreadyExists")
        adapter._core_api = AsyncMock(return_value=(api_client, core_api))  # type: ignore[method-assign]

        await adapter._create_or_update_configmap(
            name="cm-name",
            data={"config.hcl": "data"},
            labels={"app": "volundr"},
            annotations={"anno": "value"},
        )

        core_api.replace_namespaced_config_map.assert_awaited_once()
        api_client.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_delete_helpers_ignore_not_found(self, adapter):
        api_client = AsyncMock()
        core_api = AsyncMock()
        core_api.delete_namespaced_config_map.side_effect = RuntimeError("404 NotFound")
        core_api.delete_namespaced_service_account.side_effect = RuntimeError("404 NotFound")
        adapter._core_api = AsyncMock(return_value=(api_client, core_api))  # type: ignore[method-assign]

        await adapter._delete_configmap("cm-name")
        await adapter._delete_service_account("sa-name")

        assert api_client.close.await_count == 2

    @pytest.mark.asyncio()
    async def test_core_api_falls_back_to_kubeconfig(
        self,
        adapter,
        monkeypatch: pytest.MonkeyPatch,
    ):
        client_module, config_module = _install_fake_kubernetes(monkeypatch)
        config_module.load_incluster_config.side_effect = config_module.ConfigException("nope")

        api_client, core_api = await adapter._core_api()

        config_module.load_kube_config.assert_awaited_once()
        assert isinstance(api_client, client_module.ApiClient)
        assert isinstance(core_api, client_module.CoreV1Api)
