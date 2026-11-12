"""Tests for SessionMCPContributor."""

from unittest.mock import MagicMock

import pytest

from volundr.adapters.outbound.contributors.session_mcp import (
    MimirMountConfigError,
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
        with pytest.raises(MimirMountConfigError, match="'shared' has no adapter, url or path"):
            _normalize_instance({"name": "shared"})
        with pytest.raises(MimirMountConfigError, match="has no name"):
            _normalize_instance({"url": "https://mimir.example"})

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
                ],
                "ephemeral_locals": [
                    {"mount_name": "scratch", "categories": ["draft"]},
                    {"mountName": "scratch-2"},
                ],
                "bindings": [
                    {
                        "mount_name": "registry-a",
                        "access": "read_write",
                        "write_prefixes": ["docs/"],
                    },
                    {"mountName": "scratch", "access": "write", "writePrefixes": ["drafts/"]},
                    {"mount_name": "registry-b", "access": "read"},
                ],
                "write_routing": {
                    "rules": [{"prefix": "reviews/", "mounts": ["registry-b"]}],
                    "default": ["registry-b"],
                },
                "default_mounts": ["scratch"],
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

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            ({"instances": ["bad"]}, r"instances\[0\] must be an object"),
            (
                {"instances": [{"name": "a", "path": "/a"}, {"name": "a", "path": "/b"}]},
                "'a' twice",
            ),
            ({"instances": [{"url": "https://x"}]}, "has no name"),
            ({"instances": [{"name": "a"}]}, "'a' has no adapter, url or path"),
            ({"registry_refs": ["bad"]}, r"registry_refs\[0\] must be an object"),
            ({"registry_refs": [{"role": "shared"}]}, r"registry_refs\[0\] names no mount"),
            (
                {"registry_refs": [{"mount_name": "a", "path": "/a"}] * 2},
                "'a' is configured twice",
            ),
            (
                {"registry_refs": [{"mount_name": "a", "registry_entry_id": "e"}]},
                "'a' has no adapter, url or path.*registry entry must exist",
            ),
            ({"ephemeral_locals": ["bad"]}, r"ephemeral_locals\[0\] must be an object"),
            ({"ephemeral_locals": [{"categories": []}]}, r"ephemeral_locals\[0\] names no mount"),
            (
                {
                    "instances": [{"name": "a", "path": "/a"}],
                    "ephemeral_locals": [{"mount_name": "a"}],
                },
                "'a' is configured twice",
            ),
            ({"bindings": ["bad"]}, r"bindings\[0\] must be an object"),
            (
                {"instances": [{"name": "a", "path": "/a"}], "bindings": [{"mount_name": "b"}]},
                r"bindings\[0\] is for the mount 'b'.*mounted: a",
            ),
            ({"write_routing": ["bad"]}, "write_routing must be an object"),
            ({"write_routing": {"rules": ["bad"]}}, r"rules\[0\] must be an object"),
            ({"write_routing": {"rules": [{"prefix": "x/"}]}}, "needs a prefix and at least one"),
            (
                {
                    "instances": [{"name": "a", "path": "/a"}],
                    "write_routing": {"rules": [{"prefix": "x/", "mounts": ["a", "ghost"]}]},
                },
                r"rules\[0\].mounts names 'ghost'",
            ),
            (
                {"instances": [{"name": "a", "path": "/a"}], "write_routing": {"default": ["g"]}},
                "write_routing.default names 'g'",
            ),
            (
                {"instances": [{"name": "a", "path": "/a"}], "default_mounts": ["g"]},
                "default_mounts names 'g'",
            ),
        ],
    )
    def test_a_mount_configuration_that_cannot_launch_raises_with_the_remedy(self, config, message):
        with pytest.raises(MimirMountConfigError, match=message):
            _resolve_mimir_runtime(config)

    async def test_the_contributor_fails_the_launch_instead_of_dropping_a_mount(self, session):
        contributor = SessionMCPContributor()
        context = SessionContext(
            workload_config={
                "mimir": {"registry_refs": [{"mount_name": "lost", "registry_entry_id": "gone"}]}
            }
        )
        with pytest.raises(MimirMountConfigError, match="'lost'"):
            await contributor.contribute(session, context)

    def test_resolve_mimir_runtime_adds_default_hosted_instance_when_no_registry_refs(self):
        instances, routing = _resolve_mimir_runtime({"hosted_url": "https://hosted.example"})

        assert [instance["name"] for instance in instances] == ["hosted"]
        assert {"prefix": "project/", "mounts": ["hosted"]} in routing["rules"]
        assert {"prefix": "entity/", "mounts": ["hosted"]} in routing["rules"]
        assert routing["default"] == ["hosted"]


def _enforced(name, access, prefixes=(), **ref):
    return {
        "registry_refs": [{"mount_name": name, **ref}],
        "bindings": [
            {
                "mount_name": name,
                "access": access,
                "write_prefixes": list(prefixes),
                "enforce": True,
            }
        ],
    }


class TestEnforcedAccessLimits:
    def test_a_read_only_path_mount_is_started_read_only(self):
        instances, _ = _resolve_mimir_runtime(_enforced("a", "read", path="/m/a"))
        assert instances[0]["read_only"] is True

    def test_write_prefixes_are_held_and_unioned(self):
        config = _enforced("a", "read_write", ["x/"], path="/m/a")
        config["bindings"].append(
            {"mount_name": "a", "access": "read_write", "write_prefixes": ["y/"], "enforce": True}
        )
        instances, _ = _resolve_mimir_runtime(config)
        assert instances[0]["write_prefixes"] == ["x/", "y/"]
        assert "read_only" not in instances[0]

    def test_unrestricted_write_access_holds_nothing(self):
        instances, _ = _resolve_mimir_runtime(_enforced("a", "read_write", url="https://m"))
        assert "read_only" not in instances[0] and "write_prefixes" not in instances[0]

    def test_a_binding_that_only_describes_holds_nothing(self):
        config = _enforced("a", "read", url="https://m")
        config["bindings"][0].pop("enforce")
        instances, _ = _resolve_mimir_runtime(config)
        assert "read_only" not in instances[0]

    @pytest.mark.parametrize(("access", "prefixes"), [("read", ()), ("read_write", ("x/",))])
    def test_a_limit_on_a_mount_served_elsewhere_raises(self, access, prefixes):
        with pytest.raises(MimirMountConfigError, match="'a' limits writes.*cannot be held"):
            _resolve_mimir_runtime(_enforced("a", access, prefixes, url="https://m"))

    def test_read_only_and_prefixes_on_one_mount_raise(self):
        config = _enforced("a", "read", path="/m/a")
        config["bindings"].append(
            {"mount_name": "a", "access": "read_write", "write_prefixes": ["x/"], "enforce": True}
        )
        with pytest.raises(MimirMountConfigError, match="both read-only and write-limited"):
            _resolve_mimir_runtime(config)
        config["bindings"].reverse()
        with pytest.raises(MimirMountConfigError, match="both read-only and write-limited"):
            _resolve_mimir_runtime(config)

    async def test_the_server_is_started_with_the_flags(self, session):
        contributor = SessionMCPContributor()
        config = _enforced("a", "read", path="/m/a")
        config["registry_refs"].append({"mount_name": "b", "path": "/m/b"})
        config["bindings"].append(
            {"mount_name": "b", "access": "read_write", "write_prefixes": ["x/"], "enforce": True}
        )
        result = await contributor.contribute(
            session, SessionContext(workload_config={"mimir": config})
        )
        args = {s["name"]: s["args"] for s in result.values["mcpServers"]}
        assert args["mimir-a"][-1] == "--read-only"
        assert args["mimir-b"][-2:] == ["--write-prefix", "x/"]

    async def test_an_adapter_only_mount_has_no_endpoint_and_raises(self, session):
        config = {
            "registry_refs": [
                {"mount_name": "g", "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter"}
            ]
        }
        with pytest.raises(MimirMountConfigError, match="'g' is reached through an adapter"):
            await SessionMCPContributor().contribute(
                session, SessionContext(workload_config={"mimir": config})
            )
