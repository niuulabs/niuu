"""GBrainBackend: the MemoryBackend contract, and what is particular to gbrain.

Each layer is a gbrain source with a client of its own (mimir.adapters.gbrain_layers).
Runs against :class:`FakeGBrain` (tests/test_mimir/unit/memory_fakes.py), which models
sources, per-client bound write sources, scopes, the owner API and the token endpoint,
and refuses what gbrain refuses. It is not a live gbrain.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import respx

from mimir.adapters.gbrain_layers import call_tool, source_id_of
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
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    SIGNING_KEY,
    FakeGBrain,
    gbrain_backend,
)

A = "project-a"
B = "project-b"
SID_A = source_id_of(A)
SID_B = source_id_of(B)


@pytest.fixture
def brain():
    fake = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
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
            GBrainBackend(MCP, admin_token="tok")

    def test_the_admin_token_is_required(self) -> None:
        with pytest.raises(ValueError, match="admin token"):
            GBrainBackend(MCP, signing_key="k")

    def test_only_protocol_1_is_spoken(self) -> None:
        with pytest.raises(ValueError, match="protocol 1"):
            gbrain_backend(protocol_version=2)

    @pytest.mark.parametrize("knob", ["page_size", "recall_limit", "timeline_limit"])
    def test_limits_must_be_positive(self, knob) -> None:
        with pytest.raises(ValueError, match=knob):
            gbrain_backend(**{knob: 0})

    def test_the_server_root_is_the_mcp_url_without_mcp(self) -> None:
        GBrainBackend("https://brain.test/mcp/", admin_token="t", signing_key="k")

    def test_an_mcp_url_not_ending_in_mcp_needs_a_base_url(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            GBrainBackend("https://brain.test/rpc", admin_token="t", signing_key="k")
        GBrainBackend(
            "https://brain.test/rpc",
            admin_token="t",
            signing_key="k",
            base_url="https://brain.test",
        )

    def test_secrets_come_from_files(self, tmp_path) -> None:
        (tmp_path / "admin").write_text("tok\n")
        (tmp_path / "key").write_text("key\n")
        GBrainBackend(
            MCP,
            admin_token_file=str(tmp_path / "admin"),
            signing_key_file=str(tmp_path / "key"),
        )

    def test_the_old_api_token_is_not_accepted(self) -> None:
        with pytest.raises(TypeError):
            GBrainBackend(MCP, api_token="tok", signing_key="k")  # type: ignore[call-arg]


class TestLayout:
    async def test_a_layer_is_a_source_of_its_own_with_pages_under_their_own_slugs(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        await backend.put_page(
            A, "services/x", PageWrite("x"), attribution(), expected_revision=None
        )
        await backend.put_source(A, NewSource("t", "raw", SourceKind.DOCUMENT), attribution())
        remembered = await backend.remember(A, new_fact("x is y", subject="services/x"))
        assert brain.sources[SID_A]["name"] == A
        slugs = set(brain.sources[SID_A]["pages"])
        assert "services/x" in slugs
        assert any(s.startswith("_sources/src_") for s in slugs)
        raw_id = remembered.fact.fact.id.split("@")[0]
        assert f"_facts/{raw_id}" in slugs
        assert brain.facts[0]["source_id"] == SID_A
        assert brain.facts[0]["entity_slug"] == "services/x"
        assert remembered.fact.fact.id.endswith(f"@{A}/services/x")
        assert brain.sources["default"]["pages"] == {}

    async def test_source_ids_are_valid_and_one_to_one_with_the_namespace(self) -> None:
        ids = {
            ns: source_id_of(ns)
            for ns in (
                "org",
                A,
                B,
                "run-a1b2c3d4-0000-4000-8000-000000000123",
                "session-5e551011-0000-4000-8000-000000000001",
                "session-5e551011-0000-4000-8000-000000000002",
            )
        }
        assert ids["org"] == "org"
        assert len(set(ids.values())) == len(ids)
        for source_id in ids.values():
            assert 1 <= len(source_id) <= 32
            assert "_" not in source_id
        assert ids[A].startswith("p-") and ids[
            "session-5e551011-0000-4000-8000-000000000001"
        ].startswith("s-")

    async def test_writes_land_in_the_layers_own_source_through_its_own_client(
        self, brain, backend
    ) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
            await backend.put_page(ns, "p", PageWrite(ns), attribution(), expected_revision=None)
        assert brain.sources[SID_A]["pages"]["p"]["compiled_truth"] == A
        assert brain.sources[SID_B]["pages"]["p"]["compiled_truth"] == B
        writers = {(c, a["slug"]) for c, n, a in brain.calls_by_client if n == "put_page"}
        assert writers == {(f"mimir-layer-{A}", "p"), (f"mimir-layer-{B}", "p")}

    async def test_proposals_are_pages_of_the_layers_source_under_a_prefix(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        await backend.create_namespace(f"_proposals/{A}")
        await backend.put_page(A, "p", PageWrite("x"), attribution(), expected_revision=None)
        await backend.put_page(
            f"_proposals/{A}", "p-1", PageWrite("prop"), attribution(), expected_revision=None
        )
        assert set(brain.sources[SID_A]["pages"]) == {"p", "_proposals/p-1"}
        assert [r.slug for r in await backend.list_pages(A, limit=10)] == ["p"]
        assert [r.slug for r in await backend.list_pages(f"_proposals/{A}", limit=10)] == ["p-1"]
        assert (await backend.get_page(f"_proposals/{A}", "p-1")).compiled_truth == "prop"
        with pytest.raises(RecordNotFoundError):
            await backend.get_page(A, "p-1")
        assert len(brain.sources) == 2  # default and the layer: proposals made no source

    async def test_deleting_proposals_leaves_the_layer(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(A, "p", PageWrite("x"), attribution(), expected_revision=None)
        await backend.put_page(
            f"_proposals/{A}", "p-1", PageWrite("prop"), attribution(), expected_revision=None
        )
        removal = await backend.delete_namespace(f"_proposals/{A}")
        assert removal.pages_removed == 1
        assert (await backend.get_page(A, "p")).compiled_truth == "x"
        assert await backend.list_pages(f"_proposals/{A}", limit=10) == []

    @pytest.mark.parametrize("slug", ["_sources/x", "Services/X", "a//b", "a b", "../x", ""])
    async def test_reserved_or_malformed_slugs_are_refused(self, backend, slug) -> None:
        await backend.create_namespace(A)
        with pytest.raises(MemoryValidationError):
            await backend.put_page(A, slug, PageWrite("x"), attribution(), expected_revision=None)

    @pytest.mark.parametrize(
        "namespace", ["", "other-a", "attached-docs", "org-x", "project-", "_proposals/", "a/b"]
    )
    async def test_only_layers_of_the_orgs_system_are_namespaces(self, backend, namespace) -> None:
        with pytest.raises(MemoryValidationError):
            await backend.create_namespace(namespace)

    async def test_a_slug_longer_than_gbrain_keeps_is_refused_and_counted(self, backend) -> None:
        small = gbrain_backend(max_slug_chars=20)
        await small.create_namespace(A)
        with pytest.raises(MemoryValidationError, match="too long"):
            await small.put_page(A, "a" * 30, PageWrite("x"), attribution(), expected_revision=None)
        assert cap_hit_counts()["memory.systems[].max_slug_chars"] == 1

    async def test_think_is_never_called(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.search([A], "anything", kinds=frozenset(HitKind), limit=5)
        assert "think" not in {name for name, _ in brain.calls}


class TestScoping:
    async def test_gbrain_itself_refuses_a_layer_client_another_layers_source(
        self, brain, backend
    ) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
        mcp = await backend._layers.client(A, create=False)
        with pytest.raises(MemoryBackendError, match="permission_denied"):
            await call_tool(mcp, "query", {"query": "x", "source_id": SID_B})
        with pytest.raises(MemoryBackendError, match="permission_denied"):
            await call_tool(mcp, "get_page", {"slug": "p", "source_id": SID_B})
        with pytest.raises(MemoryBackendError, match="permission_denied"):
            await call_tool(mcp, "put_page", {"slug": "p", "content": "x", "source_id": SID_B})

    async def test_reads_and_search_of_one_layer_never_see_another(self, brain, backend) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
        await backend.put_page(
            A, "only-a", PageWrite("cutover a"), attribution(), expected_revision=None
        )
        await backend.put_page(
            B, "only-b", PageWrite("cutover b"), attribution(), expected_revision=None
        )
        await backend.remember(B, new_fact("b fact"))
        hits = await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=None)
        assert [(h.namespace, h.ref) for h in hits] == [(A, "only-a")]
        with pytest.raises(RecordNotFoundError):
            await backend.get_page(A, "only-b")
        assert [p.slug for p in await backend.list_pages(A, limit=10)] == ["only-a"]
        assert (await backend.recall([A], subject=None)).facts == ()
        queries = [(c, a) for c, n, a in brain.calls_by_client if n == "query"]
        assert queries == [(f"mimir-layer-{A}", queries[0][1])]
        assert queries[0][1]["source_id"] == SID_A

    async def test_listing_a_layer_never_pages_through_other_layers(self, brain, backend) -> None:
        brain.list_cap = 2
        brain.seed_page("org/noise-1", "x")
        brain.seed_page("org/noise-2", "x")
        await backend.create_namespace(A)
        for index in range(3):
            await backend.put_page(
                A, f"p{index}", PageWrite("x"), attribution(), expected_revision=None
            )
        assert [p.slug for p in await backend.list_pages(A, limit=10)] == ["p0", "p1", "p2"]
        listed = [a for n, a in brain.calls if n == "list_pages"]
        assert all(a["source_id"] == SID_A for a in listed)
        assert [a["offset"] for a in listed] == [0, 2, 3]

    async def test_a_layer_with_no_source_reads_as_empty_and_is_not_created_by_a_read(
        self, brain, backend
    ) -> None:
        assert await backend.list_pages(A, limit=5) == []
        assert await backend.list_sources(A, limit=5) == []
        assert await backend.search([A], "x", kinds=frozenset(HitKind), limit=None) == []
        assert (await backend.recall([A], subject=None)).facts == ()
        with pytest.raises(RecordNotFoundError):
            await backend.get_page(A, "p")
        assert SID_A not in brain.sources
        assert brain.live_clients() == []

    async def test_a_write_to_a_layer_never_created_raises_with_the_remedy(self, backend) -> None:
        with pytest.raises(MemoryBackendError, match="create_namespace"):
            await backend.put_page(A, "p", PageWrite("x"), attribution(), expected_revision=None)
        with pytest.raises(MemoryBackendError, match="create_namespace"):
            await backend.remember(A, new_fact("x"))

    async def test_changes_follow_one_layer(self, backend) -> None:
        await backend.create_namespace(A)
        await backend.create_namespace(B)
        with pytest.raises(MemoryValidationError, match="one layer"):
            await backend.delta([A, B], since="2000-01-01T00:00:00+00:00", limit=5)

    async def test_search_refuses_a_proposals_namespace(self, backend) -> None:
        with pytest.raises(MemoryValidationError, match="proposals"):
            await backend.search([f"_proposals/{A}"], "x", kinds=frozenset(HitKind), limit=None)

    async def test_a_hit_from_another_source_raises(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(A, "p", PageWrite("cutover"), attribution(), expected_revision=None)
        original = brain._query
        brain._query = lambda source_id, args: [
            {**row, "source_id": SID_B} for row in original(source_id, args)
        ]
        with pytest.raises(MemoryBackendError, match="wrong source"):
            await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=None)


class TestSearchSendsNoLimit:
    async def test_one_query_per_layer_source_without_limit_or_offset(self, brain, backend) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
            await backend.put_page(
                ns, "p", PageWrite("cutover"), attribution(), expected_revision=None
            )
        await backend.search([A, B], "cutover", kinds=frozenset(HitKind), limit=None)
        queries = [a for n, a in brain.calls if n == "query"]
        assert sorted(a["source_id"] for a in queries) == sorted([SID_A, SID_B])
        for args in queries:
            assert "limit" not in args and "offset" not in args
            assert args["expand"] is False and args["autocut"] is False

    async def test_gbrains_own_search_limit_decides_how_many_come_back(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        for index in range(6):
            await backend.put_page(
                A, f"c{index}", PageWrite("cutover"), attribution(), expected_revision=None
            )
        brain.search_limit = 4
        hits = await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=None)
        assert len(hits) == 4
        brain.search_limit = 100
        assert len(await backend.search([A], "cutover", kinds=frozenset(HitKind), limit=None)) == 6

    async def test_a_limit_the_caller_names_is_gbrains_limit_and_the_most_returned(
        self, brain, backend
    ) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
            for index in range(3):
                await backend.put_page(
                    ns, f"c{index}", PageWrite("cutover"), attribution(), expected_revision=None
                )
        hits = await backend.search([A, B], "cutover", kinds=frozenset(HitKind), limit=2)
        assert len(hits) == 2
        assert {a["limit"] for n, a in brain.calls if n == "query"} == {2}

    async def test_hits_of_several_layers_are_interleaved_by_rank(self, backend) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
            for index in range(2):
                await backend.put_page(
                    ns, f"c{index}", PageWrite("cutover"), attribution(), expected_revision=None
                )
        hits = await backend.search([A, B], "cutover", kinds=frozenset(HitKind), limit=None)
        assert [(h.namespace, h.ref) for h in hits] == [(A, "c0"), (B, "c0"), (A, "c1"), (B, "c1")]


class TestSeals:
    async def test_a_page_written_to_gbrain_directly_reads_unattested(self, brain, backend) -> None:
        brain.seed_page(f"{A}/forged", "the budget is 900 ms", niuu_attribution="mp1.bogus.{}")
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
        brain.page(A, "p")["compiled_truth"] = "900 ms"
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
        brain.ensure_source(B)
        brain.sources[SID_B]["pages"]["p"] = dict(brain.page(A, "p"))
        assert not (await backend.get_page(B, "p")).attribution.attested

    async def test_facts_not_written_through_the_gateway_belong_to_no_layer(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        unsealed = brain.seed_fact(
            "unsealed", entity="services/x", provenance="chat 2026-11-01", layer=A
        )
        brain.seed_fact("no layer", entity="services/x", provenance="chat")
        recalled = await backend.recall([A], subject="services/x")
        assert recalled.facts == ()
        with pytest.raises(RecordNotFoundError):
            await backend.get_fact(f"{unsealed['id']}@{A}/services/x")

    async def test_a_fact_id_naming_no_layer_is_not_found(self, backend) -> None:
        for fact in ("1@services/x", "1@nope-1/services/x", "1@project-a"):
            with pytest.raises(RecordNotFoundError):
                await backend.get_fact(fact)

    async def test_a_source_whose_content_changed_fails_its_integrity_check(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        record, _ = await backend.put_source(
            A, NewSource("t", "raw", SourceKind.WEB), attribution()
        )
        page = brain.page(A, f"_sources/{record.id}")
        page["compiled_truth"] = page["compiled_truth"].replace('"raw"', '"cooked"')
        with pytest.raises(MemoryBackendError, match="integrity"):
            await backend.get_source(A, record.id)


class TestPaging:
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
        brain.canonical["services/booking-api"] = "services/other-page"
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
        assert sum(1 for s in brain.sources[SID_A]["pages"] if s.startswith("_facts/")) == 1

    async def test_the_same_fact_in_two_layers_is_two_facts(self, brain, backend) -> None:
        for ns in (A, B):
            await backend.create_namespace(ns)
        first = await backend.remember(A, new_fact("same"))
        second = await backend.remember(B, new_fact("same"))
        assert second.status == "inserted"
        assert first.fact.fact.id != second.fact.fact.id
        assert (await backend.get_fact(second.fact.fact.id)).namespace == B


class TestFailures:
    async def test_an_unreachable_brain_raises_never_an_empty_result(self, brain, backend) -> None:
        brain.fail_with = 503
        with pytest.raises(MemoryBackendError, match="gbrain"):
            await backend.search([A], "x", kinds=frozenset(HitKind), limit=1)

    async def test_a_tool_error_names_gbrains_code(self, brain, backend) -> None:
        await backend.create_namespace(A)
        brain.fail_tools.add("query")
        with pytest.raises(MemoryBackendError, match=r"query failed \(internal\)"):
            await backend.search([A], "x", kinds=frozenset(HitKind), limit=1)

    async def test_an_answer_without_its_list_raises(self, brain, backend) -> None:
        await backend.create_namespace(A)
        brain._query = lambda source_id, args: {"unexpected": True}  # type: ignore[method-assign]
        with pytest.raises(MemoryBackendError, match="expected list"):
            await backend.search([A], "x", kinds=frozenset(HitKind), limit=1)

    async def test_a_source_that_cannot_be_created_raises_with_the_remedy(
        self, brain, backend
    ) -> None:
        brain.refuse_source_add = True
        with pytest.raises(MemoryBackendError, match=r"managed persistence.*gbrain sources add"):
            await backend.create_namespace(A)
        assert SID_A not in brain.sources
        assert brain.live_clients() == [] or all(
            c["name"] == "mimir-provisioner" for c in brain.live_clients()
        )

    async def test_a_client_secret_gbrain_cannot_return_raises_with_the_remedy(
        self, brain, backend
    ) -> None:
        brain.lose_journal = True
        with pytest.raises(MemoryBackendError, match="revoke the client"):
            await backend.create_namespace(A)

    async def test_a_wrong_admin_token_raises_naming_the_credential(self, brain) -> None:
        wrong = GBrainBackend(MCP, admin_token="not-the-bootstrap-token", signing_key=SIGNING_KEY)
        with pytest.raises(MemoryBackendError, match="admin_token"):
            await wrong.create_namespace(A)
        await wrong.close()

    async def test_an_id_held_by_a_foreign_source_is_refused(self, brain, backend) -> None:
        brain.sources[SID_A] = {"id": SID_A, "name": "somebody's wiki", "pages": {}}
        with pytest.raises(MemoryBackendError, match="holds the id"):
            await backend.create_namespace(A)

    async def test_a_client_left_unbound_by_a_removed_source_is_refused_not_reused(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        stale = next(c for c in brain.live_clients() if c["name"] == f"mimir-layer-{A}")
        stale["source_id"] = None  # gbrain unbinds a client whose source is removed
        del brain.sources[SID_A]
        fresh = gbrain_backend()
        with pytest.raises(MemoryBackendError, match="Revoke it"):
            await fresh.create_namespace(A)
        await fresh.close()

    async def test_a_revoked_clients_token_is_refused_loudly(self, brain, backend) -> None:
        await backend.create_namespace(A)
        for client in brain.live_clients():
            client["revoked"] = True
        brain.tokens.clear()
        with pytest.raises(MemoryBackendError, match="revoked"):
            await backend.put_page(A, "p", PageWrite("x"), attribution(), expected_revision=None)


class TestClients:
    async def test_a_second_replica_gets_the_same_clients_without_registering_new_ones(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        await backend.put_page(A, "p", PageWrite("one"), attribution(), expected_revision=None)
        clients = {c["client_id"]: c["secret"] for c in brain.live_clients()}
        replica = gbrain_backend()
        assert await replica.create_namespace(A) is False
        assert (await replica.get_page(A, "p")).compiled_truth == "one"
        await replica.put_page(A, "q", PageWrite("two"), attribution(), expected_revision=None)
        assert {c["client_id"]: c["secret"] for c in brain.live_clients()} == clients
        assert (await backend.get_page(A, "q")).compiled_truth == "two"
        await replica.close()

    async def test_a_restart_finds_the_layers_it_made(self, brain, backend) -> None:
        for ns in (A, "session-s1"):
            await backend.create_namespace(ns)
        restarted = gbrain_backend()
        assert sorted(await restarted.list_namespaces()) == sorted([A, "session-s1"])
        await restarted.close()

    async def test_each_layer_client_is_bound_to_its_source_with_its_scopes(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        by_name = {c["name"]: c for c in brain.live_clients()}
        layer = by_name[f"mimir-layer-{A}"]
        assert layer["source_id"] == SID_A
        assert set(layer["scopes"]) == {"read", "write", "sources_admin"}
        assert layer["grant_types"] == ["client_credentials"]
        provisioner = by_name["mimir-provisioner"]
        assert provisioner["source_id"] == "default"
        assert provisioner["scopes"] == ["sources_admin"]

    async def test_tokens_are_fetched_once_and_renewed_before_they_expire(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        for index in range(3):
            await backend.put_page(
                A, f"p{index}", PageWrite("x"), attribution(), expected_revision=None
            )
        fetched = brain.token_requests
        assert fetched == 2  # the provisioner's and the layer's, each once
        short = FakeGBrain(base="https://short.test")
        short.token_ttl = 5
        with respx.mock(assert_all_called=False) as router:
            short.mount(router)
            eager = gbrain_backend("https://short.test/mcp", token_refresh_margin_seconds=10)
            await eager.create_namespace(A)
            before = short.token_requests
            await eager.put_page(A, "p", PageWrite("x"), attribution(), expected_revision=None)
            await eager.put_page(A, "q", PageWrite("x"), attribution(), expected_revision=None)
            assert short.token_requests >= before + 2
            await eager.close()

    async def test_the_admin_session_is_renewed_when_it_expires(self, brain, backend) -> None:
        await backend.create_namespace(A)
        brain.expire_admin_sessions()
        assert await backend.list_namespaces() == [A]
        assert sum(1 for m, path in brain.requests if path == "/admin/login") == 2


class TestNamespaces:
    async def test_deleting_a_session_layer_removes_its_source_and_revokes_its_client(
        self, brain, backend
    ) -> None:
        sid = source_id_of("session-s1")
        await backend.create_namespace("session-s1")
        await backend.put_page(
            "session-s1", "p", PageWrite("x"), attribution(), expected_revision=None
        )
        fact = await backend.remember("session-s1", new_fact("goes"))
        client = next(c for c in brain.live_clients() if c["name"] == "mimir-layer-session-s1")
        removal = await backend.delete_namespace("session-s1")
        assert removal.pages_removed >= 1 and removal.facts_withdrawn == 1
        assert sid not in brain.sources
        assert client["revoked"]
        assert brain.facts[0]["expired_at"] is not None
        assert "session-s1" not in await backend.list_namespaces()
        with pytest.raises(RecordNotFoundError):
            await backend.get_fact(fact.fact.fact.id)

    async def test_deleting_leaves_other_layers_and_the_default_source(
        self, brain, backend
    ) -> None:
        for ns in (A, "run-r1"):
            await backend.create_namespace(ns)
            await backend.put_page(ns, "p", PageWrite("x"), attribution(), expected_revision=None)
        await backend.delete_namespace("run-r1")
        assert (await backend.get_page(A, "p")).compiled_truth == "x"
        assert "default" in brain.sources
        assert {c["name"] for c in brain.live_clients()} == {
            f"mimir-layer-{A}",
            "mimir-provisioner",
        }

    async def test_a_layer_can_come_back_after_it_was_deleted(self, brain, backend) -> None:
        await backend.create_namespace("session-s1")
        await backend.delete_namespace("session-s1")
        assert await backend.create_namespace("session-s1") is True
        await backend.put_page(
            "session-s1", "p", PageWrite("again"), attribution(), expected_revision=None
        )
        assert (await backend.get_page("session-s1", "p")).compiled_truth == "again"

    async def test_deleting_a_layer_that_is_already_gone_removes_nothing(self, backend) -> None:
        removal = await backend.delete_namespace("session-never")
        assert (removal.pages_removed, removal.facts_withdrawn) == (0, 0)

    async def test_a_client_left_over_from_a_removed_source_is_found_and_revoked(
        self, brain, backend
    ) -> None:
        await backend.create_namespace("session-s1")
        leftover = next(c for c in brain.live_clients() if c["name"] == "mimir-layer-session-s1")
        del brain.sources[source_id_of("session-s1")]
        fresh = gbrain_backend()
        assert "session-s1" in await fresh.list_namespaces()
        await fresh.delete_namespace("session-s1")
        assert leftover["revoked"]
        assert "session-s1" not in await fresh.list_namespaces()
        await fresh.close()

    async def test_sources_that_are_not_layers_are_not_listed_or_touched(
        self, brain, backend
    ) -> None:
        brain.sources["wiki"] = {"id": "wiki", "name": "wiki", "pages": {}}
        brain.sources["p-" + "0" * 30] = {"id": "p-" + "0" * 30, "name": "project-x", "pages": {}}
        await backend.create_namespace(A)
        assert await backend.list_namespaces() == [A]

    async def test_a_failure_to_list_the_sources_raises(self, brain, backend) -> None:
        brain.fail_paths.add("/admin/api/sources")
        with pytest.raises(MemoryBackendError, match="/admin/api/sources"):
            await backend.list_namespaces()


class TestLinks:
    async def test_backlinks_are_only_the_layers_own_pages(self, brain, backend) -> None:
        await backend.create_namespace(A)
        await backend.put_page(A, "target", PageWrite("t"), attribution(), expected_revision=None)
        brain.seed_page(f"{A}/linker", "see [[target]]")
        brain.seed_page(f"{A}/_proposals/p-1", "see [[target]]")
        brain.seed_page(f"{B}/foreign", "see [[target]]")
        refs = await backend.backlinks(A, "target")
        assert [r.slug for r in refs] == ["linker"]

    async def test_entity_aliases_and_links_come_only_from_its_own_card(
        self, brain, backend
    ) -> None:
        await backend.create_namespace(A)
        brain.set_card(
            A,
            "people/ines",
            {
                "entity": {"slug": "people/ines", "title": "Ines", "type": "person"},
                "aka": ["@ines"],
                "edges": [{"to_slug": "teams/payments"}, {"to_slug": "_facts/7"}],
            },
        )
        brain.set_card(
            A,
            "people/other",
            {"entity": {"slug": "people/elsewhere"}, "aka": ["leak"], "edges": []},
        )
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
