"""Tests for the OpenBao admin/bootstrap client."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from volundr.adapters.outbound.openbao import (
    OpenBaoAdminClient,
    OpenBaoAdminConfig,
    OpenBaoApiError,
    OpenBaoJWTAuthConfig,
    OpenBaoJWTAuthRole,
)

BAO_URL = "https://bao.example.com"


@pytest.fixture
def config() -> OpenBaoAdminConfig:
    return OpenBaoAdminConfig(
        url=BAO_URL,
        token="root-token",
        namespace="platform",
    )


@pytest.fixture
def client(config: OpenBaoAdminConfig) -> OpenBaoAdminClient:
    http_client = httpx.AsyncClient(
        base_url=config.url,
        headers={"X-Vault-Namespace": config.namespace},
    )
    return OpenBaoAdminClient(config, client=http_client)


class TestEnsureKvV2Mount:
    @respx.mock
    async def test_skips_when_mount_already_exists(self, client: OpenBaoAdminClient):
        mounts = respx.get(f"{BAO_URL}/v1/sys/mounts").respond(
            status_code=200,
            json={"data": {"volundr/": {"type": "kv"}}},
        )
        create = respx.post(f"{BAO_URL}/v1/sys/mounts/volundr").respond(status_code=204)

        await client.ensure_kv_v2_mount("volundr")

        assert mounts.called
        assert not create.called

    @respx.mock
    async def test_creates_mount_when_missing(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/mounts").respond(status_code=200, json={"data": {}})
        create = respx.post(f"{BAO_URL}/v1/sys/mounts/volundr").respond(status_code=204)

        await client.ensure_kv_v2_mount("volundr", description="Volundr credentials")

        assert create.called
        body = json.loads(create.calls.last.request.content)
        assert body["type"] == "kv"
        assert body["options"] == {"version": "2"}


class TestEnsureJwtAuthBackend:
    @respx.mock
    async def test_creates_jwt_backend_when_missing(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/auth").respond(status_code=200, json={"data": {}})
        create = respx.post(f"{BAO_URL}/v1/sys/auth/jwt-workloads").respond(status_code=204)

        await client.ensure_jwt_auth_backend("jwt-workloads", description="Cluster JWT auth")

        assert create.called
        body = json.loads(create.calls.last.request.content)
        assert body["type"] == "jwt"


class TestConfigureJwtAuth:
    @respx.mock
    async def test_writes_auth_config(self, client: OpenBaoAdminClient):
        route = respx.post(f"{BAO_URL}/v1/auth/jwt-workloads/config").respond(status_code=204)

        await client.configure_jwt_auth(
            OpenBaoJWTAuthConfig(
                path="jwt-workloads",
                oidc_discovery_url="https://kubernetes.default/.well-known/openid-configuration",
                bound_issuer="https://kubernetes.default",
                default_role="volundr-default",
            )
        )

        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert body["oidc_discovery_url"].startswith("https://kubernetes.default")
        assert body["default_role"] == "volundr-default"


class TestEnsurePolicy:
    @respx.mock
    async def test_writes_policy(self, client: OpenBaoAdminClient):
        route = respx.put(f"{BAO_URL}/v1/sys/policy/volundr-user-u1").respond(status_code=204)

        await client.ensure_policy("volundr-user-u1", 'path "volundr/data/users/u1/*" {}')

        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert "volundr/data/users/u1" in body["policy"]


class TestEnsureJwtRole:
    @respx.mock
    async def test_writes_role(self, client: OpenBaoAdminClient):
        route = respx.post(f"{BAO_URL}/v1/auth/jwt/role/volundr-u1-role").respond(status_code=204)

        await client.ensure_jwt_role(
            OpenBaoJWTAuthRole(
                name="volundr-u1-role",
                auth_path="jwt",
                bound_audiences=("openbao",),
                bound_subject="system:serviceaccount:skuld:volundr-session-u1",
                policies=("volundr-user-u1",),
            )
        )

        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert body["bound_subject"] == "system:serviceaccount:skuld:volundr-session-u1"
        assert body["policies"] == ["volundr-user-u1"]

    @respx.mock
    async def test_delete_role_ignores_missing(self, client: OpenBaoAdminClient):
        route = respx.delete(f"{BAO_URL}/v1/auth/jwt/role/volundr-u1-role").respond(status_code=404)

        await client.delete_jwt_role("volundr-u1-role")

        assert route.called


class TestEnsureServiceAccountAccess:
    @respx.mock
    async def test_provisions_policy_and_role(self, client: OpenBaoAdminClient):
        policy = respx.put(f"{BAO_URL}/v1/sys/policy/volundr-user-alice").respond(status_code=204)
        role = respx.post(f"{BAO_URL}/v1/auth/jwt-workloads/role/volundr-alice-skuld-session-alice").respond(status_code=204)

        policy_name, role_name = await client.ensure_service_account_access(
            mount_path="volundr",
            user_id="alice",
            tenant_id="acme",
            auth_path="jwt-workloads",
            service_account_namespace="skuld",
            service_account_name="session-alice",
        )

        assert policy_name == "volundr-user-alice"
        assert role_name == "volundr-alice-skuld-session-alice"
        assert policy.called
        policy_body = json.loads(policy.calls.last.request.content)
        assert "volundr/data/users/alice/*" in policy_body["policy"]
        assert "volundr/data/tenants/acme/shared/*" in policy_body["policy"]
        assert role.called


class TestHelpers:
    def test_build_user_policy(self):
        policy = OpenBaoAdminClient.build_user_policy(
            mount_path="ting",
            user_id="alice",
            tenant_id="acme",
        )
        assert 'path "ting/data/users/alice/*"' in policy
        assert 'path "ting/metadata/users/alice"' in policy
        assert 'path "ting/data/tenants/acme/shared/*"' in policy

    def test_service_account_subject(self):
        assert (
            OpenBaoAdminClient.service_account_subject("skuld", "session-a")
            == "system:serviceaccount:skuld:session-a"
        )


class TestApiErrors:
    @respx.mock
    async def test_mount_list_error_raises(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/mounts").respond(status_code=500, text="boom")

        with pytest.raises(OpenBaoApiError) as exc_info:
            await client.ensure_kv_v2_mount("volundr")

        assert exc_info.value.status_code == 500
