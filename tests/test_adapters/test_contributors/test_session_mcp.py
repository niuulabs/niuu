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
                            "url": "https://mimir.example.test/shared",
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
        assert result.values["mcpServers"][0] == {
            "name": "mimir-shared-knowledge",
            "type": "http",
            "url": "https://mimir.example.test/shared",
            "description": (
                "Mimir knowledge mount 'shared-knowledge' "
                "(role: shared; categories: project, decision)"
            ),
        }
        assert result.pod_spec is None

    async def test_a_directory_mount_is_refused(self, session):
        context = SessionContext(
            workload_type="session",
            workload_config={
                "mimir": {"registry_refs": [{"mount_name": "wiki", "path": "/tmp/mimir/wiki"}]}
            },
        )

        with pytest.raises(MimirMountConfigError, match="'wiki' has no adapter or url") as raised:
            await SessionMCPContributor().contribute(session, context)
        assert "path" not in str(raised.value)

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
                        {"mount_name": "shared", "url": "https://shared.example"},
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
        assert _normalize_instance({"name": "shared", "url": "https://mimir.example"}) == {
            "name": "shared",
            "role": "shared",
            "url": "https://mimir.example",
        }
        with pytest.raises(MimirMountConfigError, match="'shared' has no adapter or url"):
            _normalize_instance({"name": "shared"})
        with pytest.raises(MimirMountConfigError, match="'shared' has no adapter or url"):
            _normalize_instance({"name": "shared", "path": "/tmp/shared"})
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
                    {
                        "name": "scratch",
                        "url": "https://scratch.example",
                        "role": "local",
                        "categories": ["draft"],
                    },
                    {"name": "scratch-2", "url": "https://scratch-2.example", "role": "local"},
                ],
                "registry_refs": [
                    {
                        "mount_name": "registry-a",
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
            "scratch",
            "scratch-2",
            "registry-a",
            "registry-b",
            "hosted-backed",
        ]
        assert {"prefix": "self/", "mounts": ["scratch"]} in routing["rules"]
        assert {"prefix": "docs/", "mounts": ["registry-a"]} in routing["rules"]
        assert {"prefix": "drafts/", "mounts": ["scratch"]} in routing["rules"]
        assert {"prefix": "reviews/", "mounts": ["registry-b"]} in routing["rules"]
        assert routing["default"] == ["scratch"]
        registry_a = next(instance for instance in instances if instance["name"] == "registry-a")
        assert registry_a["url"] == "https://registry-a.example"

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            ({"instances": ["bad"]}, r"instances\[0\] must be an object"),
            (
                {
                    "instances": [
                        {"name": "a", "url": "https://a.example"},
                        {"name": "a", "url": "https://b.example"},
                    ]
                },
                "'a' twice",
            ),
            ({"instances": [{"url": "https://x"}]}, "has no name"),
            ({"instances": [{"name": "a"}]}, "'a' has no adapter or url"),
            ({"registry_refs": ["bad"]}, r"registry_refs\[0\] must be an object"),
            ({"registry_refs": [{"role": "shared"}]}, r"registry_refs\[0\] names no mount"),
            (
                {"registry_refs": [{"mount_name": "a", "url": "https://a.example"}] * 2},
                "'a' is configured twice",
            ),
            (
                {"registry_refs": [{"mount_name": "a", "registry_entry_id": "e"}]},
                "'a' has no adapter or url.*registry entry must exist",
            ),
            ({"ephemeral_locals": ["bad"]}, r"ephemeral_locals\[0\] must be an object"),
            ({"ephemeral_locals": [{"categories": []}]}, r"ephemeral_locals\[0\] names no mount"),
            (
                {"ephemeral_locals": [{"mount_name": "scratch"}]},
                "scratch mount 'scratch'.*no longer served",
            ),
            (
                {
                    "instances": [{"name": "a", "url": "https://a.example"}],
                    "ephemeral_locals": [{"mount_name": "a"}],
                },
                "'a' is configured twice",
            ),
            ({"bindings": ["bad"]}, r"bindings\[0\] must be an object"),
            (
                {
                    "instances": [{"name": "a", "url": "https://a.example"}],
                    "bindings": [{"mount_name": "b"}],
                },
                r"bindings\[0\] is for the mount 'b'.*mounted: a",
            ),
            ({"write_routing": ["bad"]}, "write_routing must be an object"),
            ({"write_routing": {"rules": ["bad"]}}, r"rules\[0\] must be an object"),
            ({"write_routing": {"rules": [{"prefix": "x/"}]}}, "needs a prefix and at least one"),
            (
                {
                    "instances": [{"name": "a", "url": "https://a.example"}],
                    "write_routing": {"rules": [{"prefix": "x/", "mounts": ["a", "ghost"]}]},
                },
                r"rules\[0\].mounts names 'ghost'",
            ),
            (
                {
                    "instances": [{"name": "a", "url": "https://a.example"}],
                    "write_routing": {"default": ["g"]},
                },
                "write_routing.default names 'g'",
            ),
            (
                {"instances": [{"name": "a", "url": "https://a.example"}], "default_mounts": ["g"]},
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
    def test_unrestricted_write_access_holds_nothing(self):
        instances, _ = _resolve_mimir_runtime(_enforced("a", "read_write", url="https://m"))
        assert "read_only" not in instances[0] and "write_prefixes" not in instances[0]

    def test_a_binding_that_only_describes_holds_nothing(self):
        config = _enforced("a", "read", url="https://m")
        config["bindings"][0].pop("enforce")
        instances, _ = _resolve_mimir_runtime(config)
        assert "read_only" not in instances[0]

    @pytest.mark.parametrize(("access", "prefixes"), [("read", ()), ("read_write", ("x/",))])
    def test_a_limit_a_session_cannot_hold_raises(self, access, prefixes):
        with pytest.raises(MimirMountConfigError, match="'a' limits writes.*memory gateway"):
            _resolve_mimir_runtime(_enforced("a", access, prefixes, url="https://m"))

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


GATEWAY = "http://127.0.0.1:8080/api/v1/mimir"


class TestWithTheMemoryGateway:
    """With memory.gateway_url the gateway holds the limits; no per-mount server is started."""

    def _context(self, mimir: dict) -> SessionContext:
        return SessionContext(workload_config={"mimir": mimir})

    async def test_no_per_mount_server_is_emitted(self, session):
        mimir = {
            "registry_refs": [
                {"mount_name": "a", "registry_entry_id": "e1", "url": "https://a.example"},
                {"mount_name": "b", "registry_entry_id": "e2", "url": "https://b.example"},
            ]
        }

        result = await SessionMCPContributor(memory_gateway_url=GATEWAY).contribute(
            session, self._context(mimir)
        )

        assert result.values == {}
        # Without the gateway the same config starts one server per mount.
        without = await SessionMCPContributor().contribute(session, self._context(mimir))
        assert [s["name"] for s in without.values["mcpServers"]] == ["mimir-a", "mimir-b"]

    @pytest.mark.parametrize(("access", "prefixes"), [("read", ()), ("read_write", ("x/",))])
    async def test_a_restricted_mount_is_not_refused(self, session, access, prefixes):
        mimir = _enforced("a", access, prefixes, registry_entry_id="e1", url="https://m")

        result = await SessionMCPContributor(memory_gateway_url=GATEWAY).contribute(
            session, self._context(mimir)
        )

        assert result.values == {}
        with pytest.raises(MimirMountConfigError, match="'a' limits writes.*memory gateway"):
            await SessionMCPContributor().contribute(session, self._context(mimir))

    async def test_an_adapter_only_mount_needs_no_endpoint(self, session):
        mimir = {
            "registry_refs": [
                {
                    "mount_name": "g",
                    "registry_entry_id": "e1",
                    "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
                }
            ]
        }

        result = await SessionMCPContributor(memory_gateway_url=GATEWAY).contribute(
            session, self._context(mimir)
        )

        assert result.values == {}

    @pytest.mark.parametrize(
        ("mimir", "message"),
        [
            ({"instances": [{"name": "a", "url": "https://a"}]}, "instances.*registry_refs"),
            ({"hosted_url": "https://h"}, "hosted_url.*registry_refs"),
            ({"registry_refs": [{"mount_name": "a", "url": "https://a"}]}, "registry_entry_id"),
            ({"ephemeral_locals": [{"mount_name": "s"}]}, "ephemeral_locals"),
        ],
    )
    async def test_a_mount_the_gateway_could_not_open_is_refused_not_dropped(
        self, session, mimir, message
    ):
        with pytest.raises(MimirMountConfigError, match=message):
            await SessionMCPContributor(memory_gateway_url=GATEWAY).contribute(
                session, self._context(mimir)
            )

    async def test_a_session_without_mimir_config_is_untouched(self, session):
        result = await SessionMCPContributor(memory_gateway_url=GATEWAY).contribute(
            session, SessionContext()
        )

        assert result.values == {}
