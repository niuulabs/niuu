"""Tests for LaunchSpecContributor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from volundr.adapters.outbound.contributors.launch_spec import LaunchSpecContributor
from volundr.domain.models import GitSource, LaunchSpec, Session
from volundr.domain.ports import SessionContext


@pytest.fixture
def session() -> Session:
    return Session(name="test", model="claude", source=GitSource(repo="", branch="main"))


def _spec(name: str = "standard") -> LaunchSpec:
    return LaunchSpec(
        name=name,
        resource_config={"requests": {"cpu": "2"}},
        env_vars={"FEATURE": "enabled"},
        env_secret_refs=["skuld-runtime-secret"],
        mcp_servers=[{"name": "mimir", "type": "stdio"}],
        system_prompt="Stay sharp.",
        workload_config={"skuld": {"permissionMode": "acceptEdits"}},
    )


async def test_no_provider_returns_empty_contribution(session: Session) -> None:
    contributor = LaunchSpecContributor()

    result = await contributor.contribute(session, SessionContext(launch_spec="standard"))

    assert contributor.name == "launch_spec"
    assert result.values == {}
    assert result.pod_spec is None


async def test_explicit_launch_spec_merges_runtime_values(session: Session) -> None:
    provider = MagicMock()
    provider.get.return_value = _spec()
    contributor = LaunchSpecContributor(launch_spec_provider=provider)

    result = await contributor.contribute(session, SessionContext(launch_spec="standard"))

    assert result.values == {
        "resources": {"requests": {"cpu": "2"}},
        "env": {"FEATURE": "enabled"},
        "envSecretRefs": ["skuld-runtime-secret"],
        "mcpServers": [{"name": "mimir", "type": "stdio"}],
        "session": {"systemPrompt": "Stay sharp."},
        "skuld": {"permissionMode": "acceptEdits"},
    }
    provider.get.assert_called_once_with("standard")
    provider.get_default.assert_not_called()


async def test_missing_launch_spec_falls_back_to_default(session: Session) -> None:
    default = _spec("default")
    provider = MagicMock()
    provider.get.return_value = None
    provider.get_default.return_value = default
    contributor = LaunchSpecContributor(launch_spec_provider=provider)

    result = await contributor.contribute(session, SessionContext(launch_spec="missing"))

    assert result.values["resources"] == {"requests": {"cpu": "2"}}
    provider.get.assert_called_once_with("missing")
    provider.get_default.assert_called_once_with("session")


async def test_default_absent_returns_empty_contribution(session: Session) -> None:
    provider = MagicMock()
    provider.get_default.return_value = None
    contributor = LaunchSpecContributor(launch_spec_provider=provider)

    result = await contributor.contribute(session, SessionContext())

    assert result.values == {}
