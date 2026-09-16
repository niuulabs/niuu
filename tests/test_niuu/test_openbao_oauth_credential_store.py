"""OAuth engine boundary tests: token custody, rotation, isolation and failures."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from niuu.adapters.openbao_oauth_credential_store import OpenBaoOAuthCredentialStore
from niuu.domain.models import SecretType
from niuu.domain.oauth_credentials import (
    OAUTH_ENGINE,
    OAuthCredentialUnavailableError,
    oauth_credential_name,
)


@pytest.fixture
def store():
    store = OpenBaoOAuthCredentialStore(
        oauth_mount_path="oauthapp", oauth_servers={"gitlab/default": "gitlab"}
    )
    kv = {}
    engine = {"access_token": "new-access", "expire_time": "2099-01-01T00:00:00Z"}

    async def request(method, path, **kwargs):
        if "/oauthapp/creds/" in path:
            return httpx.Response(200, json={"data": engine})
        if method == "get":
            return (
                httpx.Response(200, json={"data": {"data": kv[path]}})
                if path in kv
                else httpx.Response(404)
            )
        if method == "post":
            kv[path] = kwargs["json"]["data"]
        if method == "delete":
            kv.pop(path, None)
        return httpx.Response(204)

    store._request = AsyncMock(side_effect=request)
    return store, kv, engine


async def enroll(store, **changes):
    metadata = {
        "tenant_id": "tenant-a",
        "integration": "gitlab",
        "oauth_app": "default",
        "oauth_token_field": "token",
        "auth_state": "active",
    }
    metadata.update(changes)
    return await store.store(
        "user",
        "alice",
        "gitlab",
        SecretType.OAUTH_TOKEN,
        {"token": "initial-access", "refresh_token": "private-refresh"},
        metadata,
    )


async def test_import_keeps_tokens_out_of_kv_and_reads_rotated_value(store):
    adapter, kv, engine = store
    credential = await enroll(adapter)
    assert credential.keys == ("token", "expires_at")
    assert credential.metadata["renewal_owner"] == OAUTH_ENGINE
    serialized = json.dumps(kv)
    for secret in ("initial-access", "private-refresh", "new-access"):
        assert secret not in serialized
    assert set(next(iter(kv.values()))) == {"__meta__"}
    calls = adapter._request.call_args_list
    imported = next(c for c in calls if c.args[0] == "post" and "/creds/" in c.args[1])
    assert imported.kwargs["json"] == {
        "server": "gitlab",
        "grant_type": "refresh_token",
        "refresh_token": "private-refresh",
    }
    assert (await adapter.get_value("user", "alice", "gitlab"))["token"] == "new-access"
    engine["access_token"] = "rotated-access"
    assert (await adapter.get_value("user", "alice", "gitlab"))["token"] == "rotated-access"
    assert adapter._request.call_args.kwargs == {"params": {"minimum_seconds": 120}}
    engine.pop("expire_time")
    assert await adapter.get_value("user", "alice", "gitlab") == {"token": "rotated-access"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "other"),
        ("integration", "other"),
        ("oauth_app", "other"),
        ("oauth_token_field", "other"),
    ],
)
async def test_managed_identity_cannot_be_reassigned(store, field, value):
    adapter, _, _ = store
    await enroll(adapter)
    adapter._request.reset_mock()
    with pytest.raises(ValueError, match="identity"):
        await enroll(adapter, **{field: value})
    assert all(c.args[0] == "get" for c in adapter._request.call_args_list)


async def test_metadata_update_does_not_reimport_refresh_token(store):
    adapter, kv, _ = store
    await enroll(adapter)
    adapter._request.reset_mock()
    await adapter.store(
        "user", "alice", "gitlab", SecretType.OAUTH_TOKEN, {}, {"auth_state": "auth_required"}
    )
    assert all("/creds/" not in c.args[1] for c in adapter._request.call_args_list)
    assert "private-refresh" not in json.dumps(kv)
    assert (await adapter.get("user", "alice", "gitlab")).metadata["tenant_id"] == "tenant-a"


@pytest.mark.parametrize(
    "status,errors,reconnect",
    [
        (400, ["token expired"], True),
        (400, ["invalid_grant private-secret"], True),
        (403, [], False),
        (500, [], False),
        (404, [], True),
    ],
)
async def test_failures_do_not_leak_provider_errors(store, status, errors, reconnect):
    adapter, _, _ = store
    await enroll(adapter)
    original = adapter._request.side_effect

    async def failed(method, path, **kwargs):
        if "/creds/" in path:
            return httpx.Response(status, json={"errors": errors})
        return await original(method, path, **kwargs)

    adapter._request.side_effect = failed
    with pytest.raises(OAuthCredentialUnavailableError) as exc:
        await adapter.get_value("user", "alice", "gitlab")
    assert exc.value.reconnect is reconnect
    assert "private-secret" not in str(exc.value)


async def test_ordinary_secrets_and_delete(store):
    adapter, _, _ = store
    await adapter.store("user", "alice", "api", SecretType.OAUTH_TOKEN, {"token": "api-token"})
    assert await adapter.get_value("user", "alice", "api") == {"token": "api-token"}
    assert await adapter.get_value("user", "alice", "missing") is None
    await enroll(adapter)
    adapter._request.reset_mock()
    await adapter.delete("user", "alice", "gitlab")
    deletes = [c.args[1] for c in adapter._request.call_args_list if c.args[0] == "delete"]
    assert deletes[0].startswith("/v1/oauthapp/creds/")
    assert deletes[1] == "/v1/volundr/data/users/alice/gitlab"
    assert await adapter.get("user", "alice", "gitlab") is None


async def test_import_requires_registered_client_and_tenant(store):
    adapter, _, _ = store
    with pytest.raises(ValueError, match="oauth_servers"):
        await enroll(adapter, oauth_app="unknown")
    with pytest.raises(ValueError, match="tenant"):
        await enroll(adapter, tenant_id="")
    with pytest.raises(ValueError, match="enrollment"):
        await adapter.store(
            "user", "alice", "forged", SecretType.OAUTH_TOKEN, {}, {"renewal_owner": OAUTH_ENGINE}
        )


def test_engine_identity_is_scoped_and_literal():
    names = {
        oauth_credential_name(t, u, c)
        for t in ("a", "b")
        for u in ("a", "b")
        for c in ("../*", "b")
    }
    assert len(names) == 8
    assert all(len(n) == 64 and n.isalnum() for n in names)
    with pytest.raises(ValueError):
        OpenBaoOAuthCredentialStore(oauth_mount_path="oauth/*", oauth_servers={})


async def test_engine_network_outage_is_safe_and_not_reconnect(store):
    adapter, _, _ = store
    await enroll(adapter)
    original = adapter._request.side_effect

    async def disconnected(method, path, **kwargs):
        if "/creds/" in path:
            raise httpx.ConnectError("private-network-detail")
        return await original(method, path, **kwargs)

    adapter._request.side_effect = disconnected
    with pytest.raises(OAuthCredentialUnavailableError) as exc:
        await adapter.get_value("user", "alice", "gitlab")
    assert exc.value.reconnect is False
    assert "private-network-detail" not in str(exc.value)


async def test_replacement_without_refresh_token_is_rejected(store):
    adapter, _, _ = store
    await enroll(adapter)
    with pytest.raises(ValueError, match="refresh token"):
        await adapter.store(
            "user", "alice", "gitlab", SecretType.OAUTH_TOKEN, {"token": "replacement"}
        )


async def test_managed_application_provisions_same_server_used_by_grants():
    from niuu.domain.oauth_credentials import oauth_application_name

    adapter = OpenBaoOAuthCredentialStore(
        oauth_mount_path="oauthapp", manage_oauth_applications=True
    )
    adapter._request = AsyncMock(return_value=httpx.Response(204))
    await adapter.configure_oauth_application(
        slug="gitlab",
        app="default",
        client_id="client",
        client_secret="private-client-secret",
        authorize_url="https://gitlab.com/oauth/authorize",
        token_url="https://gitlab.com/oauth/token",
    )
    call = adapter._request.call_args
    assert call.args == (
        "post",
        "/v1/oauthapp/servers/" + oauth_application_name("gitlab", "default"),
    )
    assert call.kwargs["json"]["provider_options"]["auth_style"] == "in_params"
    assert call.kwargs["json"]["client_secret"] == "private-client-secret"
    adapter._request.return_value = httpx.Response(403, text="private-client-secret")
    with pytest.raises(RuntimeError, match="HTTP 403") as error:
        await adapter.configure_oauth_application(
            slug="gitlab",
            app="default",
            client_id="client",
            client_secret="private-client-secret",
            authorize_url="https://gitlab.com/oauth/authorize",
            token_url="https://gitlab.com/oauth/token",
        )
    assert "private-client-secret" not in str(error.value)
    with pytest.raises(ValueError, match="HTTPS"):
        await adapter.configure_oauth_application(
            slug="gitlab",
            app="default",
            client_id="client",
            client_secret="",
            authorize_url="http://gitlab.com/oauth/authorize",
            token_url="https://gitlab.com/oauth/token",
        )


async def test_managed_application_mode_import_uses_deterministic_server(store):
    from niuu.domain.oauth_credentials import oauth_application_name

    adapter, _, _ = store
    adapter._manage_apps = True
    adapter._oauth_servers = {}
    await enroll(adapter)
    imported = next(
        c for c in adapter._request.call_args_list if c.args[0] == "post" and "/creds/" in c.args[1]
    )
    assert imported.kwargs["json"]["server"] == oauth_application_name("gitlab", "default")
