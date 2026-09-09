"""Tests for the OpenBao admin/bootstrap client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

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
    async def test_skips_existing_backend(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/auth").respond(status_code=200, json={"data": {"jwt/": {}}})
        create = respx.post(f"{BAO_URL}/v1/sys/auth/jwt").respond(status_code=204)

        await client.ensure_jwt_auth_backend("jwt")

        assert not create.called

    @respx.mock
    async def test_creates_jwt_backend_when_missing(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/auth").respond(status_code=200, json={"data": {}})
        create = respx.post(f"{BAO_URL}/v1/sys/auth/jwt-workloads").respond(status_code=204)

        await client.ensure_jwt_auth_backend("jwt-workloads", description="Cluster JWT auth")

        assert create.called
        body = json.loads(create.calls.last.request.content)
        assert body["type"] == "jwt"


class TestConfigureJwtAuth:
    def test_payload_includes_optional_fields(self):
        payload = OpenBaoJWTAuthConfig(
            oidc_discovery_url="https://issuer/.well-known/openid-configuration",
            bound_issuer="https://issuer",
            default_role="default-role",
            oidc_discovery_ca_pem="pem-data",
        ).payload()

        assert payload["bound_issuer"] == "https://issuer"
        assert payload["default_role"] == "default-role"
        assert payload["oidc_discovery_ca_pem"] == "pem-data"

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
        assert (
            body["oidc_discovery_url"]
            == "https://kubernetes.default/.well-known/openid-configuration"
        )
        assert body["default_role"] == "volundr-default"

    @respx.mock
    async def test_configure_auth_raises_on_error(self, client: OpenBaoAdminClient):
        respx.post(f"{BAO_URL}/v1/auth/jwt/config").respond(status_code=500, text="boom")

        with pytest.raises(OpenBaoApiError):
            await client.configure_jwt_auth(
                OpenBaoJWTAuthConfig(oidc_discovery_url="https://issuer")
            )


class TestEnsurePolicy:
    @respx.mock
    async def test_writes_policy(self, client: OpenBaoAdminClient):
        route = respx.put(f"{BAO_URL}/v1/sys/policy/volundr-user-u1").respond(status_code=204)

        await client.ensure_policy("volundr-user-u1", 'path "volundr/data/users/u1/*" {}')

        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert "volundr/data/users/u1" in body["policy"]

    @respx.mock
    async def test_policy_raises_on_error(self, client: OpenBaoAdminClient):
        respx.put(f"{BAO_URL}/v1/sys/policy/volundr-user-u1").respond(status_code=500, text="boom")

        with pytest.raises(OpenBaoApiError):
            await client.ensure_policy("volundr-user-u1", 'path "volundr/data/users/u1/*" {}')


class TestEnsureJwtRole:
    def test_role_payload_includes_optional_claims(self):
        payload = OpenBaoJWTAuthRole(
            name="role",
            bound_subject="system:serviceaccount:skuld:session-a",
            policies=("volundr-user-a",),
            bound_claims={"kubernetes.io/serviceaccount/namespace": "skuld"},
        ).payload()

        assert payload["bound_subject"] == "system:serviceaccount:skuld:session-a"
        assert payload["bound_claims"] == {"kubernetes.io/serviceaccount/namespace": "skuld"}

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

    @respx.mock
    async def test_delete_role_raises_on_other_errors(self, client: OpenBaoAdminClient):
        respx.delete(f"{BAO_URL}/v1/auth/jwt/role/volundr-u1-role").respond(
            status_code=500,
            text="boom",
        )

        with pytest.raises(OpenBaoApiError):
            await client.delete_jwt_role("volundr-u1-role")


class TestEnsureServiceAccountAccess:
    @respx.mock
    async def test_provisions_policy_and_role(self, client: OpenBaoAdminClient):
        policy = respx.put(f"{BAO_URL}/v1/sys/policy/volundr-user-alice").respond(status_code=204)
        role = respx.post(
            f"{BAO_URL}/v1/auth/jwt-workloads/role/volundr-alice-skuld-session-alice"
        ).respond(status_code=204)

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

    def test_names_are_sanitized(self):
        assert (
            OpenBaoAdminClient.user_policy_name("team/volundr", "alice")
            == "team-volundr-user-alice"
        )
        assert (
            OpenBaoAdminClient.service_account_role_name(
                "team/volundr",
                "alice",
                "skuld/system",
                "session/main",
            )
            == "team-volundr-alice-skuld-system-session-main"
        )


class TestAuthAndLifecycle:
    @respx.mock
    async def test_approle_auth_flow_and_close(self):
        config = OpenBaoAdminConfig(
            url=BAO_URL,
            namespace="platform",
            auth_method="approle",
            approle_mount_path="auth/team-approle",
            role_id="role-id",
            secret_id="secret-id",
        )
        client = OpenBaoAdminClient(config)
        route = respx.post(f"{BAO_URL}/v1/auth/team-approle/login").respond(
            status_code=200,
            json={"auth": {"client_token": "bao-token"}},
        )

        headers = await client._headers()

        assert headers == {
            "X-Vault-Token": "bao-token",
            "X-Vault-Namespace": "platform",
        }
        assert route.called

        await client.close()
        assert client._client is None

    async def test_approle_requires_credentials(self):
        client = OpenBaoAdminClient(OpenBaoAdminConfig(auth_method="approle"))

        with pytest.raises(RuntimeError, match="requires role_id and secret_id"):
            await client._ensure_authenticated()

    @respx.mock
    async def test_approle_auth_failure_raises(self):
        client = OpenBaoAdminClient(
            OpenBaoAdminConfig(
                url=BAO_URL,
                auth_method="approle",
                role_id="role-id",
                secret_id="secret-id",
            )
        )
        respx.post(f"{BAO_URL}/v1/auth/approle/login").respond(status_code=403, text="denied")

        with pytest.raises(OpenBaoApiError):
            await client._ensure_authenticated()

    @respx.mock
    async def test_jwt_auth_flow(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("sa-jwt\n")
        client = OpenBaoAdminClient(
            OpenBaoAdminConfig(
                url=BAO_URL,
                auth_method="jwt",
                jwt_mount_path="auth/jwt-ymir",
                jwt_role="volundr-app",
                jwt_token_file=str(token_file),
            )
        )
        route = respx.post(f"{BAO_URL}/v1/auth/jwt-ymir/login").respond(
            status_code=200,
            json={"auth": {"client_token": "bao-token"}},
        )

        headers = await client._headers()

        assert headers == {"X-Vault-Token": "bao-token"}
        assert json.loads(route.calls.last.request.content) == {
            "role": "volundr-app",
            "jwt": "sa-jwt",
        }

    async def test_jwt_requires_role(self):
        client = OpenBaoAdminClient(OpenBaoAdminConfig(auth_method="jwt"))

        with pytest.raises(RuntimeError, match="requires jwt_role"):
            await client._ensure_authenticated()

    async def test_jwt_missing_token_file_raises(self, tmp_path):
        client = OpenBaoAdminClient(
            OpenBaoAdminConfig(
                auth_method="jwt",
                jwt_role="volundr-app",
                jwt_token_file=str(tmp_path / "absent"),
            )
        )

        with pytest.raises(RuntimeError, match="could not read token file"):
            await client._ensure_authenticated()

    async def test_unknown_auth_method_raises(self):
        client = OpenBaoAdminClient(OpenBaoAdminConfig(auth_method="ldap"))

        with pytest.raises(RuntimeError, match="token, approle, or jwt"):
            await client._ensure_authenticated()

    @respx.mock
    async def test_jwt_lease_expiry_reauthenticates_once(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("sa-jwt")
        client = OpenBaoAdminClient(
            OpenBaoAdminConfig(
                url=BAO_URL,
                auth_method="jwt",
                jwt_mount_path="auth/jwt-ymir",
                jwt_role="volundr-app",
                jwt_token_file=str(token_file),
            )
        )
        login = respx.post(f"{BAO_URL}/v1/auth/jwt-ymir/login")
        login.side_effect = [
            httpx.Response(200, json={"auth": {"client_token": "expired"}}),
            httpx.Response(200, json={"auth": {"client_token": "fresh"}}),
        ]
        policy = respx.put(f"{BAO_URL}/v1/sys/policy/p")
        policy.side_effect = [
            httpx.Response(403, text="permission denied"),
            httpx.Response(204),
        ]

        await client.ensure_policy("p", "policy")

        assert login.call_count == 2
        assert policy.call_count == 2
        assert policy.calls.last.request.headers["X-Vault-Token"] == "fresh"

    @respx.mock
    async def test_static_token_is_not_refreshed_on_403(self, client: OpenBaoAdminClient):
        respx.put(f"{BAO_URL}/v1/sys/policy/p").respond(status_code=403, text="denied")

        with pytest.raises(OpenBaoApiError):
            await client.ensure_policy("p", "policy")

    @respx.mock
    async def test_health_check_handles_success_and_exception(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/health").respond(status_code=429)
        assert await client.health_check() is True

        mock_http = AsyncMock()
        mock_http.get.side_effect = RuntimeError("network")
        failing_client = OpenBaoAdminClient(OpenBaoAdminConfig(url=BAO_URL), client=mock_http)
        assert await failing_client.health_check() is False


class TestApiErrors:
    @respx.mock
    async def test_mount_list_error_raises(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/mounts").respond(status_code=500, text="boom")

        with pytest.raises(OpenBaoApiError) as exc_info:
            await client.ensure_kv_v2_mount("volundr")

        assert exc_info.value.status_code == 500

    @respx.mock
    async def test_auth_backend_list_error_raises(self, client: OpenBaoAdminClient):
        respx.get(f"{BAO_URL}/v1/sys/auth").respond(status_code=500, text="boom")

        with pytest.raises(OpenBaoApiError) as exc_info:
            await client.ensure_jwt_auth_backend("jwt")

        assert exc_info.value.status_code == 500
