"""Tests for CoreSessionContributor."""

import pytest

from volundr.adapters.outbound.contributors.core import CoreSessionContributor
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext


@pytest.fixture
def session():
    return Session(name="test-session", model="claude-sonnet-4-20250514", source=GitSource())


class TestCoreSessionContributor:
    async def test_name(self):
        c = CoreSessionContributor(base_domain="example.com")
        assert c.name == "core"

    async def test_basic_values(self, session):
        c = CoreSessionContributor(base_domain="example.com")
        result = await c.contribute(session, SessionContext())
        assert result.values["session"]["id"] == str(session.id)
        assert result.values["session"]["name"] == "test-session"
        assert result.values["session"]["model"] == "claude-sonnet-4-20250514"
        assert result.values["ingress"]["host"] == "test-session.example.com"

    async def test_terminal_restricted(self, session):
        c = CoreSessionContributor(base_domain="example.com")
        ctx = SessionContext(terminal_restricted=True)
        result = await c.contribute(session, ctx)
        assert result.values["localServices"]["terminal"]["restricted"] is True

    async def test_terminal_not_restricted(self, session):
        c = CoreSessionContributor(base_domain="example.com")
        ctx = SessionContext(terminal_restricted=False)
        result = await c.contribute(session, ctx)
        assert "localServices" not in result.values

    async def test_projects_safe_a2a_directory_metadata(self, session):
        session.owner_id = "owner-1"
        session.tenant_id = "tenant-1"
        result = await CoreSessionContributor(base_domain="example.com").contribute(
            session,
            SessionContext(
                workload_config={
                    "a2a_card_url": "https://agent.example/card.json",
                    "a2a_endpoint_url": "https://agent.example/a2a",
                    "environment_id": "production",
                    "a2a_visibility": "tenant",
                    "secret": "must-not-leak",
                }
            ),
        )

        assert result.values["session"] == {
            "id": str(session.id),
            "name": "test-session",
            "model": "claude-sonnet-4-20250514",
            "a2aCardUrl": "https://agent.example/card.json",
            "a2aEndpointUrl": "https://agent.example/a2a",
            "environmentId": "production",
            "a2aVisibility": "tenant",
            "ownerId": "owner-1",
            "tenantId": "tenant-1",
        }

    async def test_pod_spec_is_none(self, session):
        c = CoreSessionContributor(base_domain="example.com")
        result = await c.contribute(session, SessionContext())
        assert result.pod_spec is None

    async def test_extra_kwargs_ignored(self):
        c = CoreSessionContributor(
            base_domain="example.com",
            gateway=None,
            storage=None,
            unknown_kwarg="ignored",
        )
        assert c.name == "core"
