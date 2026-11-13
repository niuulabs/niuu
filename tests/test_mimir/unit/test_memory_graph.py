"""The knowledge graph (contract G1): how it is built from pages, and which layers it reads.

``TestBuilding`` and ``TestFailures`` drive :class:`KnowledgeGraph` over a fake backend;
``TestLayers`` drives ``readable_layers`` over a faked Forge. The route has its own file.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from mimir.memory.graph import (
    MAX_RECORDS_KNOB,
    GraphLayerError,
    GraphTooLargeError,
    KnowledgeGraph,
    first_sentence,
    normalize_slug,
    page_kind,
)
from mimir.memory.models import (
    Attribution,
    Evidence,
    Layer,
    LayerKind,
    MemoryBackendError,
    PageRecord,
    PageRef,
    SourceRecord,
    SourceRef,
    TimelineRow,
)
from mimir.memory.person_layers import ReadableLayer, readable_layers
from mimir.memory.systems import TenantNotConfiguredError
from mimir.ports.people_directory import ForgeUnavailableError, Person, PersonRefusedError
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import TrustLevel

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
PROJECT_A = "9a0e0000-0000-4000-8000-0000000000aa"
PROJECT_B = "9a0e0000-0000-4000-8000-0000000000bb"
ORG = Layer(LayerKind.ORG)
A = Layer(LayerKind.PROJECT, PROJECT_A)
B = Layer(LayerKind.PROJECT, PROJECT_B)
SRC_1 = "src_00000000000000a1"
SRC_2 = "src_00000000000000a2"
SRC_ORG = "src_00000000000000f0"


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


def _attribution(*refs: str) -> Attribution:
    return Attribution("agent:test", TrustLevel.AGENT, tuple(Evidence.parse(r) for r in refs), NOW)


def row(on: str, summary: str, *refs: str, detail: str = "") -> TimelineRow:
    return TimelineRow(date.fromisoformat(on), summary, detail, _attribution(*refs))


def page(
    layer: Layer,
    slug: str,
    truth: str,
    *,
    title: str = "",
    page_type: str = "",
    rows: tuple[TimelineRow, ...] = (),
    omitted: str = "",
) -> PageRecord:
    return PageRecord(
        slug=slug,
        namespace=layer.namespace,
        title=title or slug,
        page_type=page_type,
        compiled_truth=truth,
        revision="1",
        attribution=_attribution(),
        updated_at=NOW,
        timeline=rows,
        timeline_omitted=omitted,
    )


def source(layer: Layer, source_id: str, title: str, kind: str = "ticket") -> SourceRecord:
    return SourceRecord(source_id, layer.namespace, title, kind, "content", None, _attribution())


class FakeBackend:
    """The reads the graph makes, over pages and sources held per namespace."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, PageRecord]] = {}
        self.sources: dict[str, dict[str, SourceRecord]] = {}
        self.calls: list[str] = []
        self.fail: dict[tuple[str, str], Exception] = {}
        self.in_flight = 0
        self.peak = 0

    def add(self, *records: PageRecord | SourceRecord) -> FakeBackend:
        for record in records:
            if isinstance(record, PageRecord):
                self.pages.setdefault(record.namespace, {})[record.slug] = record
            else:
                self.sources.setdefault(record.namespace, {})[record.id] = record
        return self

    async def _enter(self, op: str, namespace: str) -> None:
        self.calls.append(op)
        failure = self.fail.get((op, namespace))
        if failure is not None:
            raise failure
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0)

    async def list_pages(self, namespace: str, *, limit: int) -> list[PageRef]:
        await self._enter("list_pages", namespace)
        self.in_flight -= 1
        pages = sorted(self.pages.get(namespace, {}))
        return [PageRef(slug, namespace, slug) for slug in pages[:limit]]

    async def get_page(self, namespace: str, slug: str) -> PageRecord:
        await self._enter("get_page", namespace)
        try:
            return self.pages[namespace][slug]
        finally:
            await asyncio.sleep(0)
            self.in_flight -= 1

    async def list_sources(self, namespace: str, *, limit: int) -> list[SourceRef]:
        await self._enter("list_sources", namespace)
        self.in_flight -= 1
        ids = sorted(self.sources.get(namespace, {}))
        return [SourceRef(i, namespace, i) for i in ids[:limit]]

    async def get_source(self, namespace: str, source_id: str) -> SourceRecord:
        await self._enter("get_source", namespace)
        self.in_flight -= 1
        return self.sources[namespace][source_id]

    def count(self, op: str) -> int:
        return self.calls.count(op)


class FakeSystems:
    def __init__(self, backend: FakeBackend, tenant: str = "acme") -> None:
        self._backend = backend
        self._tenant = tenant

    def for_tenant(self, tenant_id: str):
        if tenant_id != self._tenant:
            raise TenantNotConfiguredError(f"org {tenant_id!r} has no memory system")
        return "acme-brain", self._backend


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def graph_of(
    backend: FakeBackend,
    *,
    concurrency: int = 4,
    max_records: int = 100,
    cache_seconds: float = 30.0,
    clock: Clock | None = None,
) -> KnowledgeGraph:
    return KnowledgeGraph(
        FakeSystems(backend),  # type: ignore[arg-type]
        concurrency=concurrency,
        max_records_per_layer=max_records,
        cache_seconds=cache_seconds,
        clock=clock or Clock(),
    )


def layers(*which: Layer) -> list[ReadableLayer]:
    names = {ORG.id: "Org", A.id: "Booking platform", B.id: "Billing"}
    return [ReadableLayer(layer, names[layer.id]) for layer in which]


def build(backend: FakeBackend, *which: Layer, **options) -> dict:
    return asyncio.run(graph_of(backend, **options).graph("acme", layers(*which)))


def node(result: dict, node_id: str) -> dict:
    return next(n for n in result["nodes"] if n["id"] == node_id)


def edges(result: dict) -> set[tuple[str, str, str, str | None]]:
    return {(e["source"], e["target"], e["relation"], e["since"]) for e in result["edges"]}


class TestBuilding:
    def test_layers_are_listed_with_their_names_in_the_order_given(self) -> None:
        result = build(FakeBackend(), ORG, A)
        assert result["layers"] == [
            {"id": "org", "name": "Org"},
            {"id": f"project:{PROJECT_A}", "name": "Booking platform"},
        ]
        assert (result["nodes"], result["edges"], result["unresolved"]) == ([], [], [])

    def test_a_page_is_a_node_with_its_kind_summary_and_times(self) -> None:
        backend = FakeBackend().add(
            page(
                A,
                "decisions/booking-cutover",
                "## Compiled Truth\n\nCut over booking-api on Friday. Rollback is ready.",
                title="Booking cut-over",
                page_type="decision",
                rows=(row("2026-11-03", "Planned", "statement:s1"), row("2026-11-09", "Moved")),
            )
        )
        result = build(backend, A)
        assert node(result, f"{A.id}/decisions/booking-cutover") == {
            "id": f"{A.id}/decisions/booking-cutover",
            "layer": A.id,
            "path": "decisions/booking-cutover",
            "title": "Booking cut-over",
            "kind": "decision",
            "summary": "Cut over booking-api on Friday.",
            "updated_at": NOW.isoformat(),
            "first_seen": "2026-11-03",
            "changed_at": "2026-11-09",
        }

    def test_a_page_without_a_timeline_has_no_first_seen_or_changed_at(self) -> None:
        result = build(FakeBackend().add(page(ORG, "notes/x", "Plain.")), ORG)
        found = node(result, "org/notes/x")
        assert (found["kind"], found["first_seen"], found["changed_at"]) == ("page", None, None)

    @pytest.mark.parametrize(
        ("page_type", "slug", "kind"),
        [
            ("decision", "anything/x", "decision"),
            ("note", "people/ines", "person"),
            ("", "services/booking-api", "service"),
            ("", "tickets/lin-1", "ticket"),
            ("", "runbooks/rollback", "runbook"),
            ("entity", "misc/x", "page"),
            ("", "x", "page"),
        ],
    )
    def test_kind_comes_from_the_page_type_then_the_first_folder(
        self, page_type: str, slug: str, kind: str
    ) -> None:
        assert page_kind(page_type, slug) == kind

    def test_a_typed_relation_line_is_an_edge_with_its_verb(self) -> None:
        backend = FakeBackend().add(
            page(
                A,
                "people/ines",
                "Ines leads the cut-over.\n\n- [[services/booking-api]] — rel: owns — the api\n",
            ),
            page(A, "services/booking-api", "The booking API."),
        )
        result = build(backend, A)
        assert edges(result) == {
            (f"{A.id}/people/ines", f"{A.id}/services/booking-api", "owns", None)
        }

    def test_a_bare_mention_is_a_mentions_edge(self) -> None:
        backend = FakeBackend().add(
            page(A, "decisions/d", "We follow [[runbooks/rollback]] when it fails."),
            page(A, "runbooks/rollback", "Roll back."),
        )
        assert edges(build(backend, A)) == {
            (f"{A.id}/decisions/d", f"{A.id}/runbooks/rollback", "mentions", None)
        }

    def test_a_slug_that_is_typed_and_also_mentioned_is_one_typed_edge(self) -> None:
        backend = FakeBackend().add(
            page(
                A,
                "people/ines",
                "Ines owns [[services/api]].\n- [[services/api]] — rel: owns — it\n",
            ),
            page(A, "services/api", "The api."),
        )
        assert {e[2] for e in edges(build(backend, A))} == {"owns"}

    def test_aliases_anchors_and_case_in_a_link_still_resolve(self) -> None:
        backend = FakeBackend().add(
            page(A, "a", "See [[Services/API|the api]] and [[services/api#auth]]."),
            page(A, "services/api", "The api."),
        )
        assert len(build(backend, A)["edges"]) == 1
        assert normalize_slug(" /Services/API|x ") == "services/api"

    def test_a_link_resolves_to_its_own_layer_before_any_other(self) -> None:
        backend = FakeBackend().add(
            page(A, "decisions/d", "Follows [[runbooks/rollback]]."),
            page(A, "runbooks/rollback", "Project rollback."),
            page(ORG, "runbooks/rollback", "Org rollback."),
            page(ORG, "decisions/o", "Follows [[runbooks/rollback]]."),
        )
        assert edges(build(backend, ORG, A)) == {
            (f"{A.id}/decisions/d", f"{A.id}/runbooks/rollback", "mentions", None),
            ("org/decisions/o", "org/runbooks/rollback", "mentions", None),
        }

    def test_a_link_to_a_page_only_another_layer_holds_goes_nearest_first(self) -> None:
        backend = FakeBackend().add(
            page(A, "decisions/d", "Needs [[standards/security]] and [[people/ines]]."),
            page(B, "people/ines", "Ines in billing."),
            page(ORG, "people/ines", "Ines in the org."),
            page(ORG, "standards/security", "The standard."),
        )
        # the org is listed first, but a project is nearer to a project page than the org is
        result = build(backend, ORG, A, B)
        assert edges(result) == {
            (f"{A.id}/decisions/d", "org/standards/security", "mentions", None),
            (f"{A.id}/decisions/d", f"{B.id}/people/ines", "mentions", None),
        }

    def test_a_link_by_name_finds_the_one_page_of_that_name(self) -> None:
        """The wiki links `[[ines-moreau]]` to `people/ines-moreau`; seen live on import."""
        backend = FakeBackend().add(
            page(A, "projects/cutover", "[[ines-moreau]] — rel: leads — owns it."),
            page(A, "people/ines-moreau", "Ines."),
        )
        result = build(backend, A)
        assert edges(result) == {
            (f"{A.id}/projects/cutover", f"{A.id}/people/ines-moreau", "leads", None),
        }

    def test_a_name_two_pages_share_is_not_guessed(self) -> None:
        backend = FakeBackend().add(
            page(A, "projects/cutover", "Ask [[ines]]."),
            page(A, "people/ines", "Ines M."),
            page(A, "customers/ines", "Ines Ltd."),
        )
        result = build(backend, A)
        assert result["edges"] == []
        assert result["unresolved"] == [
            {"source": f"{A.id}/projects/cutover", "target_slug": "ines"}
        ]

    def test_a_link_to_no_readable_page_is_listed_not_drawn(self) -> None:
        backend = FakeBackend().add(
            page(A, "decisions/d", "Needs [[standards/security]] and [[people/ines]]."),
            page(ORG, "people/ines", "Ines."),
        )
        result = build(backend, A)  # the org layer is not among the layers asked for
        assert result["edges"] == []
        assert result["unresolved"] == [
            {"source": f"{A.id}/decisions/d", "target_slug": "people/ines"},
            {"source": f"{A.id}/decisions/d", "target_slug": "standards/security"},
        ]

    def test_a_page_linking_itself_makes_no_edge(self) -> None:
        backend = FakeBackend().add(page(A, "x", "About [[x]]."))
        assert build(backend, A)["edges"] == []

    def test_a_timeline_row_citing_a_source_is_a_dated_cites_edge_to_a_source_node(self) -> None:
        backend = FakeBackend().add(
            page(
                A,
                "decisions/d",
                "Decided.",
                rows=(
                    row("2026-11-08", "Re-confirmed", f"source:{SRC_1}"),
                    row("2026-11-03", "Decided", f"source:{SRC_1}", "statement:s1"),
                ),
            ),
            source(A, SRC_1, "LIN-482 Booking cut-over plan"),
            source(A, SRC_2, "LIN-999 never cited"),
        )
        result = build(backend, A)
        source_id = f"{A.id}/_sources/{SRC_1}"
        assert edges(result) == {(f"{A.id}/decisions/d", source_id, "cites", "2026-11-03")}
        assert node(result, source_id) == {
            "id": source_id,
            "layer": A.id,
            "path": f"_sources/{SRC_1}",
            "title": "LIN-482 Booking cut-over plan",
            "kind": "ticket",
            "summary": "",
            "updated_at": NOW.isoformat(),
            "first_seen": NOW.isoformat(),
            "changed_at": None,
        }
        assert {n["id"] for n in result["nodes"]} == {f"{A.id}/decisions/d", source_id}

    def test_only_a_ticket_source_is_a_ticket_node(self) -> None:
        backend = FakeBackend().add(
            page(A, "p", "P.", rows=(row("2026-11-03", "Read", f"source:{SRC_1}"),)),
            source(A, SRC_1, "Design doc", kind="document"),
        )
        assert node(build(backend, A), f"{A.id}/_sources/{SRC_1}")["kind"] == "source"

    def test_a_cited_source_resolves_to_its_own_layer_then_the_nearest(self) -> None:
        backend = FakeBackend().add(
            page(A, "p", "P.", rows=(row("2026-11-03", "Read", f"source:{SRC_ORG}"),)),
            source(ORG, SRC_ORG, "Org policy", kind="document"),
        )
        result = build(backend, ORG, A)
        assert edges(result) == {
            (f"{A.id}/p", f"org/_sources/{SRC_ORG}", "cites", "2026-11-03"),
        }

    def test_a_cited_source_nobody_holds_is_unresolved(self) -> None:
        backend = FakeBackend().add(
            page(A, "p", "P.", rows=(row("2026-11-03", "Read", f"source:{SRC_1}"),)),
        )
        result = build(backend, A)
        assert result["edges"] == []
        assert result["unresolved"] == [{"source": f"{A.id}/p", "target_slug": f"source:{SRC_1}"}]

    def test_no_citation_reads_no_sources(self) -> None:
        backend = FakeBackend().add(page(A, "p", "P."))
        build(backend, A)
        assert backend.count("list_sources") == 0
        assert backend.count("get_source") == 0

    def test_a_link_in_a_timeline_row_dates_the_edge(self) -> None:
        backend = FakeBackend().add(
            page(
                A,
                "people/ines",
                "- [[services/api]] — rel: owns — the api",
                rows=(
                    row("2026-11-09", "Re-affirmed [[services/api]]"),
                    row("2026-11-02", "Took over", detail="now owns [[Services/API]]"),
                    row("2026-11-04", "Something else"),
                ),
            ),
            page(A, "services/api", "The api."),
        )
        assert edges(build(backend, A)) == {
            (f"{A.id}/people/ines", f"{A.id}/services/api", "owns", "2026-11-02")
        }

    def test_nodes_and_edges_come_back_in_a_stable_order(self) -> None:
        backend = FakeBackend().add(
            page(A, "b", "[[c]] [[a]]"), page(A, "a", "A."), page(A, "c", "C.")
        )
        result = build(backend, A)
        assert [e["target"] for e in result["edges"]] == [f"{A.id}/a", f"{A.id}/c"]


class TestFirstSentence:
    @pytest.mark.parametrize(
        ("truth", "expected"),
        [
            ("Cut over on Friday. Then rest.", "Cut over on Friday."),
            (
                "## Compiled Truth\n\n# Title\n\nFirst line\ncontinues here. Second.",
                "First line continues here.",
            ),
            ("- [[services/api]] — rel: owns — the api\n\nThe real prose? Yes.", "The real prose?"),
            ("**Ines** leads [[people/ines|her]] team", "Ines leads people/ines team"),
            ("", ""),
            ("## Only a heading", ""),
            ("v1.2 ships Friday", "v1.2 ships Friday"),
        ],
    )
    def test_the_first_sentence_of_the_first_paragraph_of_prose(
        self, truth: str, expected: str
    ) -> None:
        assert first_sentence(truth) == expected


class TestFailures:
    def test_a_layer_that_cannot_be_listed_fails_the_response_naming_it(self) -> None:
        backend = FakeBackend().add(page(ORG, "x", "X."), page(A, "y", "Y."))
        backend.fail[("list_pages", A.namespace)] = MemoryBackendError("gbrain is down")
        with pytest.raises(GraphLayerError) as caught:
            build(backend, ORG, A)
        assert A.id in str(caught.value) and "gbrain is down" in str(caught.value)
        assert caught.value.layer == A.id

    def test_a_page_that_cannot_be_read_fails_the_response_naming_its_layer(self) -> None:
        backend = FakeBackend().add(page(ORG, "x", "X."), page(B, "y", "Y."))
        backend.fail[("get_page", B.namespace)] = MemoryBackendError("timeout")
        with pytest.raises(GraphLayerError, match=B.id):
            build(backend, ORG, B)

    def test_a_source_that_cannot_be_read_fails_the_response_naming_its_layer(self) -> None:
        backend = FakeBackend().add(
            page(A, "p", "P.", rows=(row("2026-11-03", "Read", f"source:{SRC_1}"),)),
            source(A, SRC_1, "T"),
        )
        backend.fail[("get_source", A.namespace)] = MemoryBackendError("integrity check failed")
        with pytest.raises(GraphLayerError, match=A.id):
            build(backend, A)

    def test_a_truncated_timeline_fails_rather_than_drawing_missing_edges(self) -> None:
        backend = FakeBackend().add(
            page(A, "p", "P.", omitted="gbrain returned 1000 rows. Raise the timeline_limit")
        )
        with pytest.raises(GraphLayerError, match="timeline_limit"):
            build(backend, A)

    def test_a_layer_over_the_cap_fails_loudly_and_is_counted(self) -> None:
        backend = FakeBackend().add(*(page(A, f"p{i}", "P.") for i in range(4)))
        with pytest.raises(GraphTooLargeError, match=MAX_RECORDS_KNOB) as caught:
            build(backend, A, max_records=3)
        assert A.id in str(caught.value)
        assert cap_hit_counts()[MAX_RECORDS_KNOB] == 1

    def test_a_layer_exactly_at_the_cap_is_fine(self) -> None:
        backend = FakeBackend().add(*(page(A, f"p{i}", "P.") for i in range(3)))
        assert len(build(backend, A, max_records=3)["nodes"]) == 3
        assert cap_hit_counts() == {}

    def test_a_layer_with_too_many_sources_fails_loudly_and_is_counted(self) -> None:
        backend = FakeBackend().add(
            page(A, "p", "P.", rows=(row("2026-11-03", "Read", f"source:{SRC_1}"),)),
            source(A, SRC_1, "one"),
            source(A, SRC_2, "two"),
        )
        with pytest.raises(GraphTooLargeError, match="sources"):
            build(backend, A, max_records=1)
        assert cap_hit_counts()[MAX_RECORDS_KNOB] == 1

    def test_an_org_without_a_memory_system_is_not_hidden(self) -> None:
        with pytest.raises(TenantNotConfiguredError):
            asyncio.run(graph_of(FakeBackend()).graph("globex", layers(ORG)))

    def test_a_failure_is_not_cached(self) -> None:
        backend = FakeBackend().add(page(A, "y", "Y."))
        graph = graph_of(backend)
        backend.fail[("list_pages", A.namespace)] = MemoryBackendError("down")
        with pytest.raises(GraphLayerError):
            asyncio.run(graph.graph("acme", layers(A)))
        del backend.fail[("list_pages", A.namespace)]
        assert len(asyncio.run(graph.graph("acme", layers(A)))["nodes"]) == 1


class TestConcurrencyAndCache:
    def test_reads_are_bounded_by_the_configured_concurrency(self) -> None:
        backend = FakeBackend().add(*(page(A, f"p{i}", "P.") for i in range(12)))
        build(backend, A, concurrency=3)
        assert backend.count("get_page") == 12
        assert backend.peak == 3

    def test_a_second_request_inside_the_window_is_served_from_memory(self) -> None:
        backend = FakeBackend().add(page(A, "p", "P."))
        clock = Clock()
        graph = graph_of(backend, cache_seconds=30, clock=clock)
        first = asyncio.run(graph.graph("acme", layers(ORG, A)))
        reads = len(backend.calls)
        clock.now += 29
        again = asyncio.run(graph.graph("acme", layers(A, ORG)))  # the same set, any order
        assert again is first
        assert len(backend.calls) == reads

    def test_a_request_after_the_window_rebuilds(self) -> None:
        backend = FakeBackend().add(page(A, "p", "P."))
        clock = Clock()
        graph = graph_of(backend, cache_seconds=30, clock=clock)
        asyncio.run(graph.graph("acme", layers(A)))
        clock.now += 30
        backend.add(page(A, "q", "Q."))
        assert len(asyncio.run(graph.graph("acme", layers(A)))["nodes"]) == 2

    def test_another_layer_set_is_not_served_from_the_cache(self) -> None:
        backend = FakeBackend().add(page(A, "p", "P."), page(ORG, "o", "O."))
        graph = graph_of(backend)
        both = asyncio.run(graph.graph("acme", layers(ORG, A)))
        narrowed = asyncio.run(graph.graph("acme", layers(A)))
        assert len(both["nodes"]) == 2
        assert len(narrowed["nodes"]) == 1

    def test_zero_seconds_keeps_nothing(self) -> None:
        backend = FakeBackend().add(page(A, "p", "P."))
        graph = graph_of(backend, cache_seconds=0)
        asyncio.run(graph.graph("acme", layers(A)))
        asyncio.run(graph.graph("acme", layers(A)))
        assert backend.count("list_pages") == 2

    def test_concurrent_identical_requests_build_once(self) -> None:
        backend = FakeBackend().add(page(A, "p", "P."))
        graph = graph_of(backend)

        async def both():
            return await asyncio.gather(
                graph.graph("acme", layers(A)), graph.graph("acme", layers(A))
            )

        first, second = asyncio.run(both())
        assert first is second
        assert backend.count("list_pages") == 1

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({"concurrency": 0}, "graph_concurrency"),
            ({"max_records": 0}, MAX_RECORDS_KNOB),
            ({"cache_seconds": -1}, "graph_cache_seconds"),
        ],
    )
    def test_nonsense_settings_are_refused_at_construction(self, options, message) -> None:
        with pytest.raises(ValueError, match=message):
            graph_of(FakeBackend(), **options)


FORGE = "http://forge.test"
PROJECTS = f"{FORGE}/api/v1/forge/projects"
PERSON = Person("ines", "acme", (), "Bearer person-token")


def readable(person: Person = PERSON):
    async def go():
        async with httpx.AsyncClient() as client:
            return await readable_layers(person, forge_base_url=FORGE + "/", client=client)

    return asyncio.run(go())


class TestLayers:
    def test_org_then_each_member_project_in_forges_order(self) -> None:
        with respx.mock() as router:
            route = router.get(PROJECTS).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {"id": PROJECT_B.upper(), "name": " Billing "},
                        {"id": PROJECT_A, "name": "Booking platform"},
                        {"id": PROJECT_A, "name": "Booking platform"},
                    ],
                )
            )
            found = readable()
        assert [(item.layer.id, item.name) for item in found] == [
            ("org", "Org"),
            (B.id, "Billing"),
            (A.id, "Booking platform"),
        ]
        assert route.calls.last.request.headers["authorization"] == "Bearer person-token"

    def test_a_person_with_no_projects_reads_the_org(self) -> None:
        with respx.mock() as router:
            router.get(PROJECTS).mock(return_value=httpx.Response(200, json=[]))
            assert [item.layer.id for item in readable()] == ["org"]

    def test_no_authorization_header_is_sent_when_the_person_has_none(self) -> None:
        with respx.mock() as router:
            route = router.get(PROJECTS).mock(return_value=httpx.Response(200, json=[]))
            readable(Person("dev", "acme", ()))
        assert "authorization" not in route.calls.last.request.headers

    @pytest.mark.parametrize("status", [401, 403])
    def test_forge_refusing_the_login_is_a_refusal(self, status: int) -> None:
        with respx.mock() as router:
            router.get(PROJECTS).mock(return_value=httpx.Response(status))
            with pytest.raises(PersonRefusedError) as caught:
                readable()
        assert caught.value.status == status

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500),
            httpx.Response(200, text="not json"),
            httpx.Response(200, json={"projects": []}),
            httpx.Response(200, json=["a"]),
            httpx.Response(200, json=[{"name": "no id"}]),
            httpx.Response(200, json=[{"id": "Not A Layer!", "name": "x"}]),
            httpx.Response(200, json=[{"id": PROJECT_A}]),
        ],
    )
    def test_an_unusable_answer_from_forge_raises(self, response: httpx.Response) -> None:
        with respx.mock() as router:
            router.get(PROJECTS).mock(return_value=response)
            with pytest.raises(ForgeUnavailableError):
                readable()

    def test_forge_being_unreachable_raises(self) -> None:
        with respx.mock() as router:
            router.get(PROJECTS).mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(ForgeUnavailableError, match="refused"):
                readable()


class TestMarkdownLinks:
    def test_a_relative_markdown_link_is_a_mention(self) -> None:
        """The old wiki linked pages as `[text](../technical/x.md)`; seen live on import."""
        backend = FakeBackend().add(
            page(A, "learnings/retrieval", "See [the architecture](../technical/arch.md#top)."),
            page(A, "technical/arch", "Arch."),
        )
        result = build(backend, A)
        assert edges(result) == {
            (f"{A.id}/learnings/retrieval", f"{A.id}/technical/arch", "mentions", None),
        }

    def test_a_web_link_is_not_a_page_link(self) -> None:
        backend = FakeBackend().add(page(A, "p", "See [docs](https://example.com/x.md)."))
        result = build(backend, A)
        assert result["edges"] == [] and result["unresolved"] == []

    def test_a_link_above_the_layer_root_is_listed_unresolved(self) -> None:
        backend = FakeBackend().add(page(A, "p", "See [x](../../outside.md)."))
        result = build(backend, A)
        assert result["edges"] == []
        assert len(result["unresolved"]) == 1
