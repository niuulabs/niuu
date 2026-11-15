"""GBrainFactAdapter against a fake gbrain that speaks the memory verbs.

The fake follows the documented MEMORY_VERBS v1 shapes (``remember``, ``recall``
with ``include_expired`` / ``supersessions`` / ``grep``, ``forget``, ``entity``).
It is not a live gbrain: the real brain was not run for these tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from mimir.adapters.gbrain_facts import GBrainFactAdapter, fact_id, split_fact_id
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import (
    AuthorKind,
    FactAuthor,
    FactBackendError,
    FactDraft,
    FactKind,
    FactNotFoundError,
    FactValidationError,
    NewFact,
    OmissionReason,
    SharedOrigin,
    TrustError,
    TrustLevel,
)
from niuu.ports.mimir_facts import MimirFactPort

MCP = "https://brain.test/mcp"
NOW = datetime.now(UTC)
REGIN = FactAuthor(AuthorKind.AGENT, "regin")
JOZEF = FactAuthor(AuthorKind.PERSON, "jozef")


class FakeGBrain:
    """Just enough of gbrain's fact store: rows, expiry, forget, supersession audit."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.next_status = "inserted"
        self.supersede: int | None = None
        self.fail_with: int | None = None
        self.recall_cap = 100

    def seed(self, fact: str, *, provenance: str, entity: str = "people/a", **extra) -> dict:
        row = {
            "id": len(self.rows) + 1,
            "fact": fact,
            "kind": "fact",
            "entity_slug": entity,
            "source": provenance,
            "valid_from": NOW.isoformat(),
            "valid_until": None,
            "expired_at": None,
            "superseded_by": None,
            "context": None,
            "created_at": NOW.isoformat(),
            **extra,
        }
        self.rows.append(row)
        return row

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail_with:
            return httpx.Response(self.fail_with, text="boom")
        body = json.loads(request.content)
        name, args = body["params"]["name"], body["params"]["arguments"]
        self.calls.append((name, args))
        payload = getattr(self, f"_{name}")(args)
        result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
        if "error" in payload:
            result["isError"] = True
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    def _remember(self, args: dict) -> dict:
        if self.next_status == "duplicate":
            existing = self.rows[0]
            return self._remembered(existing, "duplicate")
        row = self.seed(
            args["fact"],
            provenance=args["provenance"],
            entity=args["entity"],
            kind=args["kind"],
            valid_until=args.get("ttl"),
        )
        if self.next_status == "superseded" and self.supersede:
            old = next(r for r in self.rows if r["id"] == self.supersede)
            old["expired_at"] = NOW.isoformat()
            old["superseded_by"] = row["id"]
        return self._remembered(row, self.next_status)

    @staticmethod
    def _remembered(row: dict, status: str) -> dict:
        return {
            "id": str(row["id"]),
            "status": status,
            "entity_slug": row["entity_slug"],
            "valid_until": row["valid_until"],
            "protocol_version": 1,
        }

    def _recall(self, args: dict) -> dict:
        rows = [r for r in self.rows if r["entity_slug"] == args.get("entity", r["entity_slug"])]
        if args.get("supersessions"):
            rows = [r for r in rows if r["superseded_by"] is not None]
        elif not args.get("include_expired"):
            rows = [r for r in rows if r["expired_at"] is None]
        if args.get("grep"):
            rows = [r for r in rows if args["grep"].lower() in r["fact"].lower()]
        rows = rows[: min(args["limit"], self.recall_cap)]
        facts = [{**r, "fact_id": str(r["id"]), "provenance": r["source"]} for r in rows]
        return {"facts": facts, "total": len(facts), "protocol_version": 1}

    def _forget(self, args: dict) -> dict:
        row = next((r for r in self.rows if str(r["id"]) == args["id"]), None)
        if row is None:
            return {"error": "not_found", "message": "no such fact", "protocol_version": 1}
        already = row["expired_at"] is not None
        if not already:
            row["expired_at"] = datetime.now(UTC).isoformat()
            row["context"] = args.get("reason")
        return {
            "id": args["id"],
            "expired": not already,
            "reason": args.get("reason"),
            "protocol_version": 1,
        }

    def _entity(self, args: dict) -> dict:
        slug = args["name"]
        if not any(r["entity_slug"] == slug for r in self.rows):
            return {"found": False, "protocol_version": 1}
        return {
            "found": True,
            "card": {"entity": {"slug": slug, "title": slug, "type": "person"}},
            "protocol_version": 1,
        }


@pytest.fixture
def brain():
    fake = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=fake.handle)
        yield fake


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


def _adapter(**kwargs) -> GBrainFactAdapter:
    return GBrainFactAdapter(MCP, "gbrain_tok", signing_key="s3cret-key", **kwargs)


def _new(
    statement: str = "regin runs on valhalla",
    *,
    author: FactAuthor = REGIN,
    trust: TrustLevel = TrustLevel.AGENT,
    **draft_kwargs,
) -> NewFact:
    draft = FactDraft("people/a", statement, ("page/one.md",), **draft_kwargs)
    return NewFact(draft=draft, author=author, trust=trust)


class TestConstruction:
    def test_is_a_fact_port(self) -> None:
        assert isinstance(_adapter(), MimirFactPort)

    def test_signing_key_is_required(self) -> None:
        with pytest.raises(ValueError, match="signing key"):
            GBrainFactAdapter(MCP, "tok")

    def test_token_is_required(self) -> None:
        with pytest.raises(ValueError, match="API token"):
            GBrainFactAdapter(MCP, "", signing_key="k")

    def test_url_is_required(self) -> None:
        with pytest.raises(ValueError, match="MCP URL"):
            GBrainFactAdapter("", "tok", signing_key="k")

    @pytest.mark.parametrize(
        "kwargs", [{"visibility": "friends"}, {"recall_limit": 0}, {"provenance_max_chars": 0}]
    )
    def test_bad_limits_are_refused(self, kwargs) -> None:
        with pytest.raises(ValueError):
            _adapter(**kwargs)

    def test_secrets_can_come_from_files(self, tmp_path) -> None:
        token, key = tmp_path / "t", tmp_path / "k"
        token.write_text("tok\n")
        key.write_text("key\n")
        GBrainFactAdapter(MCP, api_token_file=str(token), signing_key_file=str(key))


class TestFactIds:
    def test_round_trip(self) -> None:
        assert split_fact_id(fact_id(42, "people/alice")) == ("42", "people/alice")

    @pytest.mark.parametrize("bad", ["42", "42@", "@people/a", " @x"])
    def test_malformed_ids_are_refused(self, bad) -> None:
        with pytest.raises(FactValidationError):
            split_fact_id(bad)


class TestRemember:
    async def test_stores_the_structure_in_sealed_provenance(self, brain) -> None:
        outcome = await _adapter().remember(
            _new(valid_until=NOW + timedelta(days=30), kind=FactKind.BELIEF)
        )
        _, args = brain.calls[0]
        assert args["entity"] == "people/a"
        assert args["kind"] == "belief"
        assert args["visibility"] == "world"
        assert args["ttl"].startswith(str((NOW + timedelta(days=30)).year))
        assert args["provenance"].startswith("mf1.")
        assert len(args["provenance"]) <= 500
        assert outcome.status == "inserted"
        assert outcome.fact.id == "1@people/a"
        assert outcome.fact.provenance.author == REGIN
        assert outcome.fact.provenance.evidence == ("page/one.md",)
        assert outcome.fact.trust is TrustLevel.AGENT
        assert outcome.fact.valid_until is not None

    async def test_the_stored_trust_is_the_one_the_platform_assigned(self, brain) -> None:
        stored = await _adapter().remember(_new(author=JOZEF, trust=TrustLevel.PERSON))
        assert stored.fact.trust is TrustLevel.PERSON

    async def test_claimed_valid_from_survives_the_round_trip(self, brain) -> None:
        start = NOW - timedelta(days=400)
        stored = await _adapter().remember(_new(valid_from=start))
        assert stored.fact.valid_from == start

    async def test_replaces_and_derived_from_are_recorded(self, brain) -> None:
        stored = await _adapter().remember(_new(replaces=("9@people/a",), derived_from=("8@x",)))
        assert stored.fact.provenance.replaces == ("9@people/a",)
        assert stored.fact.provenance.derived_from == ("8@x",)

    async def test_a_duplicate_returns_the_existing_fact_and_says_so(self, brain) -> None:
        adapter = _adapter()
        await adapter.remember(_new())
        brain.next_status = "duplicate"
        outcome = await adapter.remember(_new("regin runs on valhalla", author=JOZEF))
        assert outcome.status == "duplicate"
        assert outcome.fact.provenance.author == REGIN

    async def test_provenance_that_does_not_fit_gbrain_raises_and_counts(self, brain) -> None:
        draft = FactDraft("people/a", "claim", tuple(f"page/{i}-{'x' * 40}.md" for i in range(20)))
        with pytest.raises(FactValidationError, match="keeps at most 500"):
            await _adapter().remember(NewFact(draft, REGIN, TrustLevel.AGENT))
        assert cap_hit_counts()["mimir.facts.gbrain.provenance_max_chars"] == 1
        assert brain.calls == []

    async def test_an_unusable_reply_raises(self, brain) -> None:
        brain._remember = lambda args: {"status": "weird"}  # type: ignore[method-assign]
        with pytest.raises(FactBackendError, match="unusable"):
            await _adapter().remember(_new())

    async def test_native_supersession_of_a_higher_trust_fact_is_refused_loudly(
        self, brain
    ) -> None:
        brain.seed(
            "regin runs on noatun",
            provenance=await _sealed(brain, JOZEF, TrustLevel.PERSON),
        )
        brain.next_status, brain.supersede = "superseded", 1
        with pytest.raises(TrustError, match="person-level fact 1@people/a"):
            await _adapter().remember(_new("regin runs on valhalla"))

    async def test_native_supersession_of_a_lower_trust_fact_is_fine(self, brain) -> None:
        brain.seed("old", provenance="plain")
        brain.next_status, brain.supersede = "superseded", 1
        outcome = await _adapter().remember(_new(author=JOZEF, trust=TrustLevel.PERSON))
        assert outcome.status == "superseded"


async def _sealed(brain, author: FactAuthor, trust: TrustLevel) -> str:
    """The provenance text a real remember by *author* at *trust* would store."""
    await _adapter().remember(_new("seed", author=author, trust=trust))
    text = next(a["provenance"] for n, a in brain.calls if n == "remember")
    brain.rows.pop()
    brain.calls.clear()
    return text


class TestRecall:
    async def test_backend_failure_raises_never_an_empty_result(self, brain) -> None:
        brain.fail_with = 503
        with pytest.raises(FactBackendError, match="gbrain recall failed"):
            await _adapter().recall(subject="people/a")

    async def test_a_verb_error_raises(self, brain) -> None:
        brain._recall = lambda args: {  # type: ignore[method-assign]
            "error": "unavailable",
            "message": "no key",
            "suggestion": "set one",
            "protocol_version": 1,
        }
        with pytest.raises(FactBackendError, match="unavailable.*no key.*set one"):
            await _adapter().recall(subject="people/a")

    async def test_empty_brain_is_a_real_empty_answer(self, brain) -> None:
        recalled = await _adapter().recall(subject="people/a")
        assert recalled.facts == () and recalled.omitted == ()

    async def test_min_trust_filters_and_reports(self, brain) -> None:
        adapter = _adapter()
        await adapter.remember(_new("agent claim"))
        await adapter.remember(
            _new("tool claim", author=FactAuthor(AuthorKind.TOOL, "t"), trust=TrustLevel.TOOL)
        )
        recalled = await adapter.recall(subject="people/a", min_trust=TrustLevel.TOOL)
        assert [f.statement for f in recalled.facts] == ["tool claim"]
        assert recalled.omitted[0].reason is OmissionReason.BELOW_MIN_TRUST

    async def test_expired_facts_are_excluded_as_of_now(self, brain) -> None:
        brain.seed("gone", provenance="p", valid_until=(NOW - timedelta(hours=1)).isoformat())
        brain.seed("here", provenance="p")
        recalled = await _adapter().recall(subject="people/a")
        assert [f.statement for f in recalled.facts] == ["here"]
        assert recalled.omitted[0].reason is OmissionReason.NOT_VALID_AT

    async def test_asking_about_the_past_includes_what_was_retired_since(self, brain) -> None:
        brain.seed(
            "was true",
            provenance="p",
            valid_from=(NOW - timedelta(days=30)).isoformat(),
            expired_at=(NOW - timedelta(days=1)).isoformat(),
        )
        adapter = _adapter()
        now = await adapter.recall(subject="people/a")
        then = await adapter.recall(subject="people/a", as_of=NOW - timedelta(days=10))
        assert now.facts == ()
        assert [f.statement for f in then.facts] == ["was true"]
        assert brain.calls[-1][1]["include_expired"] is True
        assert brain.calls[0][1]["include_expired"] is False

    async def test_contains_is_passed_to_gbrain_as_grep(self, brain) -> None:
        await _adapter().recall(subject="people/a", contains="helm")
        assert brain.calls[0][1]["grep"] == "helm"

    async def test_hitting_gbrains_recall_cap_is_reported_counted_and_logged(
        self, brain, caplog
    ) -> None:
        for n in range(3):
            brain.seed(f"fact {n}", provenance="p")
        recalled = await _adapter(recall_limit=3).recall(subject="people/a")
        assert len(recalled.facts) == 3
        assert recalled.omitted[0].reason is OmissionReason.BACKEND_LIMIT
        assert cap_hit_counts()["mimir.facts.gbrain.recall_limit"] == 1
        assert "cap hit [mimir.facts.gbrain.recall_limit]" in caplog.text

    async def test_a_row_without_a_mimir_envelope_is_unattested_and_lowest(self, brain) -> None:
        brain.seed("from the extractor", provenance="mcp:extract_facts")
        (fact,) = (await _adapter().recall(subject="people/a")).facts
        assert fact.trust is TrustLevel.AGENT
        assert fact.provenance.attested is False
        assert fact.provenance.author.kind is AuthorKind.EXTERNAL

    async def test_a_forged_envelope_cannot_claim_a_trust_level(self, brain, caplog) -> None:
        genuine = await _sealed(brain, REGIN, TrustLevel.AGENT)
        forged = genuine.replace('"t":1', '"t":4')
        assert forged != genuine
        brain.seed("forged", provenance=forged)
        (fact,) = (await _adapter().recall(subject="people/a")).facts
        assert fact.trust is TrustLevel.AGENT
        assert fact.provenance.attested is False
        assert "signature does not verify" in caplog.text

    async def test_an_envelope_sealed_with_another_key_is_not_believed(self, brain) -> None:
        other = GBrainFactAdapter(MCP, "tok", signing_key="someone-elses-key")
        await other.remember(_new("x", author=JOZEF, trust=TrustLevel.PERSON))
        (fact,) = (await _adapter().recall(subject="people/a")).facts
        assert fact.trust is TrustLevel.AGENT and not fact.provenance.attested


class TestGet:
    async def test_found(self, brain) -> None:
        adapter = _adapter()
        outcome = await adapter.remember(_new())
        assert (await adapter.get(outcome.fact.id)).statement == "regin runs on valhalla"

    async def test_missing_is_not_found(self, brain) -> None:
        with pytest.raises(FactNotFoundError):
            await _adapter().get("99@people/a")

    async def test_missing_past_the_cap_is_a_backend_error_not_a_miss(self, brain) -> None:
        for n in range(3):
            brain.seed(f"fact {n}", provenance="p")
        with pytest.raises(FactBackendError, match="may exist beyond it"):
            await _adapter(recall_limit=3).get("99@people/a")


class TestForget:
    async def test_retires_with_who_and_why_and_keeps_the_fact(self, brain) -> None:
        adapter = _adapter()
        stored = (await adapter.remember(_new())).fact
        retired = await adapter.forget(stored.id, by="person:jozef", why="moved to noatun")
        assert retired.retired is not None
        assert retired.retired.by == "person:jozef"
        assert retired.retired.why == "moved to noatun"
        assert retired.statement == stored.statement
        assert brain.rows[0]["expired_at"] is not None
        assert len(brain.rows) == 1
        history = await adapter.recall(subject="people/a", as_of=NOW)
        assert history.facts[0].id == stored.id

    async def test_superseded_by_is_recorded(self, brain) -> None:
        adapter = _adapter()
        stored = (await adapter.remember(_new())).fact
        retired = await adapter.forget(stored.id, by="agent:regin", why="r", superseded_by="2@x")
        assert retired.retired.superseded_by == "2@x"

    async def test_forgetting_twice_changes_nothing(self, brain) -> None:
        adapter = _adapter()
        stored = (await adapter.remember(_new())).fact
        first = await adapter.forget(stored.id, by="person:a", why="one")
        second = await adapter.forget(stored.id, by="person:b", why="two")
        assert second.retired == first.retired
        assert [name for name, _ in brain.calls].count("forget") == 1

    async def test_unknown_fact_is_not_found(self, brain) -> None:
        with pytest.raises(FactNotFoundError):
            await _adapter().forget("5@people/a", by="person:a", why="x")

    async def test_an_unusable_reply_raises(self, brain) -> None:
        adapter = _adapter()
        stored = (await adapter.remember(_new())).fact
        brain._forget = lambda args: {"id": args["id"]}  # type: ignore[method-assign]
        with pytest.raises(FactBackendError, match="unusable"):
            await adapter.forget(stored.id, by="person:a", why="x")

    async def test_a_retirement_gbrain_did_by_itself_is_reported_as_gbrains(self, brain) -> None:
        brain.seed("old", provenance="p", expired_at=NOW.isoformat(), superseded_by=2)
        history = (await _adapter().entity("people/a")).history
        assert history[0].retired.by == "unrecorded"
        assert history[0].retired.superseded_by == "2@people/a"


class TestEntity:
    async def test_current_facts_history_and_card(self, brain) -> None:
        adapter = _adapter()
        stored = (await adapter.remember(_new("kept"))).fact
        gone = (await adapter.remember(_new("dropped"))).fact
        await adapter.forget(gone.id, by="person:a", why="wrong")
        record = await adapter.entity("people/a")
        assert record.found
        assert [f.id for f in record.facts] == [stored.id]
        assert [f.id for f in record.history] == [gone.id]
        assert record.card["entity"]["slug"] == "people/a"

    async def test_unknown_entity_is_found_false(self, brain) -> None:
        record = await _adapter().entity("people/nobody")
        assert record.found is False and record.facts == ()


class TestContextPack:
    async def test_ranked_budgeted_and_every_omission_explained(self, brain) -> None:
        adapter = _adapter()
        await adapter.remember(
            _new("helm deploys the stack", author=JOZEF, trust=TrustLevel.PERSON)
        )
        await adapter.remember(_new("weather is mild"))
        await adapter.remember(
            _new("tool saw flux", author=FactAuthor(AuthorKind.TOOL, "t"), trust=TrustLevel.TOOL)
        )
        brain.seed("expired", provenance="p", valid_until=(NOW - timedelta(days=1)).isoformat())
        pack = await adapter.context_pack(
            ["people/a"],
            task="how does helm deploy",
            budget_tokens=40,
            chars_per_token=4,
            min_trust=TrustLevel.TOOL,
        )
        assert pack.facts[0].statement == "helm deploys the stack"
        reasons = {o.reason for o in pack.omitted}
        assert OmissionReason.BELOW_MIN_TRUST in reasons
        assert OmissionReason.NOT_VALID_AT in reasons
        assert pack.used_tokens <= 40
        assert pack.text.startswith("[person]")

    async def test_budget_omission_names_the_dropped_facts(self, brain) -> None:
        adapter = _adapter()
        for n in range(4):
            await adapter.remember(_new(f"claim number {n} " + "w" * 60))
        pack = await adapter.context_pack(["people/a"], budget_tokens=45, chars_per_token=4)
        budget = [o for o in pack.omitted if o.reason is OmissionReason.BUDGET]
        assert budget and budget[0].count == 4 - len(pack.facts)

    async def test_a_backend_cap_is_carried_into_the_pack(self, brain) -> None:
        for n in range(2):
            brain.seed(f"f{n}", provenance="p")
        pack = await _adapter(recall_limit=2).context_pack(["people/a"], chars_per_token=4)
        assert any(o.reason is OmissionReason.BACKEND_LIMIT for o in pack.omitted)


class TestPeerSharing:
    async def test_a_shared_fact_keeps_its_original_author_and_origin(self, brain) -> None:
        origin = SharedOrigin(
            author=FactAuthor(AuthorKind.AGENT, "sigrun"),
            claimed_trust=TrustLevel.TOOL,
            fact_id="7@people/a",
            source="mimir-peer",
            evidence=("src_9",),
        )
        new = NewFact(
            draft=FactDraft("people/a", "sigrun saw it", ("src_9",)),
            author=origin.author,
            trust=TrustLevel.AGENT,
            origin=origin,
            imported_by=REGIN,
        )
        stored = (await _adapter().remember(new)).fact
        assert stored.provenance.author == origin.author
        assert stored.provenance.imported_by == REGIN
        assert stored.provenance.origin == origin
        assert stored.trust is TrustLevel.AGENT


class TestTransportFailures:
    """Every way gbrain can answer badly is an error that says so."""

    @pytest.mark.parametrize(
        ("reply", "match"),
        [
            ({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "bad"}}, "MCP error"),
            ({"jsonrpc": "2.0", "id": 1, "result": "nope"}, "no result object"),
        ],
    )
    async def test_protocol_level_failures_raise(self, reply, match) -> None:
        with respx.mock() as router:
            router.post(MCP).mock(return_value=httpx.Response(200, json=reply))
            with pytest.raises(FactBackendError, match=match):
                await _adapter().recall(subject="people/a")

    async def test_unparseable_text_is_an_unusable_reply_not_an_empty_one(self) -> None:
        reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "<<"}]}}
        with respx.mock() as router:
            router.post(MCP).mock(return_value=httpx.Response(200, json=reply))
            with pytest.raises(FactBackendError, match="no facts list"):
                await _adapter().recall(subject="people/a")

    async def test_structured_content_is_read(self) -> None:
        reply = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"structuredContent": {"facts": [], "protocol_version": 1}},
        }
        with respx.mock() as router:
            router.post(MCP).mock(return_value=httpx.Response(200, json=reply))
            assert (await _adapter().recall(subject="people/a")).facts == ()

    async def test_an_unreachable_brain_raises(self) -> None:
        with respx.mock() as router:
            router.post(MCP).mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(FactBackendError, match="refused"):
                await _adapter().recall(subject="people/a")

    async def test_a_row_without_timestamps_raises(self, brain) -> None:
        brain.seed("x", provenance="p", created_at=None, valid_from=None)
        with pytest.raises(FactBackendError, match="timestamps"):
            await _adapter().recall(subject="people/a")

    async def test_close_is_safe_before_and_after_use(self, brain) -> None:
        adapter = _adapter()
        await adapter.close()
        await adapter.recall(subject="people/a")
        await adapter.close()
        await adapter.close()

    async def test_an_unrelated_supersession_is_ignored(self, brain) -> None:
        brain.seed("old", provenance="p", expired_at=NOW.isoformat(), superseded_by=77)
        brain.next_status, brain.supersede = "superseded", None
        adapter = _adapter()
        assert (await adapter.remember(_new("fresh"))).status == "superseded"
