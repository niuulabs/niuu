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
        "envVars": [{"name": "FEATURE", "value": "enabled"}],
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


async def test_launch_spec_env_survives_a_contributor_that_also_sets_env(
    session: Session,
) -> None:
    """A launch spec's env must not be erased by another contributor's env.

    The integrations contributor always emits at least SKULD__CLAUDE_AUTH, and
    plain assignment used to let that discard everything the launch spec set.
    """
    from volundr.domain.models import SessionSpec
    from volundr.domain.ports import SessionContribution

    provider = MagicMock()
    provider.get.return_value = _spec()
    contributor = LaunchSpecContributor(launch_spec_provider=provider)

    from_launch_spec = await contributor.contribute(
        session, SessionContext(launch_spec="standard")
    )
    from_integrations = SessionContribution(
        values={"envVars": [{"name": "SKULD__CLAUDE_AUTH", "value": "subscription"}]}
    )

    merged = SessionSpec.merge([from_launch_spec, from_integrations])
    names = {entry["name"] for entry in merged.values["envVars"]}

    assert names == {"FEATURE", "SKULD__CLAUDE_AUTH"}


async def test_later_contributor_overrides_same_env_var_by_name(
    session: Session,
) -> None:
    from volundr.domain.models import SessionSpec
    from volundr.domain.ports import SessionContribution

    merged = SessionSpec.merge(
        [
            SessionContribution(values={"envVars": [{"name": "A", "value": "first"}]}),
            SessionContribution(values={"envVars": [{"name": "A", "value": "second"}]}),
        ]
    )

    assert merged.values["envVars"] == [{"name": "A", "value": "second"}]
