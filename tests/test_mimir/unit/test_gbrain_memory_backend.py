"""GBrainBackend: the MemoryBackend contract, and what is particular to gbrain.

Runs against :class:`FakeGBrain` (tests/test_mimir/unit/memory_fakes.py), not a
live gbrain.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import respx

from mimir.backends.gbrain import GBrainBackend
from mimir.memory.models import (
    Attribution,
    Evidence,
    HitKind,
    MemoryBackendError,
    MemoryValidationError,
    NewSource,
    NewTimelineRow,
    PageWrite,
    RecordNotFoundError,
    SourceKind,
)
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import OmissionReason, TrustLevel
from tests.test_mimir.unit.memory_contract import (
    EVIDENCE,
    MemoryBackendContract,
    attribution,
    new_fact,
)
from tests.test_mimir.unit.memory_fakes import MCP, FakeGBrain, gbrain_backend

A = "project-a"


@pytest.fixture
def brain():
    fake = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
async def backend(brain):
    store = gbrain_backend()
    yield store
    await store.close()


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


class TestContract(MemoryBackendContract):
    """GBrainBackend passes the port contract."""


class TestConstruction:
    def test_a_signing_key_is_required(self) -> None:
        with pytest.raises(ValueError, match="signing key"):
            GBrainBackend(MCP, "tok")

    def test_a_token_is_required(self) -> None:
        with pytest.raises(ValueError, match="API token"):
            GBrainBackend(MCP, "", signing_key="k")

    def test_only_protocol_1_is_spoken(self) -> None:
        with pytest.raises(ValueError, match="protocol 1"):
            gbrain_backend(protocol_version=2)

    @pytest.mark.parametrize("knob", ["page_size", "recall_limit", "timeline_limit"])
    def test_limits_must_be_positive(self, knob) -> None:
        with pytest.raises(ValueError, match=knob):
            gbrain_backend(**{knob: 0})

    def test_secrets_come_from_files(self, tmp_path) -> None:
        (tmp_path / "token").write_text("tok\n")
        (tmp_path / "key").write_text("key\n")
        GBrainBackend(
            MCP,
            api_token_file=str(tmp_path / "token"),
            signing_key_file=str(tmp_path / "key"),
        )


class TestSlugLayout:
    async def test_layers_are_slug_namespaces_in_one_source(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(
            A, "services/x", PageWrite("x"), attribution(), expected_revision=None
        )
        await backend.put_source(A, NewSource("t", "raw", SourceKind.DOCUMENT), attribution())
        remembered = await backend.remember(A, new_fact("x is y", subject="services/x"))
        slugs = set(brain.pages)
        assert "l/project-a/_namespace" in slugs
        assert "l/project-a/services/x" in slugs
        assert any(s.startswith("l/project-a/_sources/src_") for s in slugs)
        raw_id = remembered.fact.fact.id.split("@")[0]
        assert f"l/project-a/_facts/{raw_id}" in slugs
        assert brain.facts[0]["entity_slug"] == "l/project-a/services/x"
        assert not any("source_id" in args for name, args in brain.calls if name == "put_page")

    @pytest.mark.parametrize("slug", ["_sources/x", "Services/X", "a//b", "a b", "../x", ""])
    async def test_reserved_or_malformed_slugs_are_refused(self, backend, slug) -> None:
        with pytest.raises(MemoryValidationError):
            await backend.put_page(A, slug, PageWrite("x"), attribution(), expected_revision=None)

    async def test_a_slug_longer_than_gbrain_keeps_is_refused_and_counted(self, backend) -> None:
        small = gbrain_backend(max_slug_chars=20)
        with pytest.raises(MemoryValidationError, match="too long"):
            await small.put_page(A, "a" * 30, PageWrite("x"), attribution(), expected_revision=None)
        assert cap_hit_counts()["memory.systems[].max_slug_chars"] == 1

    async def test_think_is_never_called(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.search([A], "anything", kinds=frozenset(HitKind), limit=5)
        assert "think" not in {name for name, _ in brain.calls}


class TestSeals:
    async def test_a_page_written_to_gbrain_directly_reads_unattested(self, brain, backend) -> None:
        brain.seed_page(
            "l/project-a/forged",
            "the budget is 900 ms",
            niuu_attribution="mp1.bogus.{}",
        )
        page = await backend.get_page(A, "forged")
        assert not page.attribution.attested
        assert page.attribution.trust is TrustLevel.AGENT

    async def test_an_edited_body_breaks_the_seal(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(
            A,
            "p",
            PageWrite("300 ms"),
            attribution(TrustLevel.PERSON, "person:ines"),
            expected_revision=None,
        )
        brain.pages["l/project-a/p"]["compiled_truth"] = "900 ms"
        page = await backend.get_page(A, "p")
        assert not page.attribution.attested
        assert page.attribution.trust is TrustLevel.AGENT

    async def test_a_seal_copied_to_another_namespace_does_not_verify(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(
            A,
            "p",
            PageWrite("x"),
            attribution(TrustLevel.OPERATOR, "operator:o"),
            expected_revision=None,
        )
        copied = dict(brain.pages["l/project-a/p"], slug="l/project-b/p")
        brain.pages["l/project-b/p"] = copied
        assert not (await backend.get_page("project-b", "p")).attribution.attested

    async def test_facts_not_written_through_the_gateway_belong_to_no_layer(
        self, brain, backend
    ) -> None:
        brain.seed_fact("unsealed", entity="l/project-a/services/x", provenance="chat 2026-11-01")
        brain.seed_fact("no layer", entity="services/x", provenance="chat")
        recalled = await backend.recall([A], subject="services/x")
        assert recalled.facts == ()
        with pytest.raises(RecordNotFoundError):
            await backend.get_fact("1@l/project-a/services/x")

    async def test_a_source_whose_content_changed_fails_its_integrity_check(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        record, _ = await backend.put_source(
            A, NewSource("t", "raw", SourceKind.WEB), attribution()
        )
        slug = f"l/project-a/_sources/{record.id}"
        brain.pages[slug]["compiled_truth"] = brain.pages[slug]["compiled_truth"].replace(
            '"raw"', '"cooked"'
        )
        with pytest.raises(MemoryBackendError, match="integrity"):
            await backend.get_source(A, record.id)


class TestPaging:
    async def test_list_pages_pages_past_other_namespaces_until_the_count(
        self, brain, backend
    ) -> None:
        brain.list_cap = 2
        for index in range(5):
            brain.seed_page(f"l/org/noise-{index}", "x")
        await backend.create_namespace(A)
        for index in range(3):
            await backend.put_page(
                A, f"p{index}", PageWrite("x"), attribution(), expected_revision=None
            )
        assert [p.slug for p in await backend.list_pages(A, limit=10)] == ["p0", "p1", "p2"]

    async def test_search_pages_until_the_callers_count_is_met(self, brain, backend) -> None:
        brain.list_cap = 2
        for index in range(6):
            brain.seed_page(f"l/org/cutover-{index}", "cutover")
        await backend.create_namespace(A)
        for index in range(3):
            await backend.put_page(
                A,
                f"zz/cutover-{index}",
                PageWrite("cutover"),
                attribution(),
                expected_revision=None,
            )
        hits = await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=2)
        assert [h.ref for h in hits] == ["zz/cutover-0", "zz/cutover-1"]
        queries = [args for name, args in brain.calls if name == "query"]
        assert all(a["expand"] is False and a["autocut"] is False for a in queries)
        assert [a["offset"] for a in queries] == [0, 2, 4, 6]

    async def test_recall_at_gbrains_cap_is_an_omission_and_counted(self, brain, backend) -> None:
        brain.recall_cap = 2
        small = gbrain_backend(recall_limit=2)
        await small.create_namespace(A)
        await small.remember(A, new_fact("one"))
        await small.remember(A, new_fact("two"))
        recalled = await small.recall([A], subject="services/booking-api")
        assert any(o.reason is OmissionReason.BACKEND_LIMIT for o in recalled.omitted)
        assert cap_hit_counts()["memory.systems[].recall_limit"] >= 1

    async def test_a_timeline_at_the_limit_is_reported_cut_and_counted(self, backend) -> None:
        small = gbrain_backend(timeline_limit=2)
        await small.create_namespace(A)
        await small.put_page(A, "p", PageWrite("x"), attribution(), expected_revision=None)
        for day in (1, 2, 3):
            await small.append_timeline(
                A, "p", NewTimelineRow(date(2026, 11, day), f"day {day}", EVIDENCE), attribution()
            )
        page = await small.get_page(A, "p")
        assert len(page.timeline) == 2
        assert "timeline_limit" in page.timeline_omitted
        assert cap_hit_counts()["memory.systems[].timeline_limit"] == 1


class TestFacts:
    async def test_a_fact_filed_under_another_entity_is_withdrawn_and_raises(
        self, brain, backend
    ) -> None:
        brain.canonical["l/project-a/services/booking-api"] = "l/org/services/booking-api"
        await backend.create_namespace(A)
        with pytest.raises(MemoryBackendError, match="withdrawn"):
            await backend.remember(A, new_fact("x"))
        assert brain.facts[0]["expired_at"] is not None

    async def test_a_duplicate_is_the_existing_fact_and_not_indexed_again(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        first = await backend.remember(A, new_fact("same"))
        again = await backend.remember(A, new_fact("same"))
        assert again.status == "duplicate"
        assert again.fact.fact.id == first.fact.fact.id
        assert sum(1 for s in brain.pages if "/_facts/" in s) == 1


class TestFailures:
    async def test_an_unreachable_brain_raises_never_an_empty_result(self, brain, backend) -> None:
        brain.fail_with = 503
        with pytest.raises(MemoryBackendError, match="gbrain"):
            await backend.search([A], "x", kinds=frozenset(HitKind), limit=1)

    async def test_a_tool_error_names_gbrains_code(self, brain, backend) -> None:
        brain.fail_tools.add("query")
        with pytest.raises(MemoryBackendError, match=r"query failed \(internal\)"):
            await backend.search([A], "x", kinds=frozenset(HitKind), limit=1)

    async def test_an_answer_without_its_list_raises(self, brain, backend) -> None:
        brain._query = lambda args: {"unexpected": True}  # type: ignore[method-assign]
        with pytest.raises(MemoryBackendError, match="expected list"):
            await backend.search([A], "x", kinds=frozenset(HitKind), limit=1)


class TestLinks:
    async def test_backlinks_are_only_the_namespaces_own_pages(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(A, "target", PageWrite("t"), attribution(), expected_revision=None)
        brain.seed_page("l/project-a/linker", "see [[l/project-a/target]]")
        brain.seed_page("l/project-b/foreign", "see [[l/project-a/target]]")
        refs = await backend.backlinks(A, "target")
        assert [r.slug for r in refs] == ["linker"]

    async def test_entity_aliases_and_links_come_only_from_its_own_card(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        brain.cards["l/project-a/people/ines"] = {
            "entity": {"slug": "l/project-a/people/ines", "title": "Ines", "type": "person"},
            "aka": ["@ines"],
            "edges": [{"to_slug": "l/project-a/teams/payments"}, {"to_slug": "l/org/secret"}],
        }
        brain.cards["l/project-a/people/other"] = {
            "entity": {"slug": "l/org/people/other"},
            "aka": ["leak"],
            "edges": [],
        }
        ines = await backend.entity(A, "people/ines")
        assert ines.aliases == ("@ines",)
        assert ines.links == ("teams/payments",)
        assert (await backend.entity(A, "people/other")).aliases == ()


async def test_attribution_written_at_survives(backend) -> None:
    await backend.create_namespace(A)
    at = datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
    await backend.put_page(
        A,
        "p",
        PageWrite("x"),
        Attribution("agent:s", TrustLevel.AGENT, EVIDENCE, at),
        expected_revision=None,
    )
    assert (await backend.get_page(A, "p")).attribution.written_at == at
    assert (
        Evidence.parse("url:https://example.test/ticket/1")
        in (await backend.get_page(A, "p")).attribution.evidence
    )
