"""The memory gateway: layered reads, the write rules, proposals (design sections 4–6).

Runs the real GBrainBackend against FakeGBrain; one fake brain per org.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx

from mimir.backends.gbrain import GBrainBackend
from mimir.exceptions import MimirConfigurationError
from mimir.memory.gateway import (
    LayerUnavailableError,
    MemoryGateway,
    list_proposals,
)
from mimir.memory.models import (
    Attribution,
    CapabilityMissingError,
    ChangeAction,
    Evidence,
    HitKind,
    Layer,
    LayerKind,
    MemoryValidationError,
    PageWrite,
    RecordNotFoundError,
)
from mimir.memory.rules import Rule, RuleRefusalError
from mimir.memory.systems import MemorySystems, TenantNotConfiguredError
from mimir.ports.memory_backend import Capability
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
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
    OTHER_SESSION_ID,
    PROJECT_ID,
    SESSION_ID,
    SIGNING_KEY,
    FakeGBrain,
    context,
    gbrain_backend,
)

GLOBEX_MCP = "https://globex-brain.test/mcp"
URL = "url:https://linear.app/acme/LIN-482"
PROJECT_NS = f"project-{PROJECT_ID}"


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


@pytest.fixture
def brains():
    acme, globex = FakeGBrain(), FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=acme.handle)
        router.post(GLOBEX_MCP).mock(side_effect=globex.handle)
        yield acme, globex


@pytest.fixture
def brain(brains):
    return brains[0]


@pytest.fixture
async def systems(brains):
    built = MemorySystems(
        {
            "acme-brain": gbrain_backend(),
            "globex-brain": GBrainBackend(GLOBEX_MCP, "tok", signing_key=SIGNING_KEY),
        },
        {"acme": "acme-brain", "globex": "globex-brain"},
    )
    yield built
    await built.close()


@pytest.fixture
def gateway(systems) -> MemoryGateway:
    return MemoryGateway(systems, default_result_count=10)


@pytest.fixture
async def session(gateway):
    return await gateway.open(context())


async def _seed(session, layer: str, slug: str, text: str, trust=TrustLevel.OPERATOR) -> None:
    """A record an owner wrote earlier, straight through the backend."""
    namespace = session.view.layer(layer).namespace
    author = "operator:owner" if trust is TrustLevel.OPERATOR else "person:ines"
    await session.backend.put_page(
        namespace,
        slug,
        PageWrite(text, title=slug),
        Attribution(author, trust, (Evidence.parse(URL),), datetime.now(UTC)),
        expected_revision=None,
    )


async def _seed_fact(
    session, layer: str, statement: str, kind: AuthorKind, subject="services/booking-api"
):
    namespace = session.view.layer(layer).namespace
    draft = FactDraft(subject, statement, (URL,), FactKind.FACT)
    trust = {AuthorKind.PERSON: TrustLevel.PERSON, AuthorKind.AGENT: TrustLevel.AGENT}[kind]
    new = NewFact(draft=draft, author=FactAuthor(kind, "someone"), trust=trust)
    return await session.backend.remember(namespace, new)


class TestOpening:
    async def test_each_layer_is_placed_once(self, brain, gateway) -> None:
        await gateway.open(context())
        await gateway.open(context())
        markers = [
            a["slug"]
            for n, a in brain.calls
            if n == "put_page" and a["slug"].endswith("_namespace")
        ]
        assert sorted(markers) == sorted(
            [f"l/session-{SESSION_ID}/_namespace", f"l/{PROJECT_NS}/_namespace", "l/org/_namespace"]
        )

    async def test_an_org_without_a_memory_system_names_the_setting(self, gateway) -> None:
        with pytest.raises(TenantNotConfiguredError, match="memory.tenants"):
            await gateway.open(context(tenant="initech"))

    async def test_a_system_lacking_a_capability_is_refused_when_placed(self) -> None:
        class NoChanges(GBrainBackend):
            @property
            def capabilities(self):
                return frozenset(Capability) - {Capability.CHANGES}

        backend = NoChanges(MCP, "tok", signing_key="k")
        with pytest.raises(CapabilityMissingError, match="changes"):
            MemorySystems({"weak": backend}, {"acme": "weak"})

    async def test_a_brain_that_cannot_prepare_a_layer_names_it(self, brain, gateway) -> None:
        brain.fail_with = 503
        with pytest.raises(LayerUnavailableError, match=f"session-{SESSION_ID}"):
            await gateway.open(context())


class TestWrites:
    async def test_a_session_write_lands_in_the_session_layer(
        self, brain, gateway, session
    ) -> None:
        outcome = await gateway.write_page(
            session,
            "findings/db",
            compiled_truth="staging DB is Postgres 15",
            expected_revision=None,
            evidence=[URL],
        )
        assert outcome.layer.id == f"session:{SESSION_ID}"
        assert outcome.proposal is None
        assert f"session:{SESSION_ID}" in outcome.summary
        assert f"l/session-{SESSION_ID}/findings/db" in brain.pages

    async def test_a_project_page_under_a_write_prefix_lands_in_the_project(
        self, brain, gateway, session
    ) -> None:
        outcome = await gateway.write_page(
            session,
            "decisions/drop-queue",
            compiled_truth="We dropped the queue.",
            expected_revision=None,
            layer="project",
        )
        assert outcome.layer.id == f"project:{PROJECT_ID}"
        assert outcome.proposal is None
        page = await session.backend.get_page(PROJECT_NS, "decisions/drop-queue")
        assert page.attribution.trust is TrustLevel.AGENT
        assert page.attribution.author == f"agent:session:{SESSION_ID}"

    async def test_rewriting_a_higher_trust_page_under_a_prefix_is_proposed(
        self, brain, gateway, session
    ) -> None:
        """Trust rule 3, seen live: an agent's compile rewrote an operator's page."""
        await _seed(session, "project", "decisions/drop-queue", "An owner wrote this.")

        outcome = await gateway.write_page(
            session,
            "decisions/drop-queue",
            compiled_truth="An owner wrote this. And the agent's addition.",
            expected_revision=None,
            evidence=[URL],
            layer="project",
        )

        assert outcome.proposal is not None
        assert outcome.proposal.reason.startswith("lower_trust_proposes")
        page = await session.backend.get_page(PROJECT_NS, "decisions/drop-queue")
        assert page.attribution.trust is TrustLevel.OPERATOR
        assert page.compiled_truth.strip() == "An owner wrote this."

    async def test_rewriting_an_agent_page_under_a_prefix_is_written(
        self, brain, gateway, session
    ) -> None:
        revision = None
        for text in ("First take.", "First take. Second take."):
            outcome = await gateway.write_page(
                session,
                "decisions/drop-queue",
                compiled_truth=text,
                expected_revision=revision,
                layer="project",
            )
            assert outcome.proposal is None
            revision = outcome.record["revision"]

    async def test_a_project_page_outside_the_prefixes_becomes_a_proposal(
        self, brain, gateway, session
    ) -> None:
        outcome = await gateway.write_page(
            session,
            "services/booking-api",
            compiled_truth="Owner is Ines.",
            expected_revision=None,
            evidence=[URL],
            layer="project",
        )
        assert outcome.proposal is not None
        assert f"l/{PROJECT_NS}/services/booking-api" not in brain.pages
        assert outcome.proposal.approver == f"the owners of project {PROJECT_ID}"
        assert outcome.proposal.id in outcome.summary and "must approve" in outcome.summary
        stored = f"l/_proposals/{PROJECT_NS}/{outcome.proposal.id}"
        assert stored in brain.pages
        listed = await list_proposals(
            session.backend, Layer(LayerKind.PROJECT, PROJECT_ID), limit=10
        )
        assert [p.id for p in listed] == [outcome.proposal.id]
        proposal = listed[0]
        assert proposal.attested
        assert proposal.action is ChangeAction.WRITE_PAGE
        assert proposal.change["ref"] == "services/booking-api"
        assert proposal.proposer_session == SESSION_ID
        assert proposal.session_owner == "ines"
        assert proposal.trust is TrustLevel.AGENT
        assert proposal.evidence == (Evidence.parse(URL),)
        assert proposal.reason.startswith("project_write_prefixes")

    async def test_facts_for_the_project_and_anything_for_the_org_are_proposed(
        self, brain, gateway, session
    ) -> None:
        fact = await gateway.remember(
            session,
            fact="p99 is 300 ms",
            about="services/booking-api",
            kind="fact",
            evidence=[URL],
            layer="project",
        )
        org = await gateway.add_timeline(
            session, "handbook/oncall", entry="rota changed", evidence=[URL], layer="org"
        )
        assert fact.proposal is not None and fact.proposal.action is ChangeAction.REMEMBER
        assert org.proposal is not None and org.proposal.approver == "an org admin"
        assert brain.facts == []
        assert f"l/_proposals/org/{org.proposal.id}" in brain.pages

    async def test_tool_output_is_not_a_source_and_the_refusal_is_counted(
        self, gateway, session
    ) -> None:
        with pytest.raises(RuleRefusalError, match="operational_source_refused"):
            await gateway.add_source(
                session, title="ls", content="a b c", kind="tool_output", evidence=["tool_call:t1"]
            )
        assert cap_hit_counts()["memory.write_rules.operational_source_refused"] == 1

    async def test_a_source_is_stored_with_its_url_as_evidence(self, gateway, session) -> None:
        outcome = await gateway.add_source(
            session,
            title="LIN-482",
            content="the ticket",
            kind="ticket",
            evidence=[],
            url="https://linear.app/x",
        )
        record = await session.backend.get_source(
            session.view.layers[0].namespace, outcome.record["source_id"]
        )
        assert record.attribution.evidence == (Evidence.parse("url:https://linear.app/x"),)
        with pytest.raises(MemoryValidationError, match="evidence"):
            await gateway.add_source(session, title="t", content="c", kind="document", evidence=[])

    async def test_proposing_what_may_be_written_is_refused(self, gateway, session) -> None:
        with pytest.raises(RuleRefusalError, match="proposal_not_needed"):
            await gateway.propose(
                session, layer="session", action="write_page", change={"ref": "x"}, evidence=[URL]
            )

    async def test_an_explicit_proposal_is_recorded(self, brain, gateway, session) -> None:
        outcome = await gateway.propose(
            session,
            layer="org",
            action="remember",
            change={"fact": "x", "about": "y", "kind": "fact"},
            evidence=[URL],
        )
        assert outcome.proposal is not None and outcome.layer.id == "org"
        with pytest.raises(MemoryValidationError, match="unknown change"):
            await gateway.propose(session, layer="org", action="delete", change={}, evidence=[URL])

    async def test_writing_another_projects_layer_is_refused(self, gateway, session) -> None:
        with pytest.raises(RuleRefusalError) as caught:
            await gateway.write_page(
                session,
                "decisions/x",
                compiled_truth="x",
                expected_revision=None,
                layer="project:9a0e0000-0000-4000-8000-0000000000bb",
            )
        assert caught.value.rule is Rule.LAYER_NOT_IN_SESSION


class TestRetireAndReplace:
    async def test_a_session_retires_its_own_fact(self, gateway, session) -> None:
        written = await gateway.remember(
            session, fact="db is pg 14", about="services/db", kind="fact", evidence=[URL]
        )
        outcome = await gateway.retire(session, written.record["fact_id"], reason="it is 15")
        assert outcome.proposal is None and "Retired" in outcome.summary
        again = await gateway.retire(session, written.record["fact_id"], reason="twice")
        assert "already retired" in again.summary

    async def test_retiring_a_project_fact_is_a_proposal(self, gateway, session) -> None:
        seeded = await _seed_fact(session, "project", "p99 is 300 ms", AuthorKind.PERSON)
        outcome = await gateway.retire(session, seeded.fact.fact.id, reason="stale")
        assert outcome.proposal is not None and outcome.proposal.action is ChangeAction.RETIRE
        assert (await session.backend.get_fact(seeded.fact.fact.id)).fact.retired is None

    async def test_a_fact_of_another_session_reads_as_not_found(self, gateway, session) -> None:
        other = await gateway.open(context(OTHER_SESSION_ID))
        theirs = await gateway.remember(
            other, fact="secret", about="services/db", kind="fact", evidence=[URL]
        )
        with pytest.raises(RecordNotFoundError):
            await gateway.retire(session, theirs.record["fact_id"], reason="mine now")
        with pytest.raises(RecordNotFoundError):
            await gateway.read(session, f"fact:{theirs.record['fact_id']}")

    async def test_a_fact_replaces_only_facts_of_its_own_layer(self, gateway, session) -> None:
        org = await _seed_fact(session, "org", "p99 is 500 ms", AuthorKind.PERSON)
        with pytest.raises(RuleRefusalError, match="replace_across_layers"):
            await gateway.remember(
                session,
                fact="p99 is 300 ms",
                about="services/booking-api",
                kind="fact",
                evidence=[URL],
                replaces=[org.fact.fact.id],
            )

    async def test_replacing_retires_the_old_fact(self, gateway, session) -> None:
        old = await gateway.remember(
            session, fact="pg 14", about="services/db", kind="fact", evidence=[URL]
        )
        new = await gateway.remember(
            session,
            fact="pg 15",
            about="services/db",
            kind="fact",
            evidence=[URL],
            replaces=[old.record["fact_id"]],
        )
        retired = await session.backend.get_fact(old.record["fact_id"])
        assert retired.fact.retired is not None
        assert retired.fact.retired.superseded_by == new.record["fact_id"]


class TestReads:
    async def test_the_same_page_in_several_layers_is_shown_nearest_first(
        self, gateway, session
    ) -> None:
        await _seed(session, "org", "services/booking-api", "Owned by platform.")
        await _seed(session, "project", "services/booking-api", "Owned by Ines.")
        await gateway.write_page(
            session, "services/booking-api", compiled_truth="Owned by Ines.", expected_revision=None
        )
        result = await gateway.read(session, "services/booking-api")
        assert [p.layer.kind for p in result.pages] == [
            LayerKind.SESSION,
            LayerKind.PROJECT,
            LayerKind.ORG,
        ]
        assert result.pages[1].page.attribution.trust is TrustLevel.OPERATOR
        assert len(result.disagreements) == 1
        assert result.disagreements[0].records[0].startswith("session:")

    async def test_a_page_in_no_layer_is_not_found(self, gateway, session) -> None:
        with pytest.raises(RecordNotFoundError):
            await gateway.read(session, "nowhere")

    async def test_search_labels_every_hit_and_groups_a_ref_nearest_first(
        self, brain, gateway, session
    ) -> None:
        await _seed(session, "org", "cutover", "booking cutover is owned by platform")
        await _seed(session, "project", "cutover", "booking cutover is owned by Ines")
        await _seed(session, "project", "people/ines", "Ines runs the booking cutover")
        brain.seed_page("l/project-other/cutover", "booking cutover elsewhere")
        hits = await gateway.search(session, "cutover")
        assert [(h.layer.kind, h.hit.ref) for h in hits] == [
            (LayerKind.PROJECT, "cutover"),
            (LayerKind.ORG, "cutover"),
            (LayerKind.PROJECT, "people/ines"),
        ]
        only_org = await gateway.search(session, "cutover", layers=["org"])
        assert {h.layer.kind for h in only_org} == {LayerKind.ORG}
        assert len(await gateway.search(session, "cutover", limit=1)) == 1
        assert await gateway.search(session, "cutover", kinds=[HitKind.SOURCE]) == []

    async def test_facts_come_highest_trust_first_and_disagreements_are_reported(
        self, gateway, session
    ) -> None:
        await _seed_fact(session, "org", "booking api p99 budget is 500 ms", AuthorKind.PERSON)
        await gateway.remember(
            session,
            fact="booking api p99 budget is 300 ms",
            about="services/booking-api",
            kind="fact",
            evidence=[URL],
        )
        recalled = await gateway.recall(session, "services/booking-api")
        assert [f.fact.trust for f in recalled.facts] == [TrustLevel.PERSON, TrustLevel.AGENT]
        assert recalled.facts[0].layer.id == "org"
        assert len(recalled.disagreements) == 1
        assert recalled.disagreements[0].records[0].startswith("org:")
        events = await gateway.recall(session, "services/booking-api", kinds=[FactKind.EVENT])
        assert events.facts == ()

    async def test_the_entity_card_merges_the_layers(self, gateway, session) -> None:
        await _seed(session, "org", "people/ines-moreau", "Ines, payments lead.")
        await _seed_fact(
            session,
            "project",
            "owns the booking cut-over",
            AuthorKind.PERSON,
            subject="people/ines-moreau",
        )
        card = await gateway.entity(session, "People/Ines-Moreau")
        assert card.subject == "people/ines-moreau"
        assert [p.layer.id for p in card.pages] == ["org"]
        assert [f.fact.statement for f in card.facts] == ["owns the booking cut-over"]
        assert card.facts[0].layer.id == f"project:{PROJECT_ID}"
        with pytest.raises(MemoryValidationError, match="not a slug"):
            await gateway.entity(session, "Ines Moreau")

    async def test_an_unreachable_brain_fails_the_read_naming_the_layers(
        self, brain, gateway, session
    ) -> None:
        brain.fail_with = 503
        with pytest.raises(LayerUnavailableError) as caught:
            await gateway.search(session, "cutover")
        assert f"session-{SESSION_ID}" in str(caught.value)
        assert "org" in str(caught.value) and "acme-brain" in str(caught.value)


class TestIsolation:
    async def test_sessions_of_one_project_share_the_project_but_not_their_session_layers(
        self, gateway, session
    ) -> None:
        other = await gateway.open(context(OTHER_SESSION_ID))
        await gateway.write_page(
            session,
            "decisions/schema",
            compiled_truth="one table",
            expected_revision=None,
            layer="project",
        )
        await gateway.write_page(
            session, "scratch/notes", compiled_truth="private finding", expected_revision=None
        )
        assert (await gateway.read(other, "decisions/schema")).pages[
            0
        ].layer.kind is LayerKind.PROJECT
        with pytest.raises(RecordNotFoundError):
            await gateway.read(other, "scratch/notes")

    async def test_another_org_sees_none_of_it(self, brains, gateway, session) -> None:
        acme, globex = brains
        await _seed(session, "org", "cutover", "booking cutover owner: Ines")
        outsider = await gateway.open(context(OTHER_SESSION_ID, tenant="globex"))
        assert await gateway.search(outsider, "cutover") == []
        with pytest.raises(RecordNotFoundError):
            await gateway.read(outsider, "cutover")
        assert all(not slug.endswith("/cutover") for slug in globex.pages)


async def test_systems_are_checked_at_startup() -> None:
    with pytest.raises(MimirConfigurationError, match="not in memory.systems"):
        MemorySystems({}, {"acme": "nope"})
    with pytest.raises(MimirConfigurationError, match="no 'name'"):
        MemorySystems.from_config([{"adapter": "mimir.backends.gbrain.GBrainBackend"}], {})
    good = {
        "name": "acme-brain",
        "adapter": "mimir.backends.gbrain.GBrainBackend",
        "mcp_url": MCP,
        "api_token": "t",
        "signing_key": "k",
    }
    with pytest.raises(MimirConfigurationError, match="two memory systems"):
        MemorySystems.from_config([good, good], {})
    with pytest.raises(MimirConfigurationError, match="cannot be imported"):
        MemorySystems.from_config([{"name": "a", "adapter": "nope.Missing"}], {})
    with pytest.raises(MimirConfigurationError, match="not a MemoryBackend"):
        MemorySystems.from_config([{"name": "a", "adapter": "mimir.config.MimirServiceConfig"}], {})
    with pytest.raises(MimirConfigurationError, match="could not be built"):
        MemorySystems.from_config(
            [{"name": "a", "adapter": "mimir.backends.gbrain.GBrainBackend", "mcp_url": MCP}], {}
        )
    with pytest.raises(MimirConfigurationError, match="has no 'adapter'"):
        MemorySystems.from_config([{"name": "a"}], {})
    built = MemorySystems.from_config([good], {"acme": "acme-brain"})
    assert built.for_tenant("acme")[0] == "acme-brain"
    await built.close()
