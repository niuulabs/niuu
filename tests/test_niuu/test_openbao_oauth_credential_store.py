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
        "maximum_expiry_seconds": 3600,
    }
    assert (await adapter.get_value("user", "alice", "gitlab"))["token"] == "new-access"
    engine["access_token"] = "rotated-access"
    assert (await adapter.get_value("user", "alice", "gitlab"))["token"] == "rotated-access"
    reads = [
        c for c in adapter._request.call_args_list if c.args[0] == "get" and "/creds/" in c.args[1]
    ]
    assert reads[-1].kwargs == {"params": {"minimum_seconds": 120}}
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
        (500, ["oauth2: invalid_grant private-secret"], True),
        (500, ["401 Unauthorized: refresh_token_expired private-secret"], True),
        (500, ["refresh_token_reused private-secret"], True),
        (500, ["refresh_token_invalidated private-secret"], True),
        (500, ["invalid_client private-secret"], False),
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


def codex_document(account="account", refresh="private-refresh"):
    import time

    import jwt

    token = jwt.encode(
        {
            "exp": int(time.time()) + 3600,
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account,
                "chatgpt_plan_type": "pro",
            },
        },
        key="",
        algorithm="none",
    )
    return json.dumps(
        {
            "tokens": {
                "access_token": token,
                "refresh_token": refresh,
                "id_token": "private-id",
                "account_id": account,
            }
        }
    )


async def enroll_codex(store, **metadata):
    return await store.store(
        "user",
        "alice",
        "codex",
        SecretType.OAUTH_TOKEN,
        {"auth.json": codex_document(), "config.toml": 'model = "gpt-5"'},
        {
            "enrollment_method": "codex_device",
            "tenant_id": "tenant-a",
            "oauth_token_field": "auth.json",
            **metadata,
        },
    )


@pytest.fixture
def codex_store(store):
    adapter, kv, engine = store
    adapter._codex_server = "niuu-codex-subscription"
    engine.update(
        server=adapter._codex_server,
        access_token=json.loads(codex_document())["tokens"]["access_token"],
    )
    return adapter, kv, engine


async def test_codex_import_strips_login_tokens_and_preserves_configuration(codex_store):
    adapter, kv, engine = codex_store
    stored = await enroll_codex(adapter)
    assert stored.metadata["oauth_format"] == "codex_auth"
    serialized = json.dumps(kv)
    for secret in ("private-refresh", "private-id", engine["access_token"]):
        assert secret not in serialized
    assert "config.toml" in (await adapter.get("user", "alice", "codex")).keys
    values = await adapter.get_value("user", "alice", "codex")
    assert values["config.toml"] == 'model = "gpt-5"'
    assert json.loads(values["auth.json"])["tokens"] == {
        "access_token": engine["access_token"],
        "account_id": "account",
    }
    adapter._request.reset_mock()
    await adapter.store(
        "user", "alice", "codex", SecretType.OAUTH_TOKEN, {}, {"auth_state": "pending"}
    )
    assert all("/creds/" not in c.args[1] for c in adapter._request.call_args_list)
    assert (await adapter.get("user", "alice", "codex")).metadata["codex_account_id"] == "account"


async def test_codex_tenant_cannot_change(codex_store):
    adapter, _, _ = codex_store
    await enroll_codex(adapter)
    with pytest.raises(ValueError, match="identity"):
        await enroll_codex(adapter, tenant_id="other")


async def test_codex_account_mismatch_is_rejected(codex_store):
    adapter, _, engine = codex_store
    engine["access_token"] = json.loads(codex_document(account="other"))["tokens"]["access_token"]
    with pytest.raises(ValueError, match="account"):
        await enroll_codex(adapter)


async def test_codex_requires_configured_server(codex_store):
    adapter, _, _ = codex_store
    adapter._codex_server = ""
    with pytest.raises(ValueError, match="codex_oauth_server"):
        await enroll_codex(adapter)


async def test_codex_migration_resumes_engine_import_without_reusing_refresh_token(codex_store):
    from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore

    adapter, kv, _ = codex_store
    await OpenBaoCredentialStore.store(
        adapter,
        "user",
        "alice",
        "codex",
        SecretType.GENERIC,
        {"auth.json": codex_document(), "config.toml": "config"},
        {"integration": "codex"},
    )
    adapter._request.reset_mock()
    assert await adapter.migrate_codex_credential(
        owner_id="alice", tenant_id="tenant-a", name="codex"
    )
    assert not any(
        c.args[0] == "post" and "/creds/" in c.args[1] for c in adapter._request.call_args_list
    )
    assert "private-refresh" not in json.dumps(kv)
    assert not await adapter.migrate_codex_credential(
        owner_id="alice", tenant_id="tenant-a", name="codex"
    )
    with pytest.raises(ValueError, match="tenant"):
        await adapter.migrate_codex_credential(owner_id="alice", tenant_id="other", name="codex")


async def test_codex_migration_clears_legacy_reconnect_status(codex_store):
    from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore

    adapter, _, _ = codex_store
    await OpenBaoCredentialStore.store(
        adapter,
        "user",
        "alice",
        "codex",
        SecretType.GENERIC,
        {"auth.json": codex_document()},
        {"auth_state": "auth_required", "auth_error_code": "refresh_failed"},
    )
    await adapter.migrate_codex_credential(owner_id="alice", tenant_id="tenant-a", name="codex")
    metadata = (await adapter.get("user", "alice", "codex")).metadata
    assert metadata["auth_state"] == "active"
    assert "auth_error_code" not in metadata


async def test_metadata_outage_is_reported_as_unavailable(store):
    adapter, _, _ = store
    adapter._request.side_effect = httpx.ConnectError("private provider context")
    with pytest.raises(OAuthCredentialUnavailableError) as exc:
        await adapter.get("user", "alice", "codex")
    assert not exc.value.reconnect
    assert "private" not in str(exc.value)


async def test_codex_import_reports_wrapped_provider_expiry_without_leaking(codex_store):
    adapter, kv, _ = codex_store
    original = adapter._request.side_effect

    async def request(method, path, **kwargs):
        if method == "post" and "/creds/" in path:
            return httpx.Response(
                500, json={"errors": ["401 Unauthorized: refresh_token_expired private-secret"]}
            )
        return await original(method, path, **kwargs)

    adapter._request.side_effect = request
    with pytest.raises(OAuthCredentialUnavailableError) as exc:
        await enroll_codex(adapter)
    assert exc.value.reconnect
    assert "private-secret" not in str(exc.value)
    assert not kv


async def test_flat_migration_resumes_without_replaying_rotating_grant(store):
    from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore

    adapter, kv, engine = store
    engine["server"] = "gitlab"
    await OpenBaoCredentialStore.store(
        adapter,
        "user",
        "alice",
        "legacy",
        SecretType.OAUTH_TOKEN,
        {
            "token": "old-access",
            "refresh_token": "old-refresh",
            "id_token": "old-id",
            "cloud_id": "site",
        },
        {"integration": "gitlab"},
    )
    original = adapter._request.side_effect
    imported = False
    fail_metadata = True
    calls = 0

    async def request(method, path, **kwargs):
        nonlocal imported, fail_metadata, calls
        if "/creds/" in path:
            if method == "post":
                imported = True
                calls += 1
            if not imported:
                return httpx.Response(404)
        if imported and fail_metadata and method == "post" and "/data/" in path:
            fail_metadata = False
            raise httpx.ConnectError("storage interrupted")
        return await original(method, path, **kwargs)

    adapter._request.side_effect = request
    args = dict(
        owner_id="alice",
        tenant_id="tenant-a",
        name="legacy",
        integration="gitlab",
        oauth_app="default",
        token_field="token",
    )
    with pytest.raises(OAuthCredentialUnavailableError):
        await adapter.migrate_oauth_credential(**args)
    assert calls == 1
    assert await adapter.migrate_oauth_credential(**args)
    assert not await adapter.migrate_oauth_credential(**args)
    assert calls == 1
    assert await OpenBaoCredentialStore.get_value(adapter, "user", "alice", "legacy") == {
        "cloud_id": "site"
    }
    assert (await adapter.get_value("user", "alice", "legacy"))["cloud_id"] == "site"
    assert "old-refresh" not in json.dumps(kv)


@pytest.mark.parametrize("key", ["tenant_id", "integration", "oauth_app", "oauth_token_field"])
async def test_flat_migration_rejects_identity_mismatch_before_engine_read(store, key):
    adapter, _, _ = store
    await enroll(adapter)
    adapter._request.reset_mock()
    args = dict(
        owner_id="alice",
        tenant_id="tenant-a",
        name="gitlab",
        integration="gitlab",
        oauth_app="default",
        token_field="token",
    )
    args["token_field" if key == "oauth_token_field" else key] = "other"
    with pytest.raises(ValueError, match="identity"):
        await adapter.migrate_oauth_credential(**args)
    assert all("/creds/" not in call.args[1] for call in adapter._request.call_args_list)


async def test_enrollment_preserves_auxiliary_fields_on_metadata_update(store):
    adapter, _, _ = store
    await adapter.store(
        "user",
        "alice",
        "gitlab",
        SecretType.OAUTH_TOKEN,
        {"token": "old", "refresh_token": "refresh", "cloud_id": "site", "id_token": "private"},
        {"tenant_id": "tenant-a", "integration": "gitlab", "oauth_token_field": "token"},
    )
    await adapter.store(
        "user", "alice", "gitlab", SecretType.OAUTH_TOKEN, {}, {"auth_state": "active"}
    )
    values = await adapter.get_value("user", "alice", "gitlab")
    assert values["cloud_id"] == "site"
    assert "id_token" not in values


@pytest.mark.parametrize(
    "status,errors,reconnect", [(500, ["invalid_grant secret"], True), (503, [], False)]
)
async def test_flat_enrollment_reports_safe_provider_errors(store, status, errors, reconnect):
    adapter, kv, _ = store
    original = adapter._request.side_effect

    async def failed(method, path, **kwargs):
        if method == "post" and "/creds/" in path:
            return httpx.Response(status, json={"errors": errors})
        return await original(method, path, **kwargs)

    adapter._request.side_effect = failed
    with pytest.raises(OAuthCredentialUnavailableError) as exc:
        await enroll(adapter)
    assert exc.value.reconnect is reconnect
    assert "secret" not in str(exc.value)
    assert not kv


@pytest.mark.parametrize("server,refresh", [("wrong-app", True), (None, False)])
async def test_flat_migration_refuses_unmatched_app_or_nonrenewable_key(store, server, refresh):
    from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore

    adapter, _, engine = store
    engine["server"] = server
    values = {"token": "static"}
    if refresh:
        values["refresh_token"] = "refresh"
    await OpenBaoCredentialStore.store(
        adapter,
        "user",
        "alice",
        "legacy",
        SecretType.OAUTH_TOKEN,
        values,
        {"integration": "gitlab"},
    )
    original = adapter._request.side_effect

    async def request(method, path, **kwargs):
        if "/creds/" in path and server is None:
            return httpx.Response(404)
        return await original(method, path, **kwargs)

    adapter._request.side_effect = request
    adapter._request.reset_mock()
    with pytest.raises(ValueError):
        await adapter.migrate_oauth_credential(
            owner_id="alice",
            tenant_id="tenant-a",
            name="legacy",
            integration="gitlab",
            oauth_app="default",
            token_field="token",
        )
    assert all(call.args[0] == "get" for call in adapter._request.call_args_list)
