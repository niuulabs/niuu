"""Tests for the OpenBao injector-based secret injection adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from volundr.adapters.outbound.openbao_secret_injection import (
    OpenBaoAgentInjectionAdapter,
)
from volundr.domain.models import CredentialMapping


@pytest.fixture()
def adapter() -> OpenBaoAgentInjectionAdapter:
    return OpenBaoAgentInjectionAdapter(
        openbao_url="https://openbao.ymir.niuu.world",
        namespace="skuld",
        openbao_namespace="apps",
        mount_path="volundr",
        auth_path="jwt-valhalla",
        audience="https://kubernetes.default.svc.cluster.local",
        token="root-token",
        agent_image="openbao/openbao:2.5.3",
    )


class TestOpenBaoAgentInjectionAdapter:
    @pytest.mark.asyncio()
    async def test_pod_spec_additions_returns_service_account_and_annotations(self, adapter):
        result = await adapter.pod_spec_additions("alice", "session-123")

        assert result.service_account == "openbao-session-session-123"
        assert result.annotations["openbao.org/agent-inject"] == "true"
        assert result.annotations["openbao.org/agent-configmap"] == "openbao-agent-session-123"
        assert result.annotations["openbao.org/agent-pre-populate-only"] == "true"
        assert result.annotations["openbao.org/auth-type"] == "jwt"
        assert result.annotations["openbao.org/auth-path"] == "auth/jwt-valhalla"
        assert result.annotations["openbao.org/secret-volume-path"] == "/run/secrets"
        assert result.annotations["openbao.org/namespace"] == "apps"
        assert result.annotations["openbao.org/agent-image"] == "openbao/openbao:2.5.3"

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
            audience="https://kubernetes.default.svc.cluster.local",
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
        assert cm_kwargs["annotations"]["volundr.niuu.io/openbao-role"] == "volundr-session-session-123"

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
