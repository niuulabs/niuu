"""Tests for SessionMCPContributor."""

from unittest.mock import MagicMock

import pytest

from volundr.adapters.outbound.contributors.session_mcp import SessionMCPContributor
from volundr.domain.models import GitSource, LaunchSpec, Session
from volundr.domain.ports import SessionContext


@pytest.fixture
def session():
    return Session(name="test", model="claude", source=GitSource(repo="", branch="main"))


class TestSessionMCPContributor:
    async def test_name(self):
        contributor = SessionMCPContributor()
        assert contributor.name == "session_mcp"

    async def test_no_workload_returns_empty(self, session):
        contributor = SessionMCPContributor()
        result = await contributor.contribute(session, SessionContext())
        assert result.values == {}
        assert result.pod_spec is None

    async def test_converts_attached_mimir_into_mcp_servers(self, session):
        contributor = SessionMCPContributor()
        context = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "mimir": {
                    "registry_refs": [
                        {
                            "mount_name": "shared-knowledge",
                            "path": "/tmp/mimir/shared",
                            "role": "shared",
                            "categories": ["project", "decision"],
                        }
                    ]
                }
            },
        )

        result = await contributor.contribute(session, context)

        assert [server["name"] for server in result.values["mcpServers"]] == [
            "mimir-shared-knowledge",
        ]
        assert result.values["mcpServers"][0]["args"] == [
            "-m",
            "mimir",
            "mcp",
            "--path",
            "/tmp/mimir/shared",
            "--name",
            "shared-knowledge",
        ]
        assert result.pod_spec is None

    async def test_url_backed_mimir_becomes_http_mcp_server(self, session):
        contributor = SessionMCPContributor()
        context = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "mimir": {
                    "registry_refs": [
                        {
                            "mount_name": "shared-api",
                            "url": "https://mimir.example.test",
                        }
                    ]
                }
            },
        )

        result = await contributor.contribute(session, context)

        assert result.values["mcpServers"][0] == {
            "name": "mimir-shared-api",
            "type": "http",
            "url": "https://mimir.example.test",
            "description": "Mimir knowledge mount 'shared-api' (role: shared)",
        }

    async def test_resolves_template_workload_when_context_is_empty(self, session):
        template = LaunchSpec(
            name="flock-template",
            workload_type="ravn_flock",
            workload_config={
                "mimir": {
                    "registry_refs": [
                        {"mount_name": "shared", "path": "/tmp/shared"},
                    ]
                }
            },
        )
        template_provider = MagicMock()
        template_provider.get.return_value = template
        contributor = SessionMCPContributor(launch_spec_provider=template_provider)

        result = await contributor.contribute(
            session,
            SessionContext(launch_spec="flock-template"),
        )

        assert len(result.values["mcpServers"]) == 1
