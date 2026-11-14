"""Tests for IsolationContributor."""

from uuid import uuid4

import pytest

from volundr.adapters.outbound.contributors.isolation import IsolationContributor
from volundr.domain.models import LABEL_OWNER, LABEL_SESSION_ID, GitSource, Session
from volundr.domain.ports import SessionContext


@pytest.fixture
def session():
    return Session(
        name="test",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id="user-1",
    )


class TestIsolationContributor:
    async def test_name(self):
        c = IsolationContributor()
        assert c.name == "isolation"

    async def test_labels_with_owner(self, session):
        c = IsolationContributor()
        result = await c.contribute(session, SessionContext())
        labels = result.values["podLabels"]
        assert labels[LABEL_SESSION_ID] == str(session.id)
        assert labels[LABEL_OWNER] == "user-1"

    async def test_labels_without_owner(self):
        session = Session(name="test", model="claude", source=GitSource(repo="", branch="main"))
        c = IsolationContributor()
        result = await c.contribute(session, SessionContext())
        labels = result.values["podLabels"]
        assert labels[LABEL_SESSION_ID] == str(session.id)
        assert LABEL_OWNER not in labels

    async def test_a_unit_member_is_labelled_as_its_units_pod(self, session):
        # A member runs in its unit root's pod, on the root's workspace claim.
        root_id = uuid4()
        member = session.model_copy(update={"hosted_by": root_id, "hosted_port": 8001})

        result = await IsolationContributor().contribute(member, SessionContext())

        assert result.values["podLabels"][LABEL_SESSION_ID] == str(root_id)
