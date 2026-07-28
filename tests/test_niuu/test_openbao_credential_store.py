"""Tests for the shared OpenBao credential store adapter."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore
from niuu.domain.models import SecretType, StoredCredential


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
    async def test_invalid_auth_method_raises(self):
        store = OpenBaoCredentialStore(auth_method="invalid")

        with pytest.raises(RuntimeError, match="token, approle, or jwt"):
            await store._ensure_authenticated()

    @pytest.mark.asyncio()
    async def test_approle_requires_role_and_secret(self):
        store = OpenBaoCredentialStore(auth_method="approle", role_id="role-id")

        with pytest.raises(RuntimeError, match="requires role_id and secret_id"):
            await store._ensure_authenticated()

    @pytest.mark.asyncio()
    async def test_jwt_login_reads_workload_token_and_caches_client_token(self, tmp_path: Path):
        token_file = tmp_path / "token"
        token_file.write_text("workload-jwt")
        store = OpenBaoCredentialStore(
            auth_method="jwt",
            jwt_mount_path="auth/jwt-valhalla",
            jwt_role="volundr-app",
            jwt_token_file=str(token_file),
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(
            json_data={"auth": {"client_token": "bao-token"}}
        )
        store._client = mock_client

        token = await store._ensure_authenticated()

        assert token == "bao-token"
        mock_client.post.assert_called_once_with(
            "/v1/auth/jwt-valhalla/login",
            json={"role": "volundr-app", "jwt": "workload-jwt"},
        )

    @pytest.mark.asyncio()
    async def test_jwt_request_reauthenticates_once_after_expired_lease(self, tmp_path: Path):
        token_file = tmp_path / "token"
        token_file.write_text("workload-jwt")
        store = OpenBaoCredentialStore(
            auth_method="jwt",
            jwt_role="volundr-app",
            jwt_token_file=str(token_file),
        )
        mock_client = AsyncMock()
        mock_client.get.side_effect = [
            _mock_response(status_code=403, text="expired"),
            _mock_response(status_code=404),
        ]
        mock_client.post.return_value = _mock_response(
            json_data={"auth": {"client_token": "renewed-token"}}
        )
        store._client = mock_client
        store._client_token = "expired-token"

        assert await store.get("user", "alice", "github") is None

        assert mock_client.get.call_count == 2
        first_headers = mock_client.get.call_args_list[0].kwargs["headers"]
        assert first_headers["X-Vault-Token"] == "expired-token"
        assert (
            mock_client.get.call_args_list[1].kwargs["headers"]["X-Vault-Token"] == "renewed-token"
        )

    @pytest.mark.asyncio()
    async def test_approle_login_failure_raises(self):
        store = OpenBaoCredentialStore(
            auth_method="approle",
            role_id="role-id",
            secret_id="secret-id",
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(status_code=403, text="denied")
        store._client = mock_client

        with pytest.raises(RuntimeError, match="AppRole auth failed"):
            await store._ensure_authenticated()

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


class TestClientLifecycle:
    @pytest.mark.asyncio()
    async def test_get_client_caches_and_close_resets(self):
        store = OpenBaoCredentialStore(namespace="ops")

        client = await store._get_client()
        same_client = await store._get_client()

        assert client is same_client
        assert client.headers["X-Vault-Namespace"] == "ops"

        await store.close()
        assert store._client is None


class TestReadWriteOperations:
    @pytest.mark.asyncio()
    async def test_get_returns_stored_credential(self):
        store = OpenBaoCredentialStore()
        now = datetime.now(UTC)
        meta = {
            "id": "cred-1",
            "name": "github",
            "secret_type": SecretType.GENERIC.value,
            "keys": ["token"],
            "metadata": {"provider": "github"},
            "owner_id": "alice",
            "owner_type": "user",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(
            json_data={"data": {"data": {"token": "secret", "__meta__": json.dumps(meta)}}}
        )
        store._client = mock_client

        credential = await store.get("user", "alice", "github")

        assert credential == StoredCredential(
            id="cred-1",
            name="github",
            secret_type=SecretType.GENERIC,
            keys=("token",),
            metadata={"provider": "github"},
            owner_id="alice",
            owner_type="user",
            created_at=now,
            updated_at=now,
        )

    @pytest.mark.asyncio()
    async def test_get_returns_none_for_missing_or_invalid_meta(self):
        store = OpenBaoCredentialStore()
        mock_client = AsyncMock()
        store._client = mock_client

        mock_client.get.return_value = _mock_response(
            json_data={"data": {"data": {"token": "secret"}}}
        )
        assert await store.get("user", "alice", "github") is None

        mock_client.get.return_value = _mock_response(
            json_data={"data": {"data": {"__meta__": "not-json"}}}
        )
        assert await store.get("user", "alice", "github") is None

    @pytest.mark.asyncio()
    async def test_get_value_omits_meta(self):
        store = OpenBaoCredentialStore()
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(
            json_data={
                "data": {
                    "data": {
                        "token": "secret",
                        "org_id": "acme",
                        "__meta__": '{"id":"cred-1"}',
                    }
                }
            }
        )
        store._client = mock_client

        assert await store.get_value("user", "alice", "github") == {
            "token": "secret",
            "org_id": "acme",
        }

    @pytest.mark.asyncio()
    async def test_store_raises_on_write_error(self):
        store = OpenBaoCredentialStore(token="root-token")
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(status_code=404)
        mock_client.post.return_value = _mock_response(status_code=500, text="boom")
        store._client = mock_client

        with pytest.raises(RuntimeError, match="OpenBao store error"):
            await store.store(
                owner_type="user",
                owner_id="alice",
                name="github",
                secret_type=SecretType.GENERIC,
                data={"token": "secret"},
            )

    @pytest.mark.asyncio()
    async def test_delete_logs_on_non_404_error(self, caplog: pytest.LogCaptureFixture):
        store = OpenBaoCredentialStore(token="root-token")
        mock_client = AsyncMock()
        mock_client.delete.return_value = _mock_response(status_code=500, text="boom")
        store._client = mock_client

        with caplog.at_level(logging.ERROR):
            await store.delete("user", "alice", "github")

        assert "OpenBao delete failed" in caplog.text

    @pytest.mark.asyncio()
    async def test_list_filters_sorts_and_skips_missing(self):
        store = OpenBaoCredentialStore(token="root-token")
        now = datetime.now(UTC).isoformat()
        mock_client = AsyncMock()
        mock_client.get.side_effect = [
            _mock_response(
                json_data={"data": {"keys": ["slack/", "github/", "broken/", "zapier/"]}}
            ),
            _mock_response(
                json_data={
                    "data": {
                        "data": {
                            "__meta__": json.dumps(
                                {
                                    "id": "2",
                                    "name": "slack",
                                    "secret_type": SecretType.GENERIC.value,
                                    "keys": ["token"],
                                    "metadata": {},
                                    "owner_id": "alice",
                                    "owner_type": "user",
                                    "created_at": now,
                                    "updated_at": now,
                                }
                            )
                        }
                    }
                }
            ),
            _mock_response(
                json_data={
                    "data": {
                        "data": {
                            "__meta__": json.dumps(
                                {
                                    "id": "1",
                                    "name": "github",
                                    "secret_type": SecretType.API_KEY.value,
                                    "keys": ["token"],
                                    "metadata": {},
                                    "owner_id": "alice",
                                    "owner_type": "user",
                                    "created_at": now,
                                    "updated_at": now,
                                }
                            )
                        }
                    }
                }
            ),
            _mock_response(json_data={"data": {"data": {"token": "missing-meta"}}}),
            _mock_response(
                json_data={
                    "data": {
                        "data": {
                            "__meta__": json.dumps(
                                {
                                    "id": "4",
                                    "name": "zapier",
                                    "secret_type": SecretType.GENERIC.value,
                                    "keys": ["token"],
                                    "metadata": {},
                                    "owner_id": "alice",
                                    "owner_type": "user",
                                    "created_at": now,
                                    "updated_at": now,
                                }
                            )
                        }
                    }
                }
            ),
        ]
        store._client = mock_client

        results = await store.list("user", "alice", secret_type=SecretType.GENERIC)

        assert [cred.name for cred in results] == ["slack", "zapier"]

    @pytest.mark.asyncio()
    async def test_list_handles_not_found_and_errors(self, caplog: pytest.LogCaptureFixture):
        store = OpenBaoCredentialStore(token="root-token")
        mock_client = AsyncMock()
        store._client = mock_client

        mock_client.get.return_value = _mock_response(status_code=404)
        assert await store.list("user", "alice") == []

        mock_client.get.return_value = _mock_response(status_code=500, text="boom")
        with caplog.at_level(logging.ERROR):
            assert await store.list("user", "alice") == []
        assert "OpenBao list failed" in caplog.text

    @pytest.mark.asyncio()
    async def test_health_check_success_and_failure(self):
        store = OpenBaoCredentialStore()
        mock_client = AsyncMock()
        store._client = mock_client

        mock_client.get.return_value = _mock_response(status_code=200)
        assert await store.health_check() is True

        mock_client.get.side_effect = RuntimeError("network")
        assert await store.health_check() is False
