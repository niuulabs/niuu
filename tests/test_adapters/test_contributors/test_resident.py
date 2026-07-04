"""Tests for ResidentContributor (flock-of-one resident sessions)."""

from unittest.mock import MagicMock

import pytest
import yaml

from volundr.adapters.outbound.contributors.resident import ResidentContributor
from volundr.domain.models import GitSource, LaunchSpec, Session
from volundr.domain.ports import SessionContext

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _extract_mounted_config(pod_spec, persona: str) -> str:
    init_name = f"write-ravn-cfg-{persona}"
    for ic in pod_spec.init_containers:
        if ic["name"] == init_name:
            cmd = ic["command"][2]
            open_marker = "'__RAVN_EOF__'\n"
            close_marker = "__RAVN_EOF__\n"
            start = cmd.index(open_marker) + len(open_marker)
            end = cmd.rindex(close_marker)
            return cmd[start:end]
    raise AssertionError(f"init container {init_name!r} not found")


def _env_value(pod_spec, name: str) -> str | None:
    for entry in pod_spec.env:
        if entry.get("name") == name:
            return entry.get("value")
    return None


@pytest.fixture
def session():
    return Session(name="test-resident", model="claude-opus-4-8", source=GitSource())


def _ctx(workload_config: dict) -> SessionContext:
    return SessionContext(workload_type="resident", workload_config=workload_config)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestResidentRouting:
    def test_name(self):
        assert ResidentContributor().name == "resident"

    async def test_session_workload_type_returns_empty(self, session):
        template = LaunchSpec(name="default", workload_type="session", workload_config={})
        provider = MagicMock()
        provider.get.return_value = template
        c = ResidentContributor(launch_spec_provider=provider)
        result = await c.contribute(session, SessionContext(launch_spec="default"))
        assert result.values == {}
        assert result.pod_spec is None

    async def test_ravn_flock_workload_type_returns_empty(self, session):
        template = LaunchSpec(
            name="flock",
            workload_type="ravn_flock",
            workload_config={"personas": ["coordinator"]},
        )
        provider = MagicMock()
        provider.get.return_value = template
        c = ResidentContributor(launch_spec_provider=provider)
        result = await c.contribute(session, SessionContext(launch_spec="flock"))
        assert result.values == {}
        assert result.pod_spec is None

    async def test_missing_persona_fails_loudly(self, session):
        c = ResidentContributor()
        with pytest.raises(ValueError, match="persona"):
            await c.contribute(session, _ctx({"resident_name": "Muninn"}))


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


class TestResidentOutput:
    async def test_single_ravn_container(self, session):
        c = ResidentContributor()
        result = await c.contribute(session, _ctx({"persona": "product-steward"}))
        containers = [
            ct
            for ct in result.pod_spec.extra_containers
            if ct.get("name", "").startswith("ravn-")
        ]
        assert [ct["name"] for ct in containers] == ["ravn-product-steward"]

    async def test_room_default_target_env(self, session):
        c = ResidentContributor()
        result = await c.contribute(session, _ctx({"persona": "product-steward"}))
        assert (
            _env_value(result.pod_spec, "SKULD__ROOM__DEFAULT_TARGET_PEER_ID")
            == "flock-product-steward"
        )
        assert _env_value(result.pod_spec, "SKULD__ROOM__ENABLED") == "true"

    async def test_no_workflow_trigger_env(self, session):
        c = ResidentContributor()
        result = await c.contribute(session, _ctx({"persona": "product-steward"}))
        assert _env_value(result.pod_spec, "SKULD__WORKFLOW_TRIGGER__ENABLED") is None

    async def test_resident_values_identity(self, session):
        c = ResidentContributor()
        result = await c.contribute(
            session,
            _ctx({"persona": "product-steward", "resident_name": "Muninn"}),
        )
        assert result.values["resident"] == {
            "name": "Muninn",
            "peer_id": "flock-product-steward",
            "persona": "product-steward",
        }

    async def test_resident_name_defaults_to_persona(self, session):
        c = ResidentContributor()
        result = await c.contribute(session, _ctx({"persona": "product-steward"}))
        assert result.values["resident"]["name"] == "product-steward"


# ---------------------------------------------------------------------------
# Ravn node config overlay
# ---------------------------------------------------------------------------


class TestResidentRavnConfig:
    async def test_skuld_channel_enabled(self, session):
        c = ResidentContributor()
        result = await c.contribute(
            session,
            _ctx({"persona": "product-steward", "resident_name": "Muninn"}),
        )
        cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "product-steward"))
        assert cfg["skuld"]["enabled"] is True
        assert cfg["skuld"]["broker_url"] == "ws://127.0.0.1:8081/ws/ravn"
        assert cfg["skuld"]["display_name"] == "Muninn"

    async def test_environment_resident_name(self, session):
        c = ResidentContributor()
        result = await c.contribute(
            session,
            _ctx({"persona": "product-steward", "resident_name": "Muninn"}),
        )
        cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "product-steward"))
        assert cfg["environment"]["resident_name"] == "Muninn"

    async def test_no_workflow_section(self, session):
        c = ResidentContributor()
        result = await c.contribute(session, _ctx({"persona": "product-steward"}))
        cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "product-steward"))
        assert "workflow" not in cfg

    async def test_platform_passthrough_merges_into_gateway(self, session):
        c = ResidentContributor()
        result = await c.contribute(
            session,
            _ctx(
                {
                    "persona": "product-steward",
                    "platform": {"enabled": True, "base_url": "http://volundr:8080"},
                }
            ),
        )
        cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "product-steward"))
        # Deep merge keeps the flock-generated gateway channels intact.
        assert cfg["gateway"]["platform"]["enabled"] is True
        assert cfg["gateway"]["platform"]["base_url"] == "http://volundr:8080"
        assert cfg["gateway"]["channels"]["http"]["enabled"] is True

    async def test_persona_overrides_flow_through(self, session):
        c = ResidentContributor()
        result = await c.contribute(
            session,
            _ctx(
                {
                    "persona": "product-steward",
                    "system_prompt_extra": "Curate projects/ pages in Mimir.",
                    "iteration_budget": 25,
                }
            ),
        )
        cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "product-steward"))
        po = cfg["persona_overrides"]
        assert po["system_prompt_extra"] == "Curate projects/ pages in Mimir."
        assert po["iteration_budget"] == 25

    async def test_mimir_and_sleipnir_flow_through(self, session):
        c = ResidentContributor()
        result = await c.contribute(
            session,
            _ctx(
                {
                    "persona": "product-steward",
                    "mimir": {"hosted_url": "https://mimir.internal/api/v1"},
                    "sleipnir_publish_urls": ["http://volundr:8000/sleipnir/events"],
                }
            ),
        )
        cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "product-steward"))
        assert cfg["mimir"]["enabled"] is True
        assert cfg["sleipnir"]["webhook"]["publish_urls"] == [
            "http://volundr:8000/sleipnir/events"
        ]
        assert result.values["sleipnir"]["publishUrls"] == [
            "http://volundr:8000/sleipnir/events"
        ]
