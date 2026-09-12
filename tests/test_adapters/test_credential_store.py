"""Tests for MemoryCredentialStore, mount strategies, and CredentialService."""

from __future__ import annotations

import pytest

from identity.adapters.cedar import CedarAuthorizationAdapter
from volundr.adapters.outbound.memory_credential_store import MemoryCredentialStore
from volundr.domain.models import Principal, SecretType
from volundr.domain.services.credential import (
    CredentialService,
    CredentialValidationError,
)
from volundr.domain.services.mount_strategies import (
    ApiKeyMountStrategy,
    GenericMountStrategy,
    GitCredentialMountStrategy,
    OAuthTokenMountStrategy,
    SecretMountStrategyRegistry,
    SshKeyMountStrategy,
    TlsCertMountStrategy,
)

# ------------------------------------------------------------------
# MemoryCredentialStore
# ------------------------------------------------------------------


class TestMemoryCredentialStore:
    """CRUD tests for the in-memory credential store."""

    @pytest.fixture()
    def store(self):
        return MemoryCredentialStore()

    @pytest.mark.asyncio()
    async def test_store_and_get(self, store):
        cred = await store.store("user", "u1", "my-key", SecretType.API_KEY, {"token": "abc"})
        assert cred.name == "my-key"
        assert cred.secret_type == SecretType.API_KEY
        assert "token" in cred.keys
        assert cred.owner_id == "u1"
        assert cred.owner_type == "user"

        fetched = await store.get("user", "u1", "my-key")
        assert fetched is not None
        assert fetched.id == cred.id

    @pytest.mark.asyncio()
    async def test_get_value(self, store):
        await store.store("user", "u1", "my-key", SecretType.API_KEY, {"token": "secret123"})
        value = await store.get_value("user", "u1", "my-key")
        assert value == {"token": "secret123"}

    @pytest.mark.asyncio()
    async def test_get_missing_returns_none(self, store):
        result = await store.get("user", "u1", "missing")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_value_missing_returns_none(self, store):
        result = await store.get_value("user", "u1", "missing")
        assert result is None

    @pytest.mark.asyncio()
    async def test_delete(self, store):
        await store.store("user", "u1", "my-key", SecretType.API_KEY, {"token": "abc"})
        await store.delete("user", "u1", "my-key")
        assert await store.get("user", "u1", "my-key") is None
        assert await store.get_value("user", "u1", "my-key") is None

    @pytest.mark.asyncio()
    async def test_delete_missing_does_not_raise(self, store):
        await store.delete("user", "u1", "nonexistent")

    @pytest.mark.asyncio()
    async def test_list(self, store):
        await store.store("user", "u1", "a-key", SecretType.API_KEY, {"k": "v"})
        await store.store("user", "u1", "b-key", SecretType.SSH_KEY, {"private_key": "..."})
        await store.store("user", "u2", "c-key", SecretType.API_KEY, {"k": "v"})

        results = await store.list("user", "u1")
        assert len(results) == 2
        assert results[0].name == "a-key"
        assert results[1].name == "b-key"

    @pytest.mark.asyncio()
    async def test_list_with_type_filter(self, store):
        await store.store("user", "u1", "a-key", SecretType.API_KEY, {"k": "v"})
        await store.store("user", "u1", "b-key", SecretType.SSH_KEY, {"private_key": "..."})

        results = await store.list("user", "u1", SecretType.API_KEY)
        assert len(results) == 1
        assert results[0].name == "a-key"

    @pytest.mark.asyncio()
    async def test_list_empty(self, store):
        results = await store.list("user", "u1")
        assert results == []

    @pytest.mark.asyncio()
    async def test_store_overwrites_existing(self, store):
        cred1 = await store.store("user", "u1", "key", SecretType.API_KEY, {"old": "val"})
        cred2 = await store.store("user", "u1", "key", SecretType.API_KEY, {"new": "val"})
        assert cred2.id == cred1.id
        assert cred2.created_at == cred1.created_at
        assert "new" in cred2.keys
        value = await store.get_value("user", "u1", "key")
        assert value == {"new": "val"}

    @pytest.mark.asyncio()
    async def test_health_check(self, store):
        assert await store.health_check() is True

    @pytest.mark.asyncio()
    async def test_tenant_isolation(self, store):
        await store.store("tenant", "t1", "shared", SecretType.GENERIC, {"k": "v"})
        await store.store("user", "u1", "shared", SecretType.GENERIC, {"k": "v2"})

        tenant_list = await store.list("tenant", "t1")
        user_list = await store.list("user", "u1")
        assert len(tenant_list) == 1
        assert len(user_list) == 1
        assert tenant_list[0].owner_type == "tenant"
        assert user_list[0].owner_type == "user"


# ------------------------------------------------------------------
# Mount Strategies
# ------------------------------------------------------------------


class TestMountStrategies:
    """Validation tests for each mount strategy."""

    def test_api_key_valid(self):
        s = ApiKeyMountStrategy()
        assert s.secret_type() == SecretType.API_KEY
        assert s.validate({"api_key": "abc"}) == []

    def test_api_key_empty_data(self):
        s = ApiKeyMountStrategy()
        errors = s.validate({})
        assert len(errors) == 1

    def test_oauth_valid(self):
        s = OAuthTokenMountStrategy()
        assert s.secret_type() == SecretType.OAUTH_TOKEN
        assert s.validate({"access_token": "tok"}) == []

    def test_oauth_missing_access_token(self):
        s = OAuthTokenMountStrategy()
        errors = s.validate({"refresh_token": "tok"})
        assert any("access_token" in e for e in errors)

    def test_git_credential_valid(self):
        s = GitCredentialMountStrategy()
        assert s.secret_type() == SecretType.GIT_CREDENTIAL
        assert s.validate({"url": "https://user:tok@github.com"}) == []

    def test_git_credential_missing_url(self):
        s = GitCredentialMountStrategy()
        errors = s.validate({})
        assert any("url" in e for e in errors)

    def test_ssh_key_valid(self):
        s = SshKeyMountStrategy()
        assert s.secret_type() == SecretType.SSH_KEY
        assert s.validate({"private_key": "-----BEGIN..."}) == []

    def test_ssh_key_missing_private_key(self):
        s = SshKeyMountStrategy()
        errors = s.validate({})
        assert any("private_key" in e for e in errors)

    def test_tls_cert_valid(self):
        s = TlsCertMountStrategy()
        assert s.secret_type() == SecretType.TLS_CERT
        assert s.validate({"certificate": "cert", "private_key": "key"}) == []

    def test_tls_cert_missing_fields(self):
        s = TlsCertMountStrategy()
        errors = s.validate({})
        assert len(errors) == 2

    def test_generic_valid(self):
        s = GenericMountStrategy()
        assert s.secret_type() == SecretType.GENERIC
        assert s.validate({"anything": "val"}) == []

    def test_generic_empty_rejected(self):
        s = GenericMountStrategy()
        errors = s.validate({})
        assert len(errors) == 1


class TestSecretMountStrategyRegistry:
    """Registry lookup tests."""

    def test_all_types_registered(self):
        registry = SecretMountStrategyRegistry()
        for st in SecretType:
            strategy = registry.get(st)
            assert strategy.secret_type() == st

    def test_list_types(self):
        registry = SecretMountStrategyRegistry()
        type_list = registry.list_types()
        assert len(type_list) == len(SecretType)
        types_returned = {t["type"] for t in type_list}
        for st in SecretType:
            assert st.value in types_returned


# ------------------------------------------------------------------
# CredentialService
# ------------------------------------------------------------------


class TestCredentialService:
    """Service tests with real MemoryCredentialStore."""

    @pytest.fixture()
    def service(self):
        store = MemoryCredentialStore()
        strategies = SecretMountStrategyRegistry()
        return CredentialService(store, strategies, authorization=CedarAuthorizationAdapter())

    @pytest.mark.asyncio()
    async def test_create_valid(self, service):
        cred = await service.create(
            Principal("u1", "", "t1", ["volundr:developer"]),
            "user",
            "u1",
            "my-key",
            SecretType.API_KEY,
            {"api_key": "val"},
        )
        assert cred.name == "my-key"
        assert cred.secret_type == SecretType.API_KEY

    @pytest.mark.asyncio()
    async def test_create_invalid_raises(self, service):
        with pytest.raises(CredentialValidationError) as exc_info:
            await service.create(
                Principal("u1", "", "t1", ["volundr:developer"]),
                "user",
                "u1",
                "my-key",
                SecretType.API_KEY,
                {},
            )
        assert len(exc_info.value.errors) > 0

    @pytest.mark.asyncio()
    async def test_list(self, service):
        await service.create(
            Principal("u1", "", "t1", ["volundr:developer"]),
            "user",
            "u1",
            "a",
            SecretType.GENERIC,
            {"k": "v"},
        )
        await service.create(
            Principal("u1", "", "t1", ["volundr:developer"]),
            "user",
            "u1",
            "b",
            SecretType.GENERIC,
            {"k": "v"},
        )
        results = await service.list(Principal("u1", "", "t1", ["volundr:developer"]), "user", "u1")
        assert len(results) == 2

    @pytest.mark.asyncio()
    async def test_list_with_type_filter(self, service):
        await service.create(
            Principal("u1", "", "t1", ["volundr:developer"]),
            "user",
            "u1",
            "a",
            SecretType.GENERIC,
            {"k": "v"},
        )
        await service.create(
            Principal("u1", "", "t1", ["volundr:developer"]),
            "user",
            "u1",
            "b",
            SecretType.API_KEY,
            {"api_key": "v"},
        )
        results = await service.list(
            Principal("u1", "", "t1", ["volundr:developer"]), "user", "u1", SecretType.API_KEY
        )
        assert len(results) == 1
        assert results[0].name == "b"

    @pytest.mark.asyncio()
    async def test_get(self, service):
        await service.create(
            Principal("u1", "", "t1", ["volundr:developer"]),
            "user",
            "u1",
            "key",
            SecretType.GENERIC,
            {"k": "v"},
        )
        cred = await service.get(
            Principal("u1", "", "t1", ["volundr:developer"]), "user", "u1", "key"
        )
        assert cred is not None
        assert cred.name == "key"

    @pytest.mark.asyncio()
    async def test_get_missing(self, service):
        result = await service.get(
            Principal("u1", "", "t1", ["volundr:developer"]), "user", "u1", "nope"
        )
        assert result is None

    @pytest.mark.asyncio()
    async def test_delete(self, service):
        await service.create(
            Principal("u1", "", "t1", ["volundr:developer"]),
            "user",
            "u1",
            "key",
            SecretType.GENERIC,
            {"k": "v"},
        )
        await service.delete(Principal("u1", "", "t1", ["volundr:developer"]), "user", "u1", "key")
        assert (
            await service.get(Principal("u1", "", "t1", ["volundr:developer"]), "user", "u1", "key")
            is None
        )

    def test_get_types(self, service):
        types = service.get_types()
        assert len(types) == len(SecretType)
        type_values = {t["type"] for t in types}
        assert "api_key" in type_values
        assert "generic" in type_values

    @pytest.mark.asyncio()
    async def test_oauth_validation(self, service):
        with pytest.raises(CredentialValidationError):
            await service.create(
                Principal("u1", "", "t1", ["volundr:developer"]),
                "user",
                "u1",
                "oauth",
                SecretType.OAUTH_TOKEN,
                {"refresh_token": "tok"},
            )

    @pytest.mark.asyncio()
    async def test_ssh_validation(self, service):
        with pytest.raises(CredentialValidationError):
            await service.create(
                Principal("u1", "", "t1", ["volundr:developer"]),
                "user",
                "u1",
                "ssh",
                SecretType.SSH_KEY,
                {},
            )

    @pytest.mark.asyncio()
    async def test_tls_validation(self, service):
        with pytest.raises(CredentialValidationError):
            await service.create(
                Principal("u1", "", "t1", ["volundr:developer"]),
                "user",
                "u1",
                "tls",
                SecretType.TLS_CERT,
                {"certificate": "c"},
            )
