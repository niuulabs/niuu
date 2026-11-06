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
    def test_jwt_auth_defaults_to_session_backend(self) -> None:
        adapter = OpenBaoAgentInjectionAdapter(
            openbao_url="https://openbao.example.com",
            auth_path="jwt-ymir",
            auth_method="jwt",
            jwt_role="volundr-app",
            jwt_token_file="/tmp/sa-token",
        )

        config = adapter._admin._config
        assert config.auth_method == "jwt"
        assert config.jwt_mount_path == "auth/jwt-ymir"
        assert config.jwt_role == "volundr-app"
        assert config.jwt_token_file == "/tmp/sa-token"

    def test_jwt_mount_path_override_wins(self) -> None:
        adapter = OpenBaoAgentInjectionAdapter(
            auth_path="jwt-ymir",
            auth_method="jwt",
            jwt_mount_path="auth/jwt-admin",
            jwt_role="volundr-app",
        )

        assert adapter._admin._config.jwt_mount_path == "auth/jwt-admin"

    @pytest.mark.asyncio()
    async def test_pod_spec_additions_returns_service_account_and_annotations(self, adapter):
        result = await adapter.pod_spec_additions("alice", "session-123")

        assert result.service_account == "openbao-session-session-123"
        assert result.annotations["vault.hashicorp.com/agent-inject"] == "true"
        assert (
            result.annotations["vault.hashicorp.com/agent-configmap"] == "openbao-agent-session-123"
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
            patch.object(adapter._admin, "ensure_policy", new=AsyncMock()) as mock_policy,
            patch.object(adapter._admin, "ensure_jwt_role", new=AsyncMock()) as mock_role,
        ):
            await adapter.ensure_secret_provider_class(
                "alice",
                mappings,
                session_id="session-123",
                tenant_id="acme",
            )

        mock_sa.assert_awaited_once_with("openbao-session-session-123", "session-123", "alice")
        mock_policy.assert_awaited_once_with(
            "volundr-session-session-123",
            'path "volundr/data/users/alice/github" {\n  capabilities = ["read"]\n}',
        )
        role = mock_role.await_args.args[0]
        assert role.policies == ("volundr-session-session-123",)
        assert role.bound_subject == "system:serviceaccount:skuld:openbao-session-session-123"
        assert role.auth_path == "jwt-valhalla"
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

    def test_session_policy_only_reads_mapped_paths(self, adapter):
        policy = adapter._session_policy(
            "alice",
            [
                CredentialMapping(credential_name="claude", env_mappings={"TOKEN": "token"}),
                CredentialMapping(credential_name="claude", file_mappings={"/token": "token"}),
                CredentialMapping(credential_name="unmapped"),
            ],
        )
        assert policy == 'path "volundr/data/users/alice/claude" {\n  capabilities = ["read"]\n}'

    @pytest.mark.parametrize("name", ["*", "+", "${user}"])
    def test_session_policy_rejects_nonliteral_paths(self, adapter, name):
        with pytest.raises(ValueError, match="literal"):
            adapter._session_policy(
                "alice", [CredentialMapping(credential_name=name, env_mappings={"TOKEN": "token"})]
            )

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
        assert 'path = "/home/vault/.vault-token"' in config_hcl
        assert 'perms = "0640"' in config_hcl
        assert 'destination = "/run/secrets/env.sh"' in config_hcl
        assert 'destination = "/home/volundr/.git-credentials"' in config_hcl
        assert 'secret "volundr/data/users/alice/github"' in config_hcl
        assert '{{ with secret "volundr/data/users/alice/github" }}' in config_hcl
        assert "{{ end }}" in config_hcl

    def test_build_configmap_data_separates_env_exports_with_newlines(self, adapter):
        data = adapter._build_configmap_data(
            user_id="alice",
            credential_mappings=[
                CredentialMapping(
                    credential_name="linear",
                    env_mappings={"LINEAR_API_KEY": "api_key"},
                ),
                CredentialMapping(
                    credential_name="github",
                    env_mappings={"GITHUB_TOKEN": "token"},
                ),
            ],
            role_name="volundr-session-session-123",
        )

        config_hcl = data["config.hcl"]
        env_block = config_hcl.split('destination = "/run/secrets/env.sh"', 1)[1]
        expected_linear = (
            "export LINEAR_API_KEY='{{ index .Data.data \"api_key\" }}'\n"
            "{{ end }}\n"
            '{{ with secret "volundr/data/users/alice/github" }}'
        )
        assert expected_linear in env_block
        assert "export GITHUB_TOKEN='{{ index .Data.data \"token\" }}'\n{{ end }}" in env_block

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
        assert adapter._credential_path("alice", "github") == "volundr/data/users/alice/github"
        assert adapter._sanitize_name("Session_ABC/123") == "session-abc-123"
        assert adapter._k8s_name("openbao/session", "A" * 80).startswith("openbao-session-")

    @pytest.mark.asyncio()
    async def test_cleanup_session_deletes_role_and_kubernetes_resources(self, adapter):
        with (
            patch.object(adapter._admin, "delete_policy", new=AsyncMock()) as mock_policy,
            patch.object(adapter._admin, "delete_jwt_role", new=AsyncMock()) as mock_role,
            patch.object(adapter, "_delete_configmap", new=AsyncMock()) as mock_cm,
            patch.object(adapter, "_delete_service_account", new=AsyncMock()) as mock_sa,
        ):
            await adapter.cleanup_session("session-123")

        mock_role.assert_awaited_once_with("volundr-session-session-123", auth_path="jwt-valhalla")
        mock_policy.assert_awaited_once_with("volundr-session-session-123")
        mock_cm.assert_awaited_once_with("openbao-agent-session-123")
        mock_sa.assert_awaited_once_with("openbao-session-session-123")

    @pytest.mark.asyncio()
    async def test_cleanup_session_logs_role_delete_error(self, adapter):
        with (
            patch.object(adapter._admin, "delete_policy", new=AsyncMock()),
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


def test_oauth_projection_uses_exact_engine_policy_and_continuous_agent():
    from niuu.domain.oauth_credentials import oauth_credential_name

    adapter = OpenBaoAgentInjectionAdapter(oauth_mount_path="oauthapp")
    mapping = CredentialMapping(
        credential_name="gitlab",
        oauth_tenant_id="tenant-a",
        oauth_token_field="token",
        file_mappings={"/run/secrets/mcp/token": "token"},
        oauth_token_documents=("/run/secrets/mcp/token",),
    )
    path = "oauthapp/creds/" + oauth_credential_name("tenant-a", "alice", "gitlab")
    policy = adapter._session_policy("alice", [mapping])
    assert policy == f'path "{path}" {{\n  capabilities = ["read"]\n}}'
    config = adapter._build_configmap_data(
        user_id="alice", credential_mappings=[mapping], role_name="session"
    )
    assert "exit_after_auth = true" in config["config-init.hcl"]
    assert "exit_after_auth = false" in config["config.hcl"]
    assert 'static_secret_render_interval = "30s"' in config["config.hcl"]
    assert path in config["config.hcl"]
    assert '"access_token" | toJSON' in config["config.hcl"]
    assert '"expire_time"' in config["config.hcl"]
    assert "refresh_token" not in config["config.hcl"]
    assert "client_secret" not in config["config.hcl"]
    assert ".Data.data" not in config["config.hcl"]


async def test_oauth_tenant_mismatch_has_no_side_effects():
    adapter = OpenBaoAgentInjectionAdapter(oauth_mount_path="oauthapp")
    adapter._ensure_service_account = AsyncMock()
    with pytest.raises(ValueError, match="tenant"):
        await adapter.ensure_secret_provider_class(
            "alice",
            [CredentialMapping(credential_name="token", oauth_tenant_id="other")],
            session_id="session",
            tenant_id="tenant-a",
        )
    adapter._ensure_service_account.assert_not_called()


def test_oauth_projection_rejects_refresh_token_field():
    adapter = OpenBaoAgentInjectionAdapter(oauth_mount_path="oauthapp")
    with pytest.raises(ValueError, match="only access token"):
        adapter._build_template_blocks(
            "alice",
            [
                CredentialMapping(
                    credential_name="token",
                    oauth_tenant_id="tenant-a",
                    oauth_token_field="token",
                    file_mappings={"/run/secrets/refresh": "refresh_token"},
                )
            ],
        )


class TestWorkloadInjection:
    """Residents: credentials bound to the workload's own service account."""

    _MAPPINGS = [
        CredentialMapping(
            credential_name="xai-key",
            file_mappings={"/run/secrets/xai-api-key": "api_key"},
        )
    ]

    async def test_binds_the_role_to_the_workloads_own_service_account(self, adapter):
        with (
            patch.object(adapter, "_ensure_service_account", new=AsyncMock()) as mock_sa,
            patch.object(adapter, "_create_or_update_configmap", new=AsyncMock()) as mock_cm,
            patch.object(adapter._admin, "ensure_policy", new=AsyncMock()) as mock_policy,
            patch.object(adapter._admin, "ensure_jwt_role", new=AsyncMock()) as mock_role,
        ):
            additions = await adapter.workload_pod_spec_additions(
                "alice",
                self._MAPPINGS,
                workload_id="res-1",
                service_account_name="resident-res-1",
                inject_containers="ravn",
                tenant_id="acme",
            )

        mock_sa.assert_not_awaited()
        mock_policy.assert_awaited_once_with(
            "volundr-workload-res-1",
            'path "volundr/data/users/alice/xai-key" {\n  capabilities = ["read"]\n}',
        )
        role = mock_role.await_args.args[0]
        assert role.bound_subject == "system:serviceaccount:skuld:resident-res-1"
        assert role.policies == ("volundr-workload-res-1",)
        config = mock_cm.await_args.kwargs
        assert config["name"] == "openbao-agent-workload-res-1"
        assert 'destination = "/run/secrets/xai-api-key"' in config["data"]["config.hcl"]
        assert config["labels"]["volundr.niuu.io/workload-id"] == "res-1"
        assert additions.service_account is None
        assert additions.annotations["vault.hashicorp.com/agent-inject-containers"] == "ravn"
        assert additions.annotations["vault.hashicorp.com/role"] == "volundr-workload-res-1"
        assert (
            additions.annotations["vault.hashicorp.com/agent-configmap"]
            == "openbao-agent-workload-res-1"
        )
        assert additions.annotations["vault.hashicorp.com/agent-pre-populate-only"] == "true"
        assert adapter.supports_workloads

    async def test_gives_the_agent_a_service_account_token_where_its_config_reads_it(self, adapter):
        # A resident's own ServiceAccount automounts no token, and the injector refuses
        # a pod with no ServiceAccount token mount.
        with (
            patch.object(adapter, "_create_or_update_configmap", new=AsyncMock()) as mock_cm,
            patch.object(adapter._admin, "ensure_policy", new=AsyncMock()),
            patch.object(adapter._admin, "ensure_jwt_role", new=AsyncMock()) as mock_role,
        ):
            additions = await adapter.workload_pod_spec_additions(
                "alice",
                self._MAPPINGS,
                workload_id="res-1",
                service_account_name="resident-res-1",
                inject_containers="ravn",
            )

        [volume] = additions.volumes
        [source] = volume["projected"]["sources"]
        assert source["serviceAccountToken"] == {
            "path": "token",
            "audience": "https://k8s-issuer.valhalla.asgard.niuu.world",
            "expirationSeconds": 3600,
        }
        assert mock_role.await_args.args[0].bound_audiences == (
            "https://k8s-issuer.valhalla.asgard.niuu.world",
        )
        [mount] = additions.volume_mounts
        assert mount == {
            "name": volume["name"],
            "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
            "readOnly": True,
        }
        config = mock_cm.await_args.kwargs["data"]["config.hcl"]
        assert 'path = "/var/run/secrets/kubernetes.io/serviceaccount/token"' in config

    def test_workload_token_lifetime_is_configurable_within_kubernetes_bounds(self):
        adapter = OpenBaoAgentInjectionAdapter(workload_token_expiration_seconds=900)
        assert adapter._workload_token_expiration == 900
        with pytest.raises(ValueError, match="at least 600"):
            OpenBaoAgentInjectionAdapter(workload_token_expiration_seconds=599)

    @pytest.mark.parametrize(
        ("mappings", "service_account", "message"),
        [
            ([], "resident-res-1", "at least one credential"),
            (_MAPPINGS, "", "service account"),
            (
                [CredentialMapping(credential_name="x", oauth_tenant_id="acme")],
                "resident-res-1",
                "static credentials only",
            ),
        ],
    )
    async def test_refuses_what_it_cannot_inject(self, adapter, mappings, service_account, message):
        with pytest.raises(ValueError, match=message):
            await adapter.workload_pod_spec_additions(
                "alice",
                mappings,
                workload_id="res-1",
                service_account_name=service_account,
                inject_containers="ravn",
            )

    async def test_cleanup_workload_keeps_the_service_account(self, adapter):
        with (
            patch.object(adapter._admin, "delete_policy", new=AsyncMock()) as mock_policy,
            patch.object(adapter._admin, "delete_jwt_role", new=AsyncMock()) as mock_role,
            patch.object(adapter, "_delete_configmap", new=AsyncMock()) as mock_cm,
            patch.object(adapter, "_delete_service_account", new=AsyncMock()) as mock_sa,
        ):
            await adapter.cleanup_workload("res-1")

        mock_role.assert_awaited_once_with("volundr-workload-res-1", auth_path="jwt-valhalla")
        mock_policy.assert_awaited_once_with("volundr-workload-res-1")
        mock_cm.assert_awaited_once_with("openbao-agent-workload-res-1")
        mock_sa.assert_not_awaited()


async def test_adapters_without_workload_support_say_so():
    from volundr.adapters.outbound.memory_secret_injection import InMemorySecretInjectionAdapter

    adapter = InMemorySecretInjectionAdapter()

    assert adapter.supports_workloads is False
    with pytest.raises(NotImplementedError, match="resident workload"):
        await adapter.workload_pod_spec_additions(
            "alice", [], workload_id="w", service_account_name="sa", inject_containers="ravn"
        )
    with pytest.raises(NotImplementedError, match="resident workload"):
        await adapter.cleanup_workload("w")


class TestGuestAgentInjection:
    """VM sessions run the OpenBao agent as explicit containers, not injector annotations."""

    _FILE_MAPPING = CredentialMapping(
        credential_name="git",
        env_mappings={"GIT_TOKEN": "token"},
        file_mappings={"/run/secrets/git/token": "token"},
    )

    @staticmethod
    def _adapter(**kwargs) -> OpenBaoAgentInjectionAdapter:
        return OpenBaoAgentInjectionAdapter(
            openbao_url="https://openbao.example.com",
            auth_path="jwt-valhalla",
            audience="https://k8s.example.com",
            workload_token_expiration_seconds=1800,
            guest_agent_image="openbao/openbao:2.5.3",
            **kwargs,
        )

    def test_port_default_has_no_guest_agents(self):
        from volundr.domain.ports import SecretInjectionPort

        assert SecretInjectionPort.supports_guest_agents.fget(object()) is False

    @pytest.mark.asyncio()
    async def test_port_default_names_the_adapter(self):
        from volundr.domain.ports import SecretInjectionPort

        with pytest.raises(NotImplementedError, match="OpenBaoAgentInjectionAdapter"):
            await SecretInjectionPort.guest_agent_pod_spec_additions(
                self._adapter(), "alice", "s1", []
            )

    def test_adapter_supports_guest_agents(self):
        assert self._adapter().supports_guest_agents is True

    @pytest.mark.asyncio()
    async def test_produces_init_container_and_volumes(self):
        adapter = self._adapter()
        result = await adapter.guest_agent_pod_spec_additions(
            "alice", "session-123", [self._FILE_MAPPING]
        )

        assert result.service_account == "openbao-session-session-123"
        assert result.annotations == {}
        assert result.volumes == (
            {"name": "openbao-secrets", "emptyDir": {}},
            {"name": "openbao-agent-home", "emptyDir": {}},
            {
                "name": "openbao-agent-sa-token",
                "projected": {
                    "sources": [
                        {
                            "serviceAccountToken": {
                                "path": "token",
                                "audience": "https://k8s.example.com",
                                "expirationSeconds": 1800,
                            }
                        }
                    ]
                },
            },
        )
        assert result.volume_mounts == (
            {"name": "openbao-secrets", "mountPath": "/run/secrets", "readOnly": True},
        )
        assert result.extra_containers == ()

        (init,) = result.init_containers
        assert init["name"] == "openbao-agent-init"
        assert init["image"] == "openbao/openbao:2.5.3"
        assert init["command"] == [
            "/bin/sh",
            "-ec",
            'printf "%s" "$BAO_AGENT_CONFIG" > /home/vault/config.hcl'
            " && exec bao agent -config=/home/vault/config.hcl",
        ]
        assert init["volumeMounts"] == [
            {"name": "openbao-secrets", "mountPath": "/run/secrets"},
            {"name": "openbao-agent-home", "mountPath": "/home/vault"},
            {
                "name": "openbao-agent-sa-token",
                "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
                "readOnly": True,
            },
        ]
        assert init["securityContext"] == {"runAsUser": 100, "runAsGroup": 1000}
        (env,) = init["env"]
        assert env["name"] == "BAO_AGENT_CONFIG"
        assert "exit_after_auth = true" in env["value"]
        assert 'role = "volundr-session-session-123"' in env["value"]
        assert 'destination = "/run/secrets/git/token"' in env["value"]
        assert 'destination = "/run/secrets/env.sh"' in env["value"]

    @pytest.mark.asyncio()
    async def test_token_sink_stays_in_agent_home_not_the_session_volume(self):
        result = await self._adapter().guest_agent_pod_spec_additions(
            "alice", "s1", [self._FILE_MAPPING]
        )

        config = result.init_containers[0]["env"][0]["value"]
        assert 'path = "/home/vault/.vault-token"' in config
        assert "/run/secrets/.vault-token" not in config
        session_mounts = [m["name"] for m in result.volume_mounts]
        assert "openbao-agent-home" not in session_mounts
        assert "openbao-agent-sa-token" not in session_mounts

    @pytest.mark.asyncio()
    async def test_uses_configured_binary_user_and_group(self):
        adapter = self._adapter(
            guest_agent_binary="/usr/bin/bao", guest_agent_user=200, guest_agent_group=300
        )
        result = await adapter.guest_agent_pod_spec_additions("alice", "s1", [self._FILE_MAPPING])

        init = result.init_containers[0]
        assert init["command"][2].endswith("exec /usr/bin/bao agent -config=/home/vault/config.hcl")
        assert init["securityContext"] == {"runAsUser": 200, "runAsGroup": 300}

    @pytest.mark.asyncio()
    async def test_sidecar_only_with_managed_oauth(self):
        mapping = CredentialMapping(
            credential_name="gitlab",
            oauth_tenant_id="tenant-a",
            oauth_token_field="token",
            file_mappings={"/run/secrets/mcp/token": "token"},
            oauth_token_documents=("/run/secrets/mcp/token",),
        )
        adapter = self._adapter(oauth_mount_path="oauthapp")
        result = await adapter.guest_agent_pod_spec_additions("alice", "s1", [mapping])

        (init,) = result.init_containers
        (sidecar,) = result.extra_containers
        assert init["name"] == "openbao-agent-init"
        assert "exit_after_auth = true" in init["env"][0]["value"]
        assert sidecar["name"] == "openbao-agent"
        assert sidecar["image"] == init["image"]
        assert sidecar["command"] == init["command"]
        assert sidecar["volumeMounts"] == init["volumeMounts"]
        assert sidecar["securityContext"] == init["securityContext"]
        assert "exit_after_auth = false" in sidecar["env"][0]["value"]
        assert 'static_secret_render_interval = "30s"' in sidecar["env"][0]["value"]

    @pytest.mark.asyncio()
    async def test_missing_image_is_refused_naming_the_knob(self):
        adapter = OpenBaoAgentInjectionAdapter(agent_image="openbao/openbao:2.5.3")

        with pytest.raises(ValueError, match="guest_agent_image"):
            await adapter.guest_agent_pod_spec_additions("alice", "s1", [self._FILE_MAPPING])

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "destination",
        [
            "/home/volundr/.ssh/id_rsa",
            "/run/secretsx/token",
            "/run/secrets/../etc/token",
            "/run/secrets",
            "run/secrets/token",
        ],
    )
    async def test_destination_outside_secrets_dir_is_refused(self, destination):
        mapping = CredentialMapping(credential_name="ssh", file_mappings={destination: "key"})

        with pytest.raises(ValueError, match="VM sessions") as excinfo:
            await self._adapter().guest_agent_pod_spec_additions("alice", "s1", [mapping])

        assert destination in str(excinfo.value)
        assert "/run/secrets" in str(excinfo.value)

    @pytest.mark.parametrize(
        "kwargs",
        [{"guest_agent_binary": ""}, {"guest_agent_user": -1}, {"guest_agent_group": -1}],
    )
    def test_invalid_guest_agent_knobs_are_refused(self, kwargs):
        with pytest.raises(ValueError, match="guest_agent"):
            self._adapter(**kwargs)
