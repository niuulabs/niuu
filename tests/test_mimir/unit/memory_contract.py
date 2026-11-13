"""The MemoryBackend contract, written against the port only.

A memory system plugs in behind the gateway by passing these. Subclass
:class:`MemoryBackendContract` in a ``test_*.py`` module and give it a ``backend``
fixture that yields a fresh, empty :class:`~mimir.ports.memory_backend.MemoryBackend`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from mimir.memory.models import (
    Attribution,
    Evidence,
    HitKind,
    NewSource,
    NewTimelineRow,
    PageWrite,
    RecordNotFoundError,
    RevisionConflictError,
    SourceKind,
)
from mimir.ports.memory_backend import LAYER_CAPABILITIES, MemoryBackend
from niuu.domain.mimir_facts import (
    AuthorKind,
    FactAuthor,
    FactDraft,
    FactKind,
    NewFact,
    TrustLevel,
)

A = "project-a"
B = "project-b"
EVIDENCE = (Evidence.parse("url:https://example.test/ticket/1"),)


def attribution(
    trust: TrustLevel = TrustLevel.AGENT, author: str = "agent:session:x"
) -> Attribution:
    return Attribution(author, trust, EVIDENCE, datetime.now(UTC))


def new_fact(statement: str, subject: str = "services/booking-api") -> NewFact:
    draft = FactDraft(subject, statement, ("url:https://example.test/ticket/1",), FactKind.FACT)
    return NewFact(
        draft=draft, author=FactAuthor(AuthorKind.AGENT, "session:x"), trust=TrustLevel.AGENT
    )


class MemoryBackendContract:
    """What every memory system behind the gateway must do."""

    async def test_declares_the_capabilities_every_layer_needs(
        self, backend: MemoryBackend
    ) -> None:
        for needed in LAYER_CAPABILITIES.values():
            assert needed <= backend.capabilities

    # -- pages -----------------------------------------------------------

    async def test_a_page_round_trips_with_its_attribution(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        written = await backend.put_page(
            A,
            "services/booking-api",
            PageWrite("The p99 budget is 300 ms.", title="Booking API", evidence=EVIDENCE),
            attribution(TrustLevel.OPERATOR, "operator:ines"),
            expected_revision=None,
        )
        read = await backend.get_page(A, "services/booking-api")
        assert read.compiled_truth == "The p99 budget is 300 ms."
        assert read.title == "Booking API"
        assert read.revision == written.revision != ""
        assert read.attribution.attested
        assert read.attribution.trust is TrustLevel.OPERATOR
        assert read.attribution.author == "operator:ines"
        assert read.attribution.evidence == EVIDENCE

    async def test_creating_a_page_that_exists_conflicts(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(A, "p", PageWrite("one"), attribution(), expected_revision=None)
        with pytest.raises(RevisionConflictError):
            await backend.put_page(A, "p", PageWrite("two"), attribution(), expected_revision=None)

    async def test_a_stale_revision_conflicts(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        first = await backend.put_page(
            A, "p", PageWrite("one"), attribution(), expected_revision=None
        )
        await backend.put_page(
            A, "p", PageWrite("two"), attribution(), expected_revision=first.revision
        )
        with pytest.raises(RevisionConflictError):
            await backend.put_page(
                A, "p", PageWrite("three"), attribution(), expected_revision=first.revision
            )
        assert (await backend.get_page(A, "p")).compiled_truth == "two"

    async def test_a_rewrite_keeps_the_timeline(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        first = await backend.put_page(
            A, "p", PageWrite("one"), attribution(), expected_revision=None
        )
        await backend.append_timeline(
            A, "p", NewTimelineRow(date(2026, 11, 10), "budget agreed", EVIDENCE), attribution()
        )
        current = await backend.get_page(A, "p")
        await backend.put_page(
            A, "p", PageWrite("two"), attribution(), expected_revision=current.revision
        )
        page = await backend.get_page(A, "p")
        assert page.revision != first.revision
        assert [row.summary for row in page.timeline] == ["budget agreed"]
        assert page.timeline[0].on == date(2026, 11, 10)
        assert page.timeline[0].attribution.attested
        assert page.timeline[0].attribution.evidence == EVIDENCE

    async def test_missing_pages_are_not_found(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        with pytest.raises(RecordNotFoundError):
            await backend.get_page(A, "nope")
        with pytest.raises(RecordNotFoundError):
            await backend.append_timeline(
                A, "nope", NewTimelineRow(date(2026, 11, 10), "x", EVIDENCE), attribution()
            )

    async def test_a_deleted_page_is_gone(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(A, "p", PageWrite("one"), attribution(), expected_revision=None)
        await backend.delete_page(A, "p")
        with pytest.raises(RecordNotFoundError):
            await backend.get_page(A, "p")
        with pytest.raises(RecordNotFoundError):
            await backend.delete_page(A, "p")

    async def test_namespaces_keep_pages_apart(self, backend: MemoryBackend) -> None:
        for ns, text in ((A, "in a"), (B, "in b")):
            await backend.create_namespace(ns)
            await backend.put_page(
                ns, "same", PageWrite(text), attribution(), expected_revision=None
            )
        assert (await backend.get_page(A, "same")).compiled_truth == "in a"
        assert (await backend.get_page(B, "same")).compiled_truth == "in b"
        assert [p.slug for p in await backend.list_pages(A, limit=10)] == ["same"]
        assert {p.namespace for p in await backend.list_pages(B, limit=10)} == {B}

    async def test_list_pages_returns_the_count_asked_for(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        for index in range(5):
            await backend.put_page(
                A, f"p{index}", PageWrite("x"), attribution(), expected_revision=None
            )
        assert len(await backend.list_pages(A, limit=3)) == 3
        assert len(await backend.list_pages(A, limit=50)) == 5

    async def test_list_pages_without_a_limit_returns_every_page(
        self, backend: MemoryBackend
    ) -> None:
        await backend.create_namespace(A)
        for index in range(120):
            await backend.put_page(
                A, f"p{index:03d}", PageWrite("x"), attribution(), expected_revision=None
            )
        assert len(await backend.list_pages(A, limit=None)) == 120

    # -- sources ---------------------------------------------------------

    async def test_a_source_is_kept_as_it_arrived_and_never_duplicated(
        self, backend: MemoryBackend
    ) -> None:
        await backend.create_namespace(A)
        raw = NewSource(
            "LIN-482", "  exact\n\nbytes  ", SourceKind.TICKET, "https://linear/LIN-482"
        )
        first, new = await backend.put_source(A, raw, attribution())
        again, new_again = await backend.put_source(A, raw, attribution())
        assert new and not new_again
        assert first.id == again.id
        stored = await backend.get_source(A, first.id)
        assert stored.content == "  exact\n\nbytes  "
        assert stored.kind == "ticket"
        assert stored.attribution.attested
        assert [s.id for s in await backend.list_sources(A, limit=10)] == [first.id]
        assert await backend.list_sources(B, limit=10) == []

    # -- facts -----------------------------------------------------------

    async def test_facts_are_labelled_and_kept_to_their_namespace(
        self, backend: MemoryBackend
    ) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
        remembered = await backend.remember(A, new_fact("p99 budget is 300 ms"))
        await backend.remember(B, new_fact("p99 budget is 500 ms"))
        assert remembered.status == "inserted"
        assert remembered.fact.namespace == A
        assert remembered.fact.fact.subject == "services/booking-api"
        only_a = await backend.recall([A], subject="services/booking-api")
        assert [f.fact.statement for f in only_a.facts] == ["p99 budget is 300 ms"]
        both = await backend.recall([A, B], subject="services/booking-api")
        assert {f.namespace for f in both.facts} == {A, B}
        fetched = await backend.get_fact(remembered.fact.fact.id)
        assert fetched.namespace == A

    async def test_a_retired_fact_is_kept_but_no_longer_current(
        self, backend: MemoryBackend
    ) -> None:
        await backend.create_namespace(A)
        remembered = await backend.remember(A, new_fact("p99 budget is 300 ms"))
        retired = await backend.retire(A, remembered.fact.fact.id, by="agent:x", why="wrong")
        assert retired.fact.retired is not None
        assert retired.fact.retired.why == "wrong"
        assert (await backend.recall([A], subject="services/booking-api")).facts == ()
        with pytest.raises(RecordNotFoundError):
            await backend.retire(B, remembered.fact.fact.id, by="agent:x", why="wrong layer")

    async def test_the_entity_of_one_namespace(self, backend: MemoryBackend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(
            A,
            "services/booking-api",
            PageWrite("A booking API."),
            attribution(),
            expected_revision=None,
        )
        await backend.remember(A, new_fact("p99 budget is 300 ms"))
        layer = await backend.entity(A, "services/booking-api")
        assert layer.page is not None and layer.page.compiled_truth == "A booking API."
        assert [f.fact.statement for f in layer.facts] == ["p99 budget is 300 ms"]
        empty = await backend.entity(A, "people/nobody")
        assert empty.page is None and empty.facts == ()

    # -- search and changes ----------------------------------------------

    async def test_search_labels_hits_and_reads_only_the_named_namespaces(
        self, backend: MemoryBackend
    ) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
            await backend.put_page(
                ns,
                "cutover",
                PageWrite(f"booking cutover owner in {ns}"),
                attribution(),
                expected_revision=None,
            )
        source, _ = await backend.put_source(
            A, NewSource("thread", "booking cutover thread", SourceKind.CHAT_THREAD), attribution()
        )
        hits = await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=10)
        assert {(h.kind, h.ref, h.namespace) for h in hits} == {
            (HitKind.PAGE, "cutover", A),
            (HitKind.SOURCE, source.id, A),
        }
        pages_only = await backend.search(
            [A, B], "cutover", kinds=frozenset({HitKind.PAGE}), limit=10
        )
        assert {h.namespace for h in pages_only} == {A, B}
        assert len(await backend.search([A, B], "cutover", kinds=frozenset(HitKind), limit=1)) == 1

    async def test_search_without_a_limit_is_not_capped_by_the_backend(
        self, backend: MemoryBackend
    ) -> None:
        await backend.create_namespace(A)
        for index in range(30):
            await backend.put_page(
                A, f"c{index:02d}", PageWrite("cutover"), attribution(), expected_revision=None
            )
        hits = await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=None)
        assert len(hits) > 0
        capped = await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=3)
        assert len(capped) == 3

    async def test_delta_reports_what_changed_in_the_namespaces(
        self, backend: MemoryBackend
    ) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
        start = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
        for index in range(4):
            await backend.put_page(
                A, f"a{index}", PageWrite("x"), attribution(), expected_revision=None
            )
        await backend.put_page(B, "b", PageWrite("x"), attribution(), expected_revision=None)
        first = await backend.delta([A], since=start, limit=2)
        assert [p.slug for p in first.pages] == ["a0", "a1"]
        assert first.has_more
        rest = await backend.delta([A], since=first.cursor, limit=10)
        assert [p.slug for p in rest.pages] == ["a2", "a3"]
        assert not rest.has_more

    # -- namespaces ------------------------------------------------------

    async def test_every_created_layer_is_listed(self, backend: MemoryBackend) -> None:
        for ns in (A, "session-s1", "_proposals/project-a"):
            await backend.create_namespace(ns)
        assert {A, "session-s1"} <= set(await backend.list_namespaces())
        await backend.delete_namespace("session-s1")
        assert "session-s1" not in await backend.list_namespaces()

    async def test_creating_a_namespace_twice_is_harmless(self, backend: MemoryBackend) -> None:
        assert await backend.create_namespace(A) is True
        assert await backend.create_namespace(A) is False

    async def test_deleting_a_namespace_removes_its_pages_and_withdraws_its_facts(
        self, backend: MemoryBackend
    ) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
            await backend.put_page(ns, "p", PageWrite("x"), attribution(), expected_revision=None)
        kept = await backend.remember(B, new_fact("stays"))
        gone = await backend.remember(A, new_fact("goes"))
        removal = await backend.delete_namespace(A)
        assert removal.facts_withdrawn == 1
        assert removal.pages_removed >= 1
        with pytest.raises(RecordNotFoundError):
            await backend.get_page(A, "p")
        with pytest.raises(RecordNotFoundError):
            await backend.get_fact(gone.fact.fact.id)
        assert (await backend.get_fact(kept.fact.fact.id)).fact.retired is None
        assert (await backend.get_page(B, "p")).compiled_truth == "x"
