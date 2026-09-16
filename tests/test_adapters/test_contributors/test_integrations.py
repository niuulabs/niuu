"""Tests for IntegrationContributor."""

from datetime import UTC, datetime

import pytest

from volundr.adapters.outbound.contributors.integrations import IntegrationContributor
from volundr.config import Settings
from volundr.domain.models import (
    GitSource,
    IntegrationConnection,
    IntegrationDefinition,
    MCPServerSpec,
    Principal,
    Session,
)
from volundr.domain.ports import SessionContext
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)


@pytest.fixture
def session():
    return Session(
        name="test",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id="user-1",
    )


@pytest.fixture
def principal():
    return Principal(user_id="user-1", email="u@x.com", tenant_id="t-1", roles=[])


def _linear_definition():
    return IntegrationDefinition(
        slug="linear",
        name="Linear",
        description="Issue tracker",
        integration_type="issue_tracker",
        adapter="volundr.adapters.outbound.linear.LinearAdapter",
        icon="linear",
        mcp_server=MCPServerSpec(
            name="linear",
            command="npx",
            args=("@anthropic/linear-mcp",),
            env_from_credentials={"LINEAR_API_KEY": "api_key"},
        ),
    )


def _linear_connection(*, conn_id="conn-linear", enabled=True):
    return IntegrationConnection(
        id=conn_id,
        owner_id="user-1",
        integration_type="issue_tracker",
        adapter="volundr.adapters.outbound.linear.LinearAdapter",
        credential_name="linear-cred",
        config={},
        enabled=enabled,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        slug="linear",
    )


class TestIntegrationContributor:
    async def test_claude_connection_uses_existing_secret_delivery(self, session, principal):
        from dataclasses import replace

        registry = IntegrationRegistry(
            definitions_from_config(
                [definition.model_dump() for definition in Settings().integrations.definitions]
            )
        )
        connection = replace(
            _linear_connection(), slug="claude-code", credential_name="claude-code-credentials"
        )
        contribution = await IntegrationContributor(integration_registry=registry).contribute(
            session, SessionContext(principal=principal, integration_connections=(connection,))
        )
        assert contribution.values["secretManifest"]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == {
            "file": "claude-code-credentials",
            "key": "token",
        }

    async def test_name(self):
        c = IntegrationContributor()
        assert c.name == "integrations"

    async def test_no_connections_returns_empty(self, session):
        c = IntegrationContributor()
        result = await c.contribute(session, SessionContext())
        assert result.values == {}

    async def test_no_registry_returns_empty(self, session, principal):
        ctx = SessionContext(
            principal=principal,
            integration_connections=(_linear_connection(),),
        )
        c = IntegrationContributor()
        result = await c.contribute(session, ctx)
        assert result.values == {}

    async def test_resolves_mcp_server_with_empty_env(self, session, principal):
        """MCP server config has empty env — entrypoint sources from manifest."""
        registry = IntegrationRegistry([_linear_definition()])

        ctx = SessionContext(
            principal=principal,
            integration_connections=(_linear_connection(),),
        )
        c = IntegrationContributor(integration_registry=registry)
        result = await c.contribute(session, ctx)

        assert len(result.values["mcpServers"]) == 1
        server = result.values["mcpServers"][0]
        assert server["name"] == "linear"
        assert server["command"] == "npx"
        assert server["env"] == {}

    async def test_produces_secret_manifest_for_mcp(self, session, principal):
        """MCP integration produces manifest with env mappings."""
        registry = IntegrationRegistry([_linear_definition()])

        ctx = SessionContext(
            principal=principal,
            integration_connections=(_linear_connection(),),
        )
        c = IntegrationContributor(integration_registry=registry)
        result = await c.contribute(session, ctx)

        manifest = result.values["secretManifest"]
        assert "LINEAR_API_KEY" in manifest["env"]
        entry = manifest["env"]["LINEAR_API_KEY"]
        assert entry["file"] == "linear-cred"
        assert entry["key"] == "api_key"

    async def test_no_mcp_spec_skipped(self, session, principal):
        defn = IntegrationDefinition(
            slug="jira",
            name="Jira",
            description="Tracker",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.jira.JiraAdapter",
            icon="jira",
            mcp_server=None,
        )
        registry = IntegrationRegistry([defn])

        conn = IntegrationConnection(
            id="conn-jira",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.jira.JiraAdapter",
            credential_name="jira-cred",
            config={},
            enabled=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            slug="jira",
        )

        ctx = SessionContext(
            principal=principal,
            integration_connections=(conn,),
        )
        c = IntegrationContributor(integration_registry=registry)
        result = await c.contribute(session, ctx)
        assert result.values == {}

    async def test_env_from_credentials_produces_manifest(self, session, principal):
        """Non-MCP integration with env_from_credentials produces secretManifest."""
        defn = IntegrationDefinition(
            slug="anthropic",
            name="Anthropic",
            description="AI provider",
            integration_type="ai_provider",
            adapter="volundr.adapters.outbound.anthropic.AnthropicAdapter",
            icon="anthropic",
            mcp_server=None,
            env_from_credentials={"ANTHROPIC_API_KEY": "api_key"},
        )
        registry = IntegrationRegistry([defn])

        conn = IntegrationConnection(
            id="conn-anthropic",
            owner_id="user-1",
            integration_type="ai_provider",
            adapter="volundr.adapters.outbound.anthropic.AnthropicAdapter",
            credential_name="anthropic-cred",
            config={},
            enabled=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            slug="anthropic",
        )

        ctx = SessionContext(
            principal=principal,
            integration_connections=(conn,),
        )
        c = IntegrationContributor(integration_registry=registry)
        result = await c.contribute(session, ctx)

        assert "secretManifest" in result.values
        manifest = result.values["secretManifest"]
        assert "ANTHROPIC_API_KEY" in manifest["env"]
        entry = manifest["env"]["ANTHROPIC_API_KEY"]
        assert entry["file"] == "anthropic-cred"
        assert entry["key"] == "api_key"

    async def test_file_mounts_in_manifest(self, session, principal):
        """Integration with file_mounts produces files entries in manifest."""
        defn = IntegrationDefinition(
            slug="claude-oauth",
            name="Claude OAuth",
            description="Claude OAuth credentials",
            integration_type="ai_provider",
            adapter="",
            file_mounts={"/home/devrunner/.claude/credentials.json": ""},
        )
        registry = IntegrationRegistry([defn])

        conn = IntegrationConnection(
            id="conn-claude",
            owner_id="user-1",
            integration_type="ai_provider",
            adapter="",
            credential_name="claude-oauth-cred",
            config={},
            enabled=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            slug="claude-oauth",
        )

        ctx = SessionContext(
            principal=principal,
            integration_connections=(conn,),
        )
        c = IntegrationContributor(integration_registry=registry)
        result = await c.contribute(session, ctx)

        manifest = result.values["secretManifest"]
        assert "/home/devrunner/.claude/credentials.json" in manifest["files"]
        entry = manifest["files"]["/home/devrunner/.claude/credentials.json"]
        assert entry["file"] == "claude-oauth-cred"

    async def test_without_principal_still_produces_manifest(self, session):
        """Without a principal, manifest is still produced."""
        registry = IntegrationRegistry([_linear_definition()])

        ctx = SessionContext(
            integration_connections=(_linear_connection(),),
        )
        c = IntegrationContributor(integration_registry=registry)
        result = await c.contribute(session, ctx)

        # MCP server still in values
        assert len(result.values["mcpServers"]) == 1
        assert result.values["mcpServers"][0]["env"] == {}
        # Manifest still produced
        assert "secretManifest" in result.values


async def test_claude_subscription_selects_subscription_auth(session, principal):
    from dataclasses import replace

    registry = IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in Settings().integrations.definitions])
    )
    connection = replace(
        _linear_connection(), slug="claude-code", credential_name="claude-code-credentials"
    )
    result = await IntegrationContributor(integration_registry=registry).contribute(
        session, SessionContext(principal=principal, integration_connections=(connection,))
    )
    assert result.values["envVars"] == [{"name": "SKULD__CLAUDE_AUTH", "value": "subscription"}]
    assert result.values["secretManifest"]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == {
        "file": "claude-code-credentials",
        "key": "token",
    }


async def test_anthropic_api_key_alone_selects_api_key_auth(session, principal):
    """The Claude transports strip API-key variables unless told otherwise, so a
    session whose only Claude credential is a key must be told to keep it."""
    from dataclasses import replace

    registry = IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in Settings().integrations.definitions])
    )
    connection = replace(_linear_connection(), slug="anthropic", credential_name="anthropic-work")
    result = await IntegrationContributor(integration_registry=registry).contribute(
        session, SessionContext(principal=principal, integration_connections=(connection,))
    )
    assert result.values["envVars"] == [{"name": "SKULD__CLAUDE_AUTH", "value": "api_key"}]
    assert result.values["secretManifest"]["env"]["ANTHROPIC_API_KEY"] == {
        "file": "anthropic-work",
        "key": "api_key",
    }


async def test_subscription_wins_when_both_claude_credentials_are_attached(session, principal):
    from dataclasses import replace

    registry = IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in Settings().integrations.definitions])
    )
    key = replace(_linear_connection(), slug="anthropic", credential_name="anthropic-work")
    login = replace(
        _linear_connection(conn_id="conn-claude"),
        slug="claude-code",
        credential_name="claude-code-credentials",
    )
    result = await IntegrationContributor(integration_registry=registry).contribute(
        session, SessionContext(principal=principal, integration_connections=(key, login))
    )
    assert result.values["envVars"] == [{"name": "SKULD__CLAUDE_AUTH", "value": "subscription"}]


def _model_server_definition():
    return IntegrationDefinition(
        slug="model-server",
        name="Model server",
        description="A model you serve yourself, through the gateway",
        integration_type="ai_provider",
        adapter="",
        env_from_config={"SKULD__MODEL_GATEWAY__URL": "gateway_url"},
    )


def _model_server_connection(config):
    return IntegrationConnection(
        id="conn-model-server",
        owner_id="user-1",
        integration_type="ai_provider",
        adapter="",
        credential_name="model-server-local",
        config=config,
        enabled=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        slug="model-server",
    )


async def test_model_server_gateway_url_becomes_session_env(session, principal):
    """The seeded Model server carries the gateway URL in its config; the session
    gets it as SKULD__MODEL_GATEWAY__URL, which routes Claude Code and Codex."""
    registry = IntegrationRegistry([_model_server_definition()])
    conn = _model_server_connection(
        {"provider": "local", "gateway_url": "http://niuu:8080/api/v1/bifrost", "models": ["m"]}
    )
    ctx = SessionContext(principal=principal, integration_connections=(conn,))

    result = await IntegrationContributor(integration_registry=registry).contribute(session, ctx)

    assert {"name": "SKULD__MODEL_GATEWAY__URL", "value": "http://niuu:8080/api/v1/bifrost"} in (
        result.values["envVars"]
    )
    assert "secretManifest" not in result.values


async def test_model_server_without_a_gateway_url_refuses_to_launch(session, principal):
    registry = IntegrationRegistry([_model_server_definition()])
    ctx = SessionContext(
        principal=principal,
        integration_connections=(_model_server_connection({"provider": "local"}),),
    )
    with pytest.raises(ValueError, match="gateway_url"):
        await IntegrationContributor(integration_registry=registry).contribute(session, ctx)


async def test_http_oauth_config_contains_only_file_references(session):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from niuu.domain.oauth_credentials import OAUTH_ENGINE, mcp_token_path

    registry = IntegrationRegistry(
        definitions_from_config(
            [
                {
                    "slug": "linear",
                    "name": "Linear",
                    "integration_type": "issue_tracker",
                    "mcp_server": {
                        "name": "linear",
                        "transport": "http",
                        "url": "https://mcp.linear.app/mcp",
                        "token_field": "token",
                    },
                }
            ]
        )
    )
    store = AsyncMock()
    store.get.return_value = SimpleNamespace(metadata={"renewal_owner": OAUTH_ENGINE})
    contributor = IntegrationContributor(integration_registry=registry, credential_store=store)
    result = await contributor.contribute(
        session, SessionContext(integration_connections=(_linear_connection(),))
    )
    server = result.values["mcpServers"][0]
    assert server == {
        "name": "linear",
        "type": "http",
        "url": "https://mcp.linear.app/mcp",
        "credential_file": mcp_token_path("conn-linear"),
        "credential_format": "oauth",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    }
    store.get_value.assert_not_called()
