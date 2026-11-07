"""Tests for SessionMCPContributor."""

from unittest.mock import MagicMock

import pytest

from volundr.adapters.outbound.contributors.session_mcp import (
    SessionMCPContributor,
    _normalize_instance,
    _normalize_mimir_workload_config,
    _resolve_mimir_runtime,
    _string_list,
)
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
            workload_type="session",
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
            workload_type="session",
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
            name="mimir-template",
            workload_type="session",
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
            SessionContext(launch_spec="mimir-template"),
        )

        assert len(result.values["mcpServers"]) == 1


class TestMimirHelpers:
    def test_string_list_helper(self):
        assert _string_list([" a ", "", None, "b"]) == ["a", "None", "b"]
        assert _string_list("bad") == []

    def test_normalize_instance_and_workload_config_helpers(self):
        assert _normalize_instance({"name": "shared", "path": "/tmp/shared"}) == {
            "name": "shared",
            "role": "shared",
            "path": "/tmp/shared",
        }
        assert _normalize_instance({"name": "shared", "url": "https://mimir.example"}) == {
            "name": "shared",
            "role": "shared",
            "url": "https://mimir.example",
        }
        assert _normalize_instance({"name": "shared"}) is None
        assert _normalize_instance({"url": "https://mimir.example"}) is None

        assert _normalize_mimir_workload_config({}, "https://legacy.example") == {
            "hosted_url": "https://legacy.example"
        }
        assert _normalize_mimir_workload_config(
            {"hosted_url": "https://explicit.example"},
            "https://legacy.example",
        ) == {"hosted_url": "https://explicit.example"}

    def test_resolve_mimir_runtime_supports_hosted_registry_ephemeral_and_explicit_routing(self):
        instances, routing = _resolve_mimir_runtime(
            {
                "hosted_url": "https://hosted.example",
                "instances": [
                    {"name": "shared", "url": "https://shared.example", "categories": ["entity"]},
                    {"name": "shared", "url": "https://duplicate.example"},
                    {"url": "https://invalid.example"},
                    "bad",
                ],
                "registry_refs": [
                    {
                        "mount_name": "registry-a",
                        "path": "/mnt/registry-a",
                        "url": "https://registry-a.example",
                        "role": "shared",
                        "categories": ["directive"],
                    },
                    {
                        "registryEntryId": "registry-b",
                        "url": "https://registry-b.example",
                    },
                    {
                        "mount_name": "hosted-backed",
                        "role": "shared",
                    },
                    {"mount_name": "registry-a", "url": "https://duplicate.example"},
                    "bad",
                ],
                "ephemeral_locals": [
                    {"mount_name": "scratch", "categories": ["draft"]},
                    {"mountName": "scratch-2"},
                    {"mount_name": "scratch"},
                    "bad",
                ],
                "bindings": [
                    {
                        "mount_name": "registry-a",
                        "access": "read_write",
                        "write_prefixes": ["docs/"],
                    },
                    {"mountName": "scratch", "access": "write", "writePrefixes": ["drafts/"]},
                    {"mount_name": "missing", "access": "write", "write_prefixes": ["ignored/"]},
                    "bad",
                ],
                "write_routing": {
                    "rules": [
                        {"prefix": "reviews/", "mounts": ["registry-b", "missing"]},
                        {"prefix": "ignored/", "mounts": []},
                        "bad",
                    ],
                    "default": ["registry-b", "missing"],
                },
                "default_mounts": ["scratch", "missing"],
            }
        )

        assert [instance["name"] for instance in instances] == [
            "shared",
            "registry-a",
            "registry-b",
            "hosted-backed",
            "scratch",
            "scratch-2",
        ]
        assert {"prefix": "self/", "mounts": ["scratch"]} in routing["rules"]
        assert {"prefix": "docs/", "mounts": ["registry-a"]} in routing["rules"]
        assert {"prefix": "drafts/", "mounts": ["scratch"]} in routing["rules"]
        assert {"prefix": "reviews/", "mounts": ["registry-b"]} in routing["rules"]
        assert routing["default"] == ["scratch"]
        registry_a = next(instance for instance in instances if instance["name"] == "registry-a")
        assert registry_a["url"] == "https://registry-a.example"
        assert "path" not in registry_a

    def test_resolve_mimir_runtime_adds_default_hosted_instance_when_no_registry_refs(self):
        instances, routing = _resolve_mimir_runtime({"hosted_url": "https://hosted.example"})

        assert [instance["name"] for instance in instances] == ["hosted"]
        assert {"prefix": "project/", "mounts": ["hosted"]} in routing["rules"]
        assert {"prefix": "entity/", "mounts": ["hosted"]} in routing["rules"]
        assert routing["default"] == ["hosted"]
