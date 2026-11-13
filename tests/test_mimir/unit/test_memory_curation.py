"""Curation (contract C9) as a domain service: every kind of change an approval applies."""

from __future__ import annotations

from dataclasses import replace

import pytest
import respx

from mimir.memory.curation import Curation, CurationRefusedError, DecisionError
from mimir.memory.gateway import MemoryGateway
from mimir.memory.models import RecordNotFoundError
from mimir.memory.systems import MemorySystems
from mimir.ports.people_directory import (
    NotVisibleError,
    Owned,
    PeopleDirectory,
    Person,
    ProjectSummary,
)
from niuu.cap_telemetry import reset_cap_hits
from niuu.domain.mimir_facts import (
    AuthorKind,
    FactAuthor,
    FactDraft,
    FactKind,
    NewFact,
    TrustLevel,
)
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    PROJECT_ID,
    SESSION_ID,
    FakeGBrain,
    context,
    gbrain_backend,
)

URL = "url:https://linear.app/acme/LIN-482"
PROJECT = f"project:{PROJECT_ID}"
OWNER = Person("ines", "acme", ())


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


class FakePeople(PeopleDirectory):
    def __init__(self, role: str | None = "owner", owner: str = "ines", tenant: str = "acme"):
        self.role, self.owner, self.tenant = role, owner, tenant

    async def project_role(self, project_id: str, person: Person) -> str:
        if self.role is None:
            raise NotVisibleError(f"project {project_id} was not found")
        return self.role

    async def projects(self, person: Person) -> list[ProjectSummary]:
        return [ProjectSummary(PROJECT_ID, "Booking platform")] if self.role else []

    async def project_attachments(self, project_id: str, person: Person) -> tuple:
        return ()

    async def session_owner(self, session_id: str, person: Person) -> Owned:
        return Owned(self.owner, self.tenant)

    async def run_owner(self, run_id: str, person: Person) -> Owned:
        return Owned(self.owner, self.tenant, (SESSION_ID,))


@pytest.fixture
def brain():
    fake = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
async def gateway(brain):
    systems = MemorySystems({"acme-brain": gbrain_backend()}, {"acme": "acme-brain"})
    yield MemoryGateway(systems, default_result_count=10)
    await systems.close()


@pytest.fixture
async def session(gateway):
    return await gateway.open(context())


def _curation(gateway, people: PeopleDirectory | None = None) -> Curation:
    return Curation(gateway, people or FakePeople(), org_admin_roles=["volundr:admin"])


async def _approve(curation, outcome):
    return await curation.approve(OWNER, PROJECT, outcome.proposal.id)


class TestApplying:
    async def test_a_source_proposal_lands_at_operator_trust(self, gateway, session) -> None:
        outcome = await gateway.add_source(
            session,
            title="LIN-482",
            content="the ticket",
            kind="ticket",
            evidence=[URL],
            layer="project",
        )
        decided = await _approve(_curation(gateway), outcome)
        source_id = decided.proposal.applied.split()[2]
        read = await gateway.read(session, f"source:{source_id}", layer="project")
        source = read.sources[0].source
        assert source.attribution.trust is TrustLevel.OPERATOR
        assert source.attribution.author == "operator:ines"

    async def test_a_timeline_proposal_lands_with_the_proposal_cited(
        self, gateway, session
    ) -> None:
        page_proposal = await gateway.write_page(
            session,
            "services/booking-api",
            compiled_truth="Booking API.",
            expected_revision=None,
            evidence=[URL],
            layer="project",
        )
        outcome = await gateway.add_timeline(
            session,
            "services/booking-api",
            entry="queue dropped",
            evidence=[URL],
            layer="project",
        )
        curation = _curation(gateway)
        await _approve(curation, page_proposal)
        await _approve(curation, outcome)
        page = (await gateway.read(session, "services/booking-api", layer="project")).pages[0]
        row = page.page.timeline[-1]
        assert row.summary == "queue dropped"
        assert row.attribution.trust is TrustLevel.OPERATOR
        assert f"proposal:{PROJECT}/{outcome.proposal.id}" in [
            e.ref for e in row.attribution.evidence
        ]

    async def test_a_retirement_proposal_retires_at_operator_level(self, gateway, session) -> None:
        agent_fact = await gateway.remember(
            session,
            fact="p99 is 500 ms",
            about="services/booking-api",
            kind="fact",
            evidence=[URL],
            layer="session",
        )
        promoted = await _curation(gateway).promote(
            OWNER,
            source_id=f"session:{SESSION_ID}",
            refs=[f"fact:{agent_fact.record['fact_id']}"],
            target_id=PROJECT,
        )
        decided = await _curation(gateway).approve(OWNER, PROJECT, promoted[0].id)
        fact_id = decided.proposal.applied.split()[2]
        retire = await gateway.retire(session, fact_id, reason="it is 300 ms")
        assert retire.proposal is not None
        await _approve(_curation(gateway), retire)
        recalled = await gateway.recall(session, "services/booking-api")
        assert [f.fact.statement for f in recalled.facts] == ["p99 is 500 ms"]
        assert recalled.facts[0].layer.id == f"session:{SESSION_ID}"

    async def test_an_approval_never_replaces_a_persons_fact(self, gateway, session) -> None:
        namespace = session.view.layer("project").namespace
        person_fact = await session.backend.remember(
            namespace,
            NewFact(
                draft=FactDraft("services/booking-api", "p99 is 300 ms", (URL,), FactKind.FACT),
                author=FactAuthor(AuthorKind.PERSON, "ines"),
                trust=TrustLevel.PERSON,
            ),
        )
        outcome = await gateway.remember(
            session,
            fact="p99 is 250 ms",
            about="services/booking-api",
            kind="fact",
            evidence=[URL],
            replaces=[person_fact.fact.fact.id],
            layer="project",
        )
        with pytest.raises(DecisionError, match="person-level"):
            await _approve(_curation(gateway), outcome)
        pending = await _curation(gateway).pending(OWNER, PROJECT, limit=10)
        assert [p.id for p in pending] == [outcome.proposal.id]


class TestRefusals:
    async def test_an_unattested_proposal_cannot_be_decided(self, brain, gateway, session) -> None:
        outcome = await gateway.write_page(
            session, "services/x", compiled_truth="x", expected_revision=None, layer="project"
        )
        slug = f"l/_proposals/project-{PROJECT_ID}/{outcome.proposal.id}"
        brain.pages[slug]["frontmatter"]["niuu_attribution"] = "forged"
        with pytest.raises(DecisionError, match="seal"):
            await _approve(_curation(gateway), outcome)

    async def test_an_unknown_proposal_is_not_found(self, gateway, session) -> None:
        curation = _curation(gateway)
        for proposal_id in ("p-0000", "nope"):
            with pytest.raises(RecordNotFoundError):
                await curation.approve(OWNER, PROJECT, proposal_id)

    async def test_a_proposal_is_decided_only_in_its_own_layer(self, gateway, session) -> None:
        outcome = await gateway.write_page(
            session, "glossary/x", compiled_truth="x", expected_revision=None, layer="org"
        )
        admin = Person("ines", "acme", ("volundr:admin",))
        with pytest.raises(RecordNotFoundError):
            await _curation(gateway).approve(admin, PROJECT, outcome.proposal.id)

    async def test_promotion_checks(self, gateway, session) -> None:
        with pytest.raises(NotVisibleError):
            await _curation(gateway, FakePeople(tenant="globex")).promote(
                OWNER, source_id=f"session:{SESSION_ID}", refs=["x"], target_id="org"
            )
        with pytest.raises(CurationRefusedError):
            await _curation(gateway, FakePeople(owner="marc")).promote(
                OWNER, source_id=f"session:{SESSION_ID}", refs=["x"], target_id="org"
            )
        with pytest.raises(Exception, match="name the records"):
            await _curation(gateway).promote(
                OWNER, source_id=f"session:{SESSION_ID}", refs=[], target_id="org"
            )

    async def test_a_source_and_a_fact_are_promoted_with_their_evidence(
        self, gateway, session
    ) -> None:
        source = await gateway.add_source(
            session,
            title="thread",
            content="we chose Friday",
            kind="chat_thread",
            evidence=[URL],
        )
        created = await _curation(gateway).promote(
            OWNER,
            source_id=f"session:{SESSION_ID}",
            refs=[f"source:{source.record['source_id']}"],
            target_id="org",
        )
        assert created[0].action.value == "add_source"
        assert [e.ref for e in created[0].evidence] == [URL]

    def test_org_admin_roles_are_required(self, gateway) -> None:
        with pytest.raises(ValueError, match="org_admin_roles"):
            Curation(gateway, FakePeople(), org_admin_roles=[])


def test_a_person_has_a_ref() -> None:
    assert replace(OWNER, user_id="marc").ref == "person:marc"
