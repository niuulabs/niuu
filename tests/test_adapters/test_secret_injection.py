"""Tests for SecretInjectionPort adapters and PodSpecAdditions model."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from volundr.adapters.outbound.infisical_secret_injection import (
    InfisicalAgentInjectionAdapter,
)
from volundr.adapters.outbound.memory_secret_injection import (
    InMemorySecretInjectionAdapter,
)
from volundr.domain.models import CredentialMapping, PodSpecAdditions

# ------------------------------------------------------------------
# PodSpecAdditions model
# ------------------------------------------------------------------


class TestPodSpecAdditions:
    def test_defaults(self):
        pa = PodSpecAdditions()
        assert pa.volumes == ()
        assert pa.volume_mounts == ()
        assert pa.labels == {}
        assert pa.annotations == {}
        assert pa.env == ()
        assert pa.service_account is None

    def test_frozen(self):
        pa = PodSpecAdditions()
        with pytest.raises(AttributeError):
            pa.service_account = "new-sa"  # type: ignore[misc]

    def test_with_values(self):
        pa = PodSpecAdditions(
            volumes=({"name": "v1"},),
            volume_mounts=({"name": "v1", "mountPath": "/mnt"},),
            labels={"app": "test"},
            annotations={"note": "value"},
            env=({"name": "FOO", "value": "bar"},),
            service_account="my-sa",
        )
        assert len(pa.volumes) == 1
        assert pa.labels == {"app": "test"}
        assert pa.service_account == "my-sa"

    def test_post_init_converts_empty_tuple_defaults_to_dicts(self):
        pa = PodSpecAdditions()
        assert isinstance(pa.labels, dict)
        assert isinstance(pa.annotations, dict)


# ------------------------------------------------------------------
# CredentialMapping model
# ------------------------------------------------------------------


class TestCredentialMapping:
    def test_defaults(self):
        m = CredentialMapping(credential_name="my-cred")
        assert m.credential_name == "my-cred"
        assert m.env_mappings == {}
        assert m.file_mappings == {}

    def test_with_mappings(self):
        m = CredentialMapping(
            credential_name="openai-cred",
            env_mappings={"OPENAI_API_KEY": "api_key"},
            file_mappings={"/etc/config": "config_file"},
        )
        assert m.env_mappings == {"OPENAI_API_KEY": "api_key"}
        assert m.file_mappings == {"/etc/config": "config_file"}

    def test_frozen(self):
        m = CredentialMapping(credential_name="x")
        with pytest.raises(AttributeError):
            m.credential_name = "y"  # type: ignore[misc]


# ------------------------------------------------------------------
# InMemorySecretInjectionAdapter
# ------------------------------------------------------------------


class TestInMemorySecretInjectionAdapter:
    @pytest.fixture()
    def adapter(self):
        return InMemorySecretInjectionAdapter()

    @pytest.mark.asyncio()
    async def test_pod_spec_additions_returns_empty(self, adapter):
        result = await adapter.pod_spec_additions("user-1", "session-1")
        assert isinstance(result, PodSpecAdditions)
        assert result.volumes == ()

    @pytest.mark.asyncio()
    async def test_provision_user(self, adapter):
        await adapter.provision_user("alice")
        assert "alice" in adapter._provisioned_users

    @pytest.mark.asyncio()
    async def test_deprovision_user(self, adapter):
        await adapter.provision_user("alice")
        await adapter.deprovision_user("alice")
        assert "alice" not in adapter._provisioned_users

    @pytest.mark.asyncio()
    async def test_accepts_extra_kwargs(self):
        adapter = InMemorySecretInjectionAdapter(foo="bar", baz=42)
        result = await adapter.pod_spec_additions("u1", "s1")
        assert isinstance(result, PodSpecAdditions)


# ------------------------------------------------------------------
# InfisicalAgentInjectionAdapter
# ------------------------------------------------------------------


class TestInfisicalAgentInjectionAdapter:
    @pytest.fixture()
    def adapter(self):
        return InfisicalAgentInjectionAdapter(
            infisical_url="https://infisical.test",
            client_id="test-client-id",
            client_secret="test-client-secret",
            namespace="test-ns",
            org_id="org-123",
            credential_project_id="proj-123",
            environment="dev",
            token_ttl_seconds=300,
        )

    def test_the_injector_works_on_the_pod_not_the_session(self, adapter):
        assert adapter.injects_per_pod is True

    @pytest.mark.asyncio()
    async def test_pod_spec_additions_returns_annotations(self, adapter):
        result = await adapter.pod_spec_additions("alice", "s-123")

        assert result.annotations["org.infisical.com/inject"] == "true"
        assert result.annotations["org.infisical.com/inject-mode"] == "init"
        assert result.annotations["org.infisical.com/agent-config-map"] == "infisical-agent-s-123"
        assert result.annotations["org.infisical.com/agent-revoke-on-shutdown"] == "true"
        assert result.annotations["org.infisical.com/agent-set-security-context"] == "true"
        assert result.volumes == ()
        assert result.service_account is None

    @pytest.mark.asyncio()
    async def test_ensure_creates_identity_and_configmap(self, adapter):
        """ensure_secret_provider_class creates an identity and ConfigMap."""
        mappings = [
            CredentialMapping(
                credential_name="openai-cred",
                env_mappings={"OPENAI_API_KEY": "api_key"},
            ),
        ]
        with (
            patch.object(
                adapter,
                "_create_session_identity",
                return_value="ident-1",
            ) as mock_identity,
            patch.object(adapter, "_create_or_update_configmap") as mock_cm,
        ):
            await adapter.ensure_secret_provider_class(
                "alice",
                mappings,
                session_id="s-123",
            )

            mock_identity.assert_called_once_with("alice", "s-123")
            mock_cm.assert_called_once()
            call_kwargs = mock_cm.call_args[1]
            assert call_kwargs["name"] == "infisical-agent-s-123"
            assert "config.yaml" in call_kwargs["data"]
            assert call_kwargs["labels"]["volundr.niuu.io/session-id"] == "s-123"
            assert call_kwargs["annotations"]["volundr.niuu.io/identity-id"] == "ident-1"

    @pytest.mark.asyncio()
    async def test_ensure_skips_when_no_mappings(self, adapter):
        with patch.object(adapter, "_create_session_identity") as mock_ident:
            await adapter.ensure_secret_provider_class("alice", [], session_id="s-1")
            mock_ident.assert_not_called()

    @pytest.mark.asyncio()
    async def test_ensure_skips_when_no_session_id(self, adapter):
        mappings = [CredentialMapping(credential_name="cred")]
        with patch.object(adapter, "_create_session_identity") as mock_ident:
            await adapter.ensure_secret_provider_class("alice", mappings)
            mock_ident.assert_not_called()

    @pytest.mark.asyncio()
    async def test_build_configmap_uses_kubernetes_auth(self, adapter):
        """Config uses kubernetes auth with identity ID."""
        import yaml

        mappings = [
            CredentialMapping(
                credential_name="openai-cred",
                env_mappings={"OPENAI_API_KEY": "api_key"},
            ),
        ]
        data = adapter._build_configmap_data(
            user_id="alice",
            credential_mappings=mappings,
            identity_id="ident-1",
        )

        config = yaml.safe_load(data["config.yaml"])
        assert config["infisical"]["auth"]["type"] == "kubernetes"
        assert config["infisical"]["auth"]["config"]["identity-id"] == "ident-1"

    @pytest.mark.asyncio()
    async def test_build_configmap_env_mapping(self, adapter):
        """Env mappings render as export lines in env.sh."""
        import yaml

        mappings = [
            CredentialMapping(
                credential_name="openai-cred",
                env_mappings={"OPENAI_API_KEY": "api_key", "OPENAI_ORG": "org_id"},
            ),
        ]
        data = adapter._build_configmap_data(
            user_id="alice",
            credential_mappings=mappings,
            identity_id="ident-1",
        )

        config = yaml.safe_load(data["config.yaml"])
        env_templates = [
            t for t in config["templates"] if t["destination-path"] == "/run/secrets/env.sh"
        ]
        assert len(env_templates) == 1
        content = env_templates[0]["template-content"]
        assert "export OPENAI_API_KEY=" in content
        assert "export OPENAI_ORG=" in content
        assert 'getSecretByName "proj-123" "dev" "/users/alice/openai-cred" "api_key"' in content

    @pytest.mark.asyncio()
    async def test_build_configmap_file_mapping(self, adapter):
        """File mappings render as individual templates at target paths."""
        import yaml

        mappings = [
            CredentialMapping(
                credential_name="ssh-key",
                file_mappings={"/home/dev/.ssh/id_rsa": "private_key"},
            ),
        ]
        data = adapter._build_configmap_data(
            user_id="alice",
            credential_mappings=mappings,
            identity_id="ident-1",
        )

        config = yaml.safe_load(data["config.yaml"])
        file_templates = [
            t for t in config["templates"] if t["destination-path"] == "/home/dev/.ssh/id_rsa"
        ]
        assert len(file_templates) == 1
        content = file_templates[0]["template-content"]
        assert 'getSecretByName "proj-123" "dev" "/users/alice/ssh-key" "private_key"' in content

    @pytest.mark.asyncio()
    async def test_build_configmap_unmapped_credential(self, adapter):
        """Unmapped credentials produce no templates (nothing to render)."""
        import yaml

        mappings = [
            CredentialMapping(credential_name="generic-cred"),
        ]
        data = adapter._build_configmap_data(
            user_id="alice",
            credential_mappings=mappings,
            identity_id="ident-1",
        )

        config = yaml.safe_load(data["config.yaml"])
        assert len(config.get("templates", [])) == 0

    @pytest.mark.asyncio()
    async def test_build_configmap_mixed_mappings(self, adapter):
        """Multiple credentials with different mapping types."""
        import yaml

        mappings = [
            CredentialMapping(
                credential_name="openai",
                env_mappings={"OPENAI_API_KEY": "api_key"},
            ),
            CredentialMapping(
                credential_name="ssh",
                file_mappings={"/root/.ssh/id_rsa": "private_key"},
            ),
            CredentialMapping(credential_name="raw-cred"),
        ]
        data = adapter._build_configmap_data(
            user_id="alice",
            credential_mappings=mappings,
            identity_id="ident-1",
        )

        config = yaml.safe_load(data["config.yaml"])
        # env.sh + ssh file = 2 templates (unmapped raw-cred is skipped)
        assert len(config["templates"]) == 2

    @pytest.mark.asyncio()
    async def test_create_session_identity(self, adapter):
        """_create_session_identity makes 4 API calls in sequence."""
        responses = [
            # 1. Create identity
            _mock_response(200, {"identity": {"id": "ident-1"}}),
            # 2. Attach Kubernetes Auth
            _mock_response(200, {"identityKubernetesAuth": {"id": "ka-1"}}),
            # 3. Add identity to project
            _mock_response(200, {"identityMembership": {"id": "mem-1"}}),
            # 4. Add privilege
            _mock_response(200, {"privilege": {"id": "priv-1"}}),
        ]

        with (
            patch.object(adapter, "_ensure_authenticated", return_value="mgmt-token"),
            patch.object(adapter, "_get_client") as mock_get_client,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=responses)
            mock_get_client.return_value = mock_client

            identity_id = await adapter._create_session_identity("alice", "s-123")

            assert identity_id == "ident-1"
            assert mock_client.post.call_count == 4

            # Verify identity creation call
            create_call = mock_client.post.call_args_list[0]
            assert create_call[0][0] == "/api/v1/identities"
            assert create_call[1]["json"]["name"] == "session-s-123"
            assert create_call[1]["json"]["organizationId"] == "org-123"

            # Verify Kubernetes Auth call
            k8s_auth_call = mock_client.post.call_args_list[1]
            assert "/kubernetes-auth/identities/ident-1" in k8s_auth_call[0][0]
            assert k8s_auth_call[1]["json"]["allowedNamespaces"] == "test-ns"

            # Verify project membership call
            membership_call = mock_client.post.call_args_list[2]
            assert "/memberships/identities/ident-1" in membership_call[0][0]
            assert membership_call[1]["json"]["role"] == "no-access"

            # Verify privilege call
            priv_call = mock_client.post.call_args_list[3]
            assert priv_call[0][0] == "/api/v2/identity-project-additional-privilege"
            perms = priv_call[1]["json"]["permissions"]
            assert perms[0]["conditions"]["secretPath"]["$glob"] == "/users/alice/**"

    @pytest.mark.asyncio()
    async def test_create_session_identity_cleans_up_on_auth_failure(self, adapter):
        """If attaching Kubernetes Auth fails, the identity is deleted."""
        responses = [
            _mock_response(200, {"identity": {"id": "ident-1"}}),
            _mock_response(403, text="forbidden"),
        ]

        with (
            patch.object(adapter, "_ensure_authenticated", return_value="mgmt-token"),
            patch.object(adapter, "_get_client") as mock_get_client,
            patch.object(adapter, "_delete_identity") as mock_delete,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=responses)
            mock_get_client.return_value = mock_client

            with pytest.raises(RuntimeError, match="Failed to attach Kubernetes Auth"):
                await adapter._create_session_identity("alice", "s-123")

            mock_delete.assert_called_once_with("ident-1")

    @pytest.mark.asyncio()
    async def test_provision_user_is_noop(self, adapter):
        await adapter.provision_user("alice")  # should not raise

    @pytest.mark.asyncio()
    async def test_deprovision_user_is_noop(self, adapter):
        await adapter.deprovision_user("alice")  # should not raise

    @pytest.mark.asyncio()
    async def test_accepts_extra_kwargs(self):
        adapter = InfisicalAgentInjectionAdapter(
            infisical_url="https://test.example.com",
            extra_param="ignored",
        )
        result = await adapter.pod_spec_additions("u1", "s1")
        assert result.annotations["org.infisical.com/inject"] == "true"


def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or str(json_data)
    if json_data is not None:
        resp.json.return_value = json_data
    return resp
