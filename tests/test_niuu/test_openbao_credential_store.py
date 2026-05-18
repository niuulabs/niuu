"""Tests for the shared OpenBao credential store adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore
from niuu.domain.models import SecretType


def _mock_response(*, status_code: int = 200, json_data: dict | None = None, text: str = ""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text or str(json_data)
    if json_data is not None:
        response.json.return_value = json_data
    return response


class TestInit:
    def test_defaults(self):
        store = OpenBaoCredentialStore()
        assert store._url == "http://openbao.volundr-system:8200"
        assert store._mount_path == "volundr"
        assert store._auth_method == "token"

    def test_custom_values(self):
        store = OpenBaoCredentialStore(
            url="https://bao.example.com/",
            namespace="team-a",
            mount_path="ting",
            auth_method="approle",
            role_id="role-1",
            secret_id="secret-1",
        )
        assert store._url == "https://bao.example.com"
        assert store._namespace == "team-a"
        assert store._mount_path == "ting"
        assert store._auth_method == "approle"


class TestHeaders:
    @pytest.mark.asyncio()
    async def test_headers_include_namespace_and_token(self):
        store = OpenBaoCredentialStore(namespace="ops", token="s.test")
        assert await store._headers() == {
            "X-Vault-Token": "s.test",
            "X-Vault-Namespace": "ops",
        }

    @pytest.mark.asyncio()
    async def test_token_auth_without_token_omits_auth_header(self):
        store = OpenBaoCredentialStore(namespace="ops")
        assert await store._headers() == {"X-Vault-Namespace": "ops"}


class TestAppRoleAuth:
    @pytest.mark.asyncio()
    async def test_approle_login_caches_client_token(self):
        store = OpenBaoCredentialStore(
            auth_method="approle",
            role_id="role-id",
            secret_id="secret-id",
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(
            json_data={"auth": {"client_token": "bao-token"}}
        )
        store._client = mock_client

        token = await store._ensure_authenticated()

        assert token == "bao-token"
        assert store._client_token == "bao-token"
        mock_client.post.assert_called_once_with(
            "/v1/auth/approle/login",
            json={"role_id": "role-id", "secret_id": "secret-id"},
        )

    @pytest.mark.asyncio()
    async def test_store_uses_mount_specific_paths(self):
        store = OpenBaoCredentialStore(mount_path="ting")
        mock_client = AsyncMock()
        store._client = mock_client
        mock_client.get.return_value = _mock_response(status_code=404)
        mock_client.post.return_value = _mock_response(json_data={})

        await store.store(
            owner_type="user",
            owner_id="u-1",
            name="telegram",
            secret_type=SecretType.GENERIC,
            data={"bot_token": "x"},
        )

        mock_client.post.assert_called_once()
        request_path = mock_client.post.call_args[0][0]
        assert request_path == "/v1/ting/data/users/u-1/telegram"

