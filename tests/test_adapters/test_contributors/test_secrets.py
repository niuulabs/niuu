"""Tests for SecretInjectionContributor and SecretsContributor."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from volundr.adapters.outbound.contributors.secrets import (
    SecretInjectionContributor,
    SecretsContributor,
)
from volundr.domain.models import (
    CredentialMapping,
    GitSource,
    IntegrationConnection,
    IntegrationDefinition,
    IntegrationType,
    MCPServerSpec,
    PodSpecAdditions,
    SecretType,
    Session,
    StoredCredential,
)
from volundr.domain.ports import SessionContext
from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry


@pytest.fixture
def session():
    return Session(
        name="test",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id="user-1",
    )


def _connection(credential_name="my-cred", slug="test"):
    return IntegrationConnection(
        id="conn-1",
        owner_id="user-1",
        integration_type="ai_provider",
        adapter="some.adapter",
        credential_name=credential_name,
        config={},
        enabled=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        slug=slug,
    )


def _definition(
    slug="test",
    env_from_credentials=None,
    file_mounts=None,
    mcp_server=None,
):
    return IntegrationDefinition(
        slug=slug,
        name="Test",
        description="Test integration",
        integration_type=IntegrationType.AI_PROVIDER,
        adapter="some.adapter",
        env_from_credentials=env_from_credentials or {},
        file_mounts=file_mounts or {},
        mcp_server=mcp_server,
    )


def _registry(definitions=None):
    """Build a mock IntegrationRegistry."""
    reg = MagicMock()
    defs = {d.slug: d for d in (definitions or [])}
    reg.get_definition = lambda slug: defs.get(slug)
    return reg


class TestSecretInjectionContributor:
    async def test_name(self):
        c = SecretInjectionContributor()
        assert c.name == "secret_injection"

    async def test_no_adapter_returns_empty(self, session):
        c = SecretInjectionContributor()
        result = await c.contribute(session, SessionContext())
        assert result.values == {}
        assert result.pod_spec is None

    async def test_no_owner_returns_empty(self):
        session = Session(name="test", model="claude", source=GitSource(repo="", branch="main"))
        adapter = AsyncMock()
        c = SecretInjectionContributor(secret_injection=adapter)
        result = await c.contribute(session, SessionContext())
        assert result.pod_spec is None
        adapter.pod_spec_additions.assert_not_called()

    async def test_returns_pod_spec(self, session):
        pod_spec = PodSpecAdditions(
            annotations={"org.infisical.com/inject": "true"},
        )
        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = pod_spec
        adapter.ensure_secret_provider_class.return_value = None

        ctx = SessionContext(
            integration_connections=(_connection("my-cred", "test"),),
        )
        c = SecretInjectionContributor(secret_injection=adapter)
        result = await c.contribute(session, ctx)
        assert result.pod_spec is pod_spec
        adapter.pod_spec_additions.assert_called_once_with("user-1", str(session.id))
        adapter.ensure_secret_provider_class.assert_called_once_with(
            "user-1",
            [CredentialMapping(credential_name="my-cred", env_mappings={}, file_mappings={})],
            session_id=str(session.id),
            tenant_id=None,
        )

    async def test_builds_mappings_from_registry(self, session):
        """Credential mappings include env and file mappings from definitions."""
        defn = _definition(
            slug="openai",
            env_from_credentials={"OPENAI_API_KEY": "api_key"},
        )
        registry = _registry([defn])

        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        ctx = SessionContext(
            integration_connections=(_connection("openai-cred", "openai"),),
        )
        c = SecretInjectionContributor(
            secret_injection=adapter,
            integration_registry=registry,
        )
        await c.contribute(session, ctx)

        call_args = adapter.ensure_secret_provider_class.call_args
        mappings = call_args[0][1]  # second positional arg
        assert len(mappings) == 1
        assert mappings[0].credential_name == "openai-cred"
        assert mappings[0].env_mappings == {"OPENAI_API_KEY": "api_key"}

    async def test_builds_mappings_with_mcp_env(self, session):
        """MCP server env_from_credentials are included in mappings."""
        mcp = MCPServerSpec(
            name="linear",
            command="mcp-linear",
            env_from_credentials={"LINEAR_API_KEY": "api_key"},
        )
        defn = _definition(slug="linear", mcp_server=mcp)
        registry = _registry([defn])

        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        ctx = SessionContext(
            integration_connections=(_connection("linear-cred", "linear"),),
        )
        c = SecretInjectionContributor(
            secret_injection=adapter,
            integration_registry=registry,
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert mappings[0].env_mappings == {"LINEAR_API_KEY": "api_key"}

    async def test_builds_mappings_with_file_mounts(self, session):
        """File mounts from definitions are included in mappings."""
        defn = _definition(
            slug="claude",
            file_mounts={"/home/dev/.claude/credentials.json": "oauth_token"},
        )
        registry = _registry([defn])

        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        ctx = SessionContext(
            integration_connections=(_connection("claude-cred", "claude"),),
        )
        c = SecretInjectionContributor(
            secret_injection=adapter,
            integration_registry=registry,
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert mappings[0].file_mappings == {
            "/home/dev/.claude/credentials.json": "oauth_token",
        }

    async def test_integration_auth_ref_maps_mimir_token_file(self, session):
        """Mimir auth refs can target an integration slug portably."""
        defn = _definition(
            slug="volundr",
            env_from_credentials={"EXTERNAL_SERVICE_TOKEN": "token"},
        )
        registry = _registry([defn])

        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        ctx = SessionContext(
            integration_connections=(_connection("volundr-session-runtime-valhalla", "volundr"),),
            workload_type="ravn_flock",
            workload_config={
                "mimir": {
                    "registry_refs": [
                        {
                            "mount_name": "mimir-yggdrasil",
                            "auth_ref": "integration:volundr",
                        }
                    ]
                }
            },
        )
        c = SecretInjectionContributor(
            secret_injection=adapter,
            integration_registry=registry,
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert mappings[0].credential_name == "volundr-session-runtime-valhalla"
        assert mappings[0].env_mappings == {"EXTERNAL_SERVICE_TOKEN": "token"}
        assert mappings[0].file_mappings == {
            "/run/secrets/mimir/integration-volundr/token": "token",
        }

    async def test_direct_credential_names_fallback_without_store(self, session):
        """Direct credential_names have no env/file mappings when no store is available."""
        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        ctx = SessionContext(credential_names=("direct-cred",))
        c = SecretInjectionContributor(secret_injection=adapter)
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert len(mappings) == 1
        assert mappings[0].credential_name == "direct-cred"
        assert mappings[0].env_mappings == {}
        assert mappings[0].file_mappings == {}

    async def test_direct_api_key_resolved_as_env(self, session):
        """API key credentials are resolved to env mappings via mount strategy."""
        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        cred_store = AsyncMock()
        cred_store.get.return_value = StoredCredential(
            id="cred-1",
            name="openai-key",
            secret_type=SecretType.API_KEY,
            keys=("api_key",),
            metadata={},
            owner_id="user-1",
            owner_type="user",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

        ctx = SessionContext(credential_names=("openai-key",))
        c = SecretInjectionContributor(
            secret_injection=adapter,
            credential_store=cred_store,
            mount_strategies=SecretMountStrategyRegistry(),
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert len(mappings) == 1
        assert mappings[0].credential_name == "openai-key"
        assert mappings[0].env_mappings == {"OPENAI_KEY": "api_key"}
        assert mappings[0].file_mappings == {}

    async def test_direct_ssh_key_resolved_as_file(self, session):
        """SSH key credentials are resolved to file mappings via mount strategy."""
        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        cred_store = AsyncMock()
        cred_store.get.return_value = StoredCredential(
            id="cred-2",
            name="my-ssh-key",
            secret_type=SecretType.SSH_KEY,
            keys=("private_key",),
            metadata={},
            owner_id="user-1",
            owner_type="user",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

        ctx = SessionContext(credential_names=("my-ssh-key",))
        c = SecretInjectionContributor(
            secret_injection=adapter,
            credential_store=cred_store,
            mount_strategies=SecretMountStrategyRegistry(),
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert len(mappings) == 1
        assert mappings[0].credential_name == "my-ssh-key"
        assert mappings[0].env_mappings == {}
        assert mappings[0].file_mappings == {"/home/volundr/.ssh/id_rsa": "private_key"}

    async def test_direct_tls_cert_resolved_as_files(self, session):
        """TLS cert credentials with multiple keys produce multiple file mappings."""
        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        cred_store = AsyncMock()
        cred_store.get.return_value = StoredCredential(
            id="cred-3",
            name="my-tls",
            secret_type=SecretType.TLS_CERT,
            keys=("certificate", "private_key"),
            metadata={},
            owner_id="user-1",
            owner_type="user",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

        ctx = SessionContext(credential_names=("my-tls",))
        c = SecretInjectionContributor(
            secret_injection=adapter,
            credential_store=cred_store,
            mount_strategies=SecretMountStrategyRegistry(),
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert len(mappings) == 1
        assert mappings[0].credential_name == "my-tls"
        assert mappings[0].env_mappings == {}
        assert mappings[0].file_mappings == {
            "/run/secrets/tls/certificate": "certificate",
            "/run/secrets/tls/private_key": "private_key",
        }

    async def test_direct_credential_not_found_falls_back_to_empty(self, session):
        """Missing credentials produce empty mappings (no crash)."""
        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        cred_store = AsyncMock()
        cred_store.get.return_value = None

        ctx = SessionContext(credential_names=("nonexistent",))
        c = SecretInjectionContributor(
            secret_injection=adapter,
            credential_store=cred_store,
            mount_strategies=SecretMountStrategyRegistry(),
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert len(mappings) == 1
        assert mappings[0].credential_name == "nonexistent"
        assert mappings[0].env_mappings == {}

    async def test_mixed_integrations_and_direct_credentials(self, session):
        """Integration connections and direct credentials combine in one mapping list."""
        defn = _definition(
            slug="openai",
            env_from_credentials={"OPENAI_API_KEY": "api_key"},
        )
        registry = _registry([defn])

        adapter = AsyncMock()
        adapter.pod_spec_additions.return_value = PodSpecAdditions()
        adapter.ensure_secret_provider_class.return_value = None

        cred_store = AsyncMock()
        cred_store.get.return_value = StoredCredential(
            id="cred-ssh",
            name="my-ssh",
            secret_type=SecretType.SSH_KEY,
            keys=("private_key",),
            metadata={},
            owner_id="user-1",
            owner_type="user",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

        ctx = SessionContext(
            integration_connections=(_connection("openai-cred", "openai"),),
            credential_names=("my-ssh",),
        )
        c = SecretInjectionContributor(
            secret_injection=adapter,
            integration_registry=registry,
            credential_store=cred_store,
            mount_strategies=SecretMountStrategyRegistry(),
        )
        await c.contribute(session, ctx)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert len(mappings) == 2
        # Integration mapping
        assert mappings[0].credential_name == "openai-cred"
        assert mappings[0].env_mappings == {"OPENAI_API_KEY": "api_key"}
        # Direct credential mapping
        assert mappings[1].credential_name == "my-ssh"
        assert mappings[1].file_mappings == {"/home/volundr/.ssh/id_rsa": "private_key"}

    async def test_no_mappings_returns_empty(self, session):
        adapter = AsyncMock()
        c = SecretInjectionContributor(secret_injection=adapter)
        result = await c.contribute(session, SessionContext())
        adapter.ensure_secret_provider_class.assert_not_called()
        adapter.pod_spec_additions.assert_not_called()
        assert result.pod_spec is None

    async def test_ensure_failure_skips_volume(self, session):
        adapter = AsyncMock()
        adapter.ensure_secret_provider_class.side_effect = RuntimeError("403")
        ctx = SessionContext(
            integration_connections=(_connection("some-cred"),),
        )
        c = SecretInjectionContributor(secret_injection=adapter)
        result = await c.contribute(session, ctx)
        assert result.pod_spec is None
        adapter.pod_spec_additions.assert_not_called()

    async def test_cleanup_calls_adapter(self, session):
        adapter = AsyncMock()
        c = SecretInjectionContributor(secret_injection=adapter)
        await c.cleanup(session, SessionContext())
        adapter.cleanup_session.assert_called_once_with(str(session.id))

    async def test_cleanup_noop_without_adapter(self, session):
        c = SecretInjectionContributor()
        await c.cleanup(session, SessionContext())


class TestSecretsContributor:
    async def test_name(self):
        c = SecretsContributor()
        assert c.name == "secrets"

    async def test_contribute_returns_empty(self, session):
        c = SecretsContributor()
        result = await c.contribute(session, SessionContext())
        assert result.values == {}

    async def test_cleanup_calls_delete(self, session):
        repo = AsyncMock()
        c = SecretsContributor(secret_repo=repo)
        await c.cleanup(session, SessionContext())
        repo.delete_session_secrets.assert_called_once_with(str(session.id))

    async def test_cleanup_noop_without_repo(self, session):
        c = SecretsContributor()
        await c.cleanup(session, SessionContext())
