"""FactService, the caller resolver, and the MCP / agent-tool surfaces for typed facts.

The service and tool tests run on an in-memory port; the end-to-end class runs
the same surfaces on GBrainFactAdapter over a fake gbrain.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.gbrain_facts import GBrainFactAdapter
from mimir.config import MimirFactsConfig, MimirServiceConfig
from mimir.fact_tools import FACT_TOOLS
from mimir.facts import FactCallerResolver, FactService
from mimir.mcp import MimirMcpServer
from mimir.tools import build_mimir_fact_tools
from niuu.domain.mimir_facts import (
    AuthorKind,
    EntityRecord,
    Fact,
    FactAuthor,
    FactBackendError,
    FactCaller,
    FactDraft,
    FactNotFoundError,
    FactProvenance,
    FactRecall,
    FactValidationError,
    NewFact,
    RememberOutcome,
    Retirement,
    SharedFact,
    TrustError,
    TrustLevel,
    select_facts,
)
from niuu.domain.models import Principal
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError
from niuu.ports.mimir_facts import MimirFactPort
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter
from tests.test_mimir.unit.test_gbrain_facts_adapter import MCP, FakeGBrain

_BACKEND = {"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"}

NOW = datetime.now(UTC)
PERSON = FactCaller(FactAuthor(AuthorKind.PERSON, "jozef"))
REGIN = FactCaller(FactAuthor(AuthorKind.AGENT, "regin"))
TOOL = FactCaller(FactAuthor(AuthorKind.TOOL, "scanner"))


class MemoryFacts(MimirFactPort):
    def __init__(self) -> None:
        self.facts: dict[str, Fact] = {}
        self.fail_forget = False

    async def remember(self, new: NewFact) -> RememberOutcome:
        fact_id = f"{len(self.facts) + 1}@{new.draft.subject}"
        fact = Fact(
            id=fact_id,
            subject=new.draft.subject,
            statement=new.draft.statement,
            kind=new.draft.kind,
            trust=new.trust,
            provenance=FactProvenance(
                author=new.author,
                evidence=new.draft.evidence,
                derived_from=new.draft.derived_from,
                replaces=new.draft.replaces,
                origin=new.origin,
                imported_by=new.imported_by,
            ),
            recorded_at=NOW,
            valid_from=new.draft.valid_from or NOW - timedelta(seconds=1),
            valid_until=new.draft.valid_until,
        )
        self.facts[fact_id] = fact
        return RememberOutcome(fact, "inserted")

    async def get(self, fact_id: str) -> Fact:
        if fact_id not in self.facts:
            raise FactNotFoundError(f"no fact {fact_id}")
        return self.facts[fact_id]

    async def recall(self, *, subject=None, contains=None, min_trust=TrustLevel.AGENT, as_of=None):
        facts = [f for f in self.facts.values() if subject in (None, f.subject)]
        kept, omitted = select_facts(facts, min_trust=min_trust, as_of=as_of or NOW)
        return FactRecall(tuple(kept), tuple(omitted))

    async def forget(self, fact_id, *, by, why, superseded_by=None) -> Fact:
        if self.fail_forget:
            raise FactBackendError("gbrain forget failed")
        fact = self.facts[fact_id]
        retired = Retirement(at=NOW, by=by, why=why, superseded_by=superseded_by)
        self.facts[fact_id] = Fact(**{**fact.__dict__, "retired": retired})
        return self.facts[fact_id]

    async def entity(self, subject: str) -> EntityRecord:
        facts = tuple(f for f in self.facts.values() if f.subject == subject)
        return EntityRecord(subject, bool(facts), facts)


def _service() -> tuple[FactService, MemoryFacts]:
    port = MemoryFacts()
    return FactService(port, source="mimir-a", chars_per_token=4), port


def _draft(statement: str = "regin runs on valhalla", **kwargs) -> FactDraft:
    return FactDraft("people/regin", statement, ("page/one.md",), **kwargs)


class TestRemember:
    async def test_trust_comes_from_the_caller_not_the_request(self) -> None:
        service, _ = _service()
        by_agent = await service.remember(REGIN, _draft())
        by_person = await service.remember(PERSON, _draft("another"))
        assert by_agent.fact.trust is TrustLevel.AGENT
        assert by_person.fact.trust is TrustLevel.PERSON
        assert by_agent.fact.provenance.author == REGIN.author

    async def test_derived_fact_takes_the_lowest_input_trust(self) -> None:
        service, _ = _service()
        confirmed = (await service.remember(PERSON, _draft("a"))).fact
        observed = (await service.remember(TOOL, _draft("b"))).fact
        derived = await service.remember(
            PERSON, _draft("a and b", derived_from=(confirmed.id, observed.id))
        )
        assert derived.fact.trust is TrustLevel.TOOL

    async def test_deriving_from_an_unknown_fact_raises(self) -> None:
        service, _ = _service()
        with pytest.raises(FactNotFoundError):
            await service.remember(REGIN, _draft(derived_from=("9@nope",)))

    async def test_deriving_from_a_retired_fact_is_refused(self) -> None:
        service, _ = _service()
        old = (await service.remember(REGIN, _draft())).fact
        await service.forget(REGIN, old.id, "wrong")
        with pytest.raises(FactValidationError, match="no longer current"):
            await service.remember(REGIN, _draft("x", derived_from=(old.id,)))

    async def test_replacing_retires_the_old_fact_and_keeps_it_in_history(self) -> None:
        service, port = _service()
        old = (await service.remember(REGIN, _draft("regin runs on valhalla"))).fact
        new = (
            await service.remember(REGIN, _draft("regin runs on noatun", replaces=(old.id,)))
        ).fact
        assert port.facts[old.id].retired.superseded_by == new.id
        assert port.facts[old.id].retired.by == "agent:regin"
        assert (await service.get(old.id)).statement == "regin runs on valhalla"

    async def test_an_agent_cannot_replace_a_person_confirmed_fact(self) -> None:
        service, port = _service()
        confirmed = (await service.remember(PERSON, _draft())).fact
        with pytest.raises(TrustError, match="may not replace"):
            await service.remember(REGIN, _draft("regin runs elsewhere", replaces=(confirmed.id,)))
        assert port.facts[confirmed.id].retired is None
        assert len(port.facts) == 1

    async def test_a_person_may_replace_an_agent_fact(self) -> None:
        service, port = _service()
        agent_fact = (await service.remember(REGIN, _draft())).fact
        await service.remember(PERSON, _draft("corrected", replaces=(agent_fact.id,)))
        assert port.facts[agent_fact.id].retired is not None

    async def test_replacing_an_already_retired_fact_is_refused(self) -> None:
        service, _ = _service()
        old = (await service.remember(REGIN, _draft())).fact
        await service.forget(REGIN, old.id, "x")
        with pytest.raises(FactValidationError, match="already retired"):
            await service.remember(REGIN, _draft("y", replaces=(old.id,)))

    async def test_a_failed_retirement_after_the_write_names_both_facts(self) -> None:
        service, port = _service()
        old = (await service.remember(REGIN, _draft())).fact
        port.fail_forget = True
        with pytest.raises(FactBackendError, match=f"was stored.*{old.id}"):
            await service.remember(REGIN, _draft("new", replaces=(old.id,)))


class TestForget:
    async def test_retires_with_who_and_why_and_keeps_history(self) -> None:
        service, port = _service()
        fact = (await service.remember(REGIN, _draft())).fact
        retired = await service.forget(PERSON, fact.id, "  was wrong  ")
        assert retired.retired.by == "person:jozef"
        assert retired.retired.why == "was wrong"
        assert port.facts[fact.id].statement == fact.statement
        assert (await service.recall(as_of=NOW - timedelta(seconds=0))).facts == ()

    async def test_a_reason_is_required(self) -> None:
        service, _ = _service()
        fact = (await service.remember(REGIN, _draft())).fact
        with pytest.raises(FactValidationError, match="reason"):
            await service.forget(REGIN, fact.id, "  ")

    async def test_an_agent_cannot_retire_a_person_confirmed_fact(self) -> None:
        service, port = _service()
        fact = (await service.remember(PERSON, _draft())).fact
        with pytest.raises(TrustError, match="may not retire"):
            await service.forget(REGIN, fact.id, "disagree")
        assert port.facts[fact.id].retired is None

    async def test_forgetting_a_retired_fact_changes_nothing(self) -> None:
        service, _ = _service()
        fact = (await service.remember(REGIN, _draft())).fact
        first = await service.forget(REGIN, fact.id, "one")
        again = await service.forget(PERSON, fact.id, "two")
        assert again.retired == first.retired

    async def test_unknown_fact_raises(self) -> None:
        service, _ = _service()
        with pytest.raises(FactNotFoundError):
            await service.forget(REGIN, "5@x", "why")


class TestRecallAndPack:
    async def test_recall_filters_by_trust_and_validity(self) -> None:
        service, _ = _service()
        await service.remember(REGIN, _draft("agent says"))
        await service.remember(PERSON, _draft("person says"))
        await service.remember(
            PERSON,
            _draft(
                "expired", valid_from=NOW - timedelta(days=9), valid_until=NOW - timedelta(days=1)
            ),
        )
        recalled = await service.recall(subject="people/regin", min_trust=TrustLevel.OPERATOR)
        assert [f.statement for f in recalled.facts] == ["person says"]
        assert {o.reason.value for o in recalled.omitted} == {"below_min_trust", "not_valid_at"}

    async def test_context_pack_needs_a_subject_and_a_real_budget(self) -> None:
        service, _ = _service()
        with pytest.raises(FactValidationError, match="subject"):
            await service.context_pack([])
        with pytest.raises(FactValidationError, match="budget_tokens"):
            await service.context_pack(["x"], budget_tokens=0)

    async def test_context_pack_ranks_and_reports_what_it_left_out(self) -> None:
        service, _ = _service()
        await service.remember(PERSON, _draft("regin owns the valhalla cluster"))
        await service.remember(REGIN, _draft("unrelated gossip " + "z" * 80))
        pack = await service.context_pack(
            ["people/regin"], task="valhalla cluster", budget_tokens=30
        )
        assert pack.facts[0].statement.startswith("regin owns")
        assert pack.omitted and pack.omitted[0].reason.value == "budget"

    async def test_entity(self) -> None:
        service, _ = _service()
        await service.remember(REGIN, _draft())
        assert (await service.entity("people/regin")).found


class TestPeerSharing:
    async def test_a_received_fact_stays_its_authors_and_is_never_relabelled(self) -> None:
        service_a, _ = _service()
        service_b, _ = _service()
        original = (await service_a.remember(TOOL, _draft("scanner saw it"))).fact
        bundle = await service_a.share(original.id)
        received = (await service_b.receive(REGIN, bundle)).fact
        assert received.provenance.author == TOOL.author
        assert received.provenance.imported_by == REGIN.author
        assert received.provenance.origin.fact_id == original.id
        assert received.provenance.origin.source == "mimir-a"
        assert received.provenance.evidence == original.provenance.evidence

    async def test_a_peers_claimed_trust_is_capped_at_the_importer(self) -> None:
        service_a, _ = _service()
        service_b, _ = _service()
        confirmed = (await service_a.remember(PERSON, _draft())).fact
        bundle = await service_a.share(confirmed.id)
        assert bundle.trust is TrustLevel.PERSON
        assert (await service_b.receive(REGIN, bundle)).fact.trust is TrustLevel.AGENT
        assert (await service_b.receive(PERSON, bundle)).fact.trust is TrustLevel.PERSON

    async def test_resharing_keeps_the_first_origin(self) -> None:
        a, _ = _service()
        b, _ = _service()
        c, _ = _service()
        first = (await a.remember(TOOL, _draft())).fact
        relayed = (await b.receive(REGIN, await a.share(first.id))).fact
        final = (await c.receive(REGIN, await b.share(relayed.id))).fact
        assert final.provenance.author == TOOL.author
        assert final.provenance.origin.source == "mimir-a"
        assert final.provenance.origin.fact_id == first.id

    async def test_a_fact_without_evidence_is_refused(self) -> None:
        service, _ = _service()
        bare = SharedFact(
            "1@x", "people/x", "s", _draft().kind, TrustLevel.TOOL, TOOL.author, (), NOW
        )
        with pytest.raises(FactValidationError, match="provenance"):
            await service.receive(REGIN, bare)


class TestCallerResolver:
    ROLES = {"volundr:admin": "person", "curator": "operator"}

    def _resolver(self, **scopes) -> FactCallerResolver:
        return FactCallerResolver(self.ROLES, scopes)

    def _principal(self, **kwargs) -> Principal:
        return Principal(
            **{"user_id": "u1", "email": "u@x", "tenant_id": "t", "roles": [], **kwargs}
        )

    def test_a_human_gets_the_highest_level_among_their_roles(self) -> None:
        caller = self._resolver().resolve(self._principal(roles=["curator", "volundr:admin"]))
        assert caller.author == FactAuthor(AuthorKind.PERSON, "u1")

    def test_an_operator_role_is_the_operator_level(self) -> None:
        caller = self._resolver().resolve(self._principal(roles=["curator"]))
        assert caller.trust is TrustLevel.OPERATOR

    def test_a_human_with_no_mapped_role_is_refused(self) -> None:
        with pytest.raises(TrustError, match="role_levels"):
            self._resolver().resolve(self._principal(roles=["viewer"]))

    def test_a_resident_is_an_agent_whatever_roles_its_token_carries(self) -> None:
        principal = self._principal(roles=["volundr:admin"], actor="system:serviceaccount:ns:regin")
        caller = self._resolver().resolve(principal)
        assert caller.author == FactAuthor(AuthorKind.AGENT, "regin")
        assert caller.trust is TrustLevel.AGENT

    def test_a_scoped_session_token_is_an_agent_named_for_its_session(self) -> None:
        principal = self._principal(
            roles=["volundr:admin"], token_use="forge_session", bound_session_id="sess-1"
        )
        caller = self._resolver().resolve(principal)
        assert caller.author == FactAuthor(AuthorKind.AGENT, "sess-1")

    def test_a_workload_scope_mapped_to_tool_makes_a_tool(self) -> None:
        resolver = self._resolver(**{"mimir:observe": "tool"})
        principal = self._principal(actor="a:scanner", scopes=("mimir:observe",))
        assert resolver.resolve(principal).trust is TrustLevel.TOOL

    @pytest.mark.parametrize("level", ["operator", "person"])
    def test_a_workload_can_never_be_mapped_above_tool(self, level) -> None:
        with pytest.raises(ValueError, match="above 'tool'"):
            FactCallerResolver({}, {"some:scope": level})


class _Auth(HeaderAuthenticationPort):
    """Principal named by the ``x-who`` header."""

    def __init__(self, principals: dict[str, Principal]) -> None:
        self._principals = principals

    async def validate_headers(self, headers: dict[str, str]) -> Principal:
        who = headers.get("x-who")
        if who not in self._principals:
            raise InvalidTokenError("no credential")
        return self._principals[who]


def _principal(user: str, roles=(), **kwargs) -> Principal:
    return Principal(user, f"{user}@x", "t", list(roles), **kwargs)


PRINCIPALS = {
    "jozef": _principal("jozef", ["volundr:admin"]),
    "regin": _principal("jozef", ["volundr:admin"], actor="sa:regin"),
    "viewer": _principal("vi", ["viewer"]),
}


def _mcp(tmp_path, port: MimirFactPort | None = None, *, configured: bool = True):
    kwargs = {}
    if configured:
        kwargs = {
            "facts": FactService(port or MemoryFacts(), source="mimir-a", chars_per_token=4),
            "fact_callers": FactCallerResolver(
                {"volundr:admin": "person", "curator": "operator"}, {}
            ),
        }
    server = MimirMcpServer(
        adapter=InMemoryMimirAdapter(),
        name="test",
        auth=_Auth(PRINCIPALS),
        auth_mode="none",
        **kwargs,
    )
    app = FastAPI()
    app.include_router(server.router(), prefix="/mcp")
    return TestClient(app)


def _call(client: TestClient, who: str | None, tool: str, args: dict) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    headers = {"x-who": who} if who else {}
    response = client.post("/mcp", json=body, headers=headers)
    return response.json()["result"]


def _text(result: dict):
    text = result["content"][0]["text"]
    return json.loads(text) if not result.get("isError") else text


REMEMBER = {"subject": "people/regin", "statement": "regin is a resident", "evidence": ["e1"]}


class TestMcpSurface:
    def test_fact_tools_are_listed_only_when_facts_are_configured(self, tmp_path) -> None:
        listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        on = _mcp(tmp_path).post("/mcp", json=listing).json()["result"]["tools"]
        off = _mcp(tmp_path, configured=False).post("/mcp", json=listing).json()["result"]["tools"]
        assert {t["name"] for t in on} >= set(FACT_TOOLS)
        assert not {t["name"] for t in off} & set(FACT_TOOLS)

    def test_calling_a_fact_tool_without_facts_configured_is_an_error(self, tmp_path) -> None:
        client = _mcp(tmp_path, configured=False)
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "mimir_fact_recall", "arguments": {}},
        }
        assert "error" in client.post("/mcp", json=body).json()

    def test_facts_and_callers_must_come_together(self, tmp_path) -> None:
        service = FactService(MemoryFacts(), source="x", chars_per_token=4)
        with pytest.raises(ValueError, match="together"):
            MimirMcpServer(adapter=InMemoryMimirAdapter(), facts=service)

    def test_a_person_writes_at_the_person_level(self, tmp_path) -> None:
        result = _text(_call(_mcp(tmp_path), "jozef", "mimir_fact_remember", REMEMBER))
        assert result["fact"]["trust"] == "person"
        assert result["fact"]["author"] == "person:jozef"

    def test_a_resident_writes_at_the_agent_level_even_with_a_person_token(self, tmp_path) -> None:
        result = _text(_call(_mcp(tmp_path), "regin", "mimir_fact_remember", REMEMBER))
        assert result["fact"]["trust"] == "agent"
        assert result["fact"]["author"] == "agent:regin"

    @pytest.mark.parametrize("field", ["trust", "author", "confidence"])
    def test_a_caller_naming_its_own_trust_is_refused_and_nothing_is_stored(
        self, tmp_path, field
    ) -> None:
        port = MemoryFacts()
        client = _mcp(tmp_path, port)
        result = _call(client, "regin", "mimir_fact_remember", {**REMEMBER, field: "person"})
        assert result["isError"] is True
        assert "cannot be set by the caller" in result["content"][0]["text"]
        assert port.facts == {}

    def test_unknown_arguments_are_refused(self, tmp_path) -> None:
        result = _call(_mcp(tmp_path), "jozef", "mimir_fact_remember", {**REMEMBER, "wat": 1})
        assert result["isError"] is True and "wat" in result["content"][0]["text"]

    def test_missing_evidence_is_refused(self, tmp_path) -> None:
        args = {"subject": "people/regin", "statement": "s"}
        result = _call(_mcp(tmp_path), "jozef", "mimir_fact_remember", args)
        assert result["isError"] is True and "provenance" in result["content"][0]["text"]

    def test_a_write_without_an_identity_is_refused_not_attributed_to_nobody(
        self, tmp_path
    ) -> None:
        result = _call(_mcp(tmp_path), None, "mimir_fact_remember", REMEMBER)
        assert result["isError"] is True
        assert "no valid credential" in result["content"][0]["text"]

    def test_a_human_without_a_trust_role_cannot_write(self, tmp_path) -> None:
        result = _call(_mcp(tmp_path), "viewer", "mimir_fact_remember", REMEMBER)
        assert result["isError"] is True and "role_levels" in result["content"][0]["text"]

    def test_an_agent_cannot_retire_a_person_fact_over_mcp(self, tmp_path) -> None:
        client = _mcp(tmp_path)
        stored = _text(_call(client, "jozef", "mimir_fact_remember", REMEMBER))["fact"]
        result = _call(client, "regin", "mimir_fact_forget", {"id": stored["id"], "why": "no"})
        assert result["isError"] is True
        recalled = _text(_call(client, None, "mimir_fact_recall", {"subject": "people/regin"}))
        assert [f["id"] for f in recalled["facts"]] == [stored["id"]]

    def test_forget_keeps_history_visible_through_entity(self, tmp_path) -> None:
        client = _mcp(tmp_path)
        stored = _text(_call(client, "jozef", "mimir_fact_remember", REMEMBER))["fact"]
        forgotten = _text(
            _call(client, "jozef", "mimir_fact_forget", {"id": stored["id"], "why": "x"})
        )
        assert forgotten["fact"]["retired"]["why"] == "x"

    def test_recall_filters_and_reports_omissions(self, tmp_path) -> None:
        client = _mcp(tmp_path)
        _call(client, "regin", "mimir_fact_remember", REMEMBER)
        recalled = _text(
            _call(
                client, None, "mimir_fact_recall", {"subject": "people/regin", "min_trust": "tool"}
            )
        )
        assert recalled["facts"] == []
        assert recalled["omitted"][0]["reason"] == "below_min_trust"

    def test_a_bad_min_trust_is_refused(self, tmp_path) -> None:
        result = _call(_mcp(tmp_path), None, "mimir_fact_recall", {"min_trust": "king"})
        assert result["isError"] is True

    def test_a_bad_timestamp_is_refused(self, tmp_path) -> None:
        result = _call(_mcp(tmp_path), None, "mimir_fact_recall", {"as_of": "yesterday-ish"})
        assert result["isError"] is True and "as_of" in result["content"][0]["text"]

    def test_context_pack_entity_share_and_receive(self, tmp_path) -> None:
        client = _mcp(tmp_path)
        stored = _text(_call(client, "jozef", "mimir_fact_remember", REMEMBER))["fact"]
        pack = _text(_call(client, None, "mimir_fact_context_pack", {"subjects": ["people/regin"]}))
        assert pack["facts"][0]["id"] == stored["id"] and pack["text"]
        assert _text(_call(client, None, "mimir_fact_entity", {"subject": "people/regin"}))["found"]
        bundle = _text(_call(client, None, "mimir_fact_share", {"id": stored["id"]}))["fact"]
        assert bundle["author"] == "person:jozef" and bundle["trust"] == "person"
        received = _text(_call(client, "regin", "mimir_fact_receive", {"fact": bundle}))
        assert received["fact"]["author"] == "person:jozef"
        assert received["fact"]["trust"] == "agent"
        assert received["fact"]["imported_by"] == "agent:regin"
        assert received["fact"]["origin"]["claimed_trust"] == "person"

    def test_a_malformed_shared_fact_is_refused(self, tmp_path) -> None:
        client = _mcp(tmp_path)
        for args in ({"fact": "nope"}, {"fact": {"fact_id": "1"}}):
            assert _call(client, "regin", "mimir_fact_receive", args)["isError"] is True

    def test_a_backend_failure_is_an_error_the_model_can_read_never_an_empty_recall(
        self, tmp_path
    ) -> None:
        class Down(MemoryFacts):
            async def recall(self, **_kwargs):
                raise FactBackendError("gbrain recall failed: connection refused")

        result = _call(_mcp(tmp_path, Down()), None, "mimir_fact_recall", {})
        assert result["isError"] is True
        assert "connection refused" in result["content"][0]["text"]


class TestAgentTools:
    def test_one_tool_per_operation(self) -> None:
        service, _ = _service()
        tools = build_mimir_fact_tools(service, REGIN)
        assert {t.name for t in tools} == set(FACT_TOOLS)

    async def test_the_tool_writes_as_the_identity_it_was_built_with(self) -> None:
        service, port = _service()
        remember = next(
            t for t in build_mimir_fact_tools(service, REGIN) if t.name == "mimir_fact_remember"
        )
        result = await remember.execute(REMEMBER)
        assert not result.is_error
        (fact,) = port.facts.values()
        assert fact.trust is TrustLevel.AGENT and fact.provenance.author == REGIN.author

    async def test_the_model_cannot_raise_its_level_through_the_tool(self) -> None:
        service, port = _service()
        remember = next(
            t for t in build_mimir_fact_tools(service, REGIN) if t.name == "mimir_fact_remember"
        )
        result = await remember.execute({**REMEMBER, "trust": "person"})
        assert result.is_error and "cannot be set by the caller" in result.content
        assert port.facts == {}

    async def test_permissions_split_reads_from_writes(self) -> None:
        service, _ = _service()
        tools = {t.name: t for t in build_mimir_fact_tools(service, REGIN)}
        assert tools["mimir_fact_recall"].required_permission == "mimir:read"
        assert tools["mimir_fact_forget"].required_permission == "mimir:write"
        assert tools["mimir_fact_recall"].input_schema["type"] == "object"
        assert tools["mimir_fact_recall"].description

    async def test_a_failed_backend_is_a_tool_error(self) -> None:
        service, port = _service()
        port.fail_forget = True
        old = (await service.remember(REGIN, _draft())).fact
        remember = next(
            t for t in build_mimir_fact_tools(service, REGIN) if t.name == "mimir_fact_remember"
        )
        result = await remember.execute({**REMEMBER, "replaces": [old.id]})
        assert result.is_error and "forget failed" in result.content


class TestEndToEndOnGBrain:
    """The whole stack on the gbrain adapter, over a fake brain."""

    @pytest.fixture
    def client(self, tmp_path):
        fake = FakeGBrain()
        with respx.mock(assert_all_called=False) as router:
            router.post(MCP).mock(side_effect=fake.handle)
            adapter = GBrainFactAdapter(MCP, "tok", signing_key="k")
            self.fake = fake
            yield _mcp(tmp_path, adapter)

    def test_trust_survives_the_store_and_cannot_be_forged_by_a_direct_write(self, client) -> None:
        stored = _text(_call(client, "regin", "mimir_fact_remember", REMEMBER))["fact"]
        # Someone with a gbrain token rewrites the row's provenance to claim a person.
        row = self.fake.rows[0]
        row["source"] = row["source"].replace('"t":1', '"t":4')
        recalled = _text(_call(client, None, "mimir_fact_recall", {"subject": "people/regin"}))
        assert recalled["facts"][0]["trust"] == "agent"
        assert recalled["facts"][0]["attested"] is False
        assert stored["trust"] == "agent"

    def test_forget_then_entity_shows_the_retired_fact_in_history(self, client) -> None:
        stored = _text(_call(client, "jozef", "mimir_fact_remember", REMEMBER))["fact"]
        _call(client, "jozef", "mimir_fact_forget", {"id": stored["id"], "why": "obsolete"})
        entity = _text(_call(client, None, "mimir_fact_entity", {"subject": "people/regin"}))
        assert entity["facts"] == []
        assert entity["history"][0]["retired"] == {
            **entity["history"][0]["retired"],
            "by": "person:jozef",
            "why": "obsolete",
        }


class TestConfig:
    def test_facts_are_off_unless_configured(self) -> None:
        assert MimirServiceConfig().facts is None

    def test_gbrain_is_the_default_backend_and_trust_defaults_are_set(self) -> None:
        config = MimirFactsConfig()
        assert config.adapter == "mimir.adapters.gbrain_facts.GBrainFactAdapter"
        assert config.trust.role_levels["mimir:curator"] == "operator"
        assert config.chars_per_token == 4

    def test_the_app_refuses_an_adapter_that_is_not_a_fact_port(self, tmp_path) -> None:
        from mimir.app import create_app

        config = MimirServiceConfig(
            path=str(tmp_path / "m"),
            backend=_BACKEND,
            facts=MimirFactsConfig(adapter="tests.test_mimir.unit.fakes.InMemoryMimirAdapter"),
        )
        with pytest.raises(ValueError, match="not a MimirFactPort"):
            create_app(config)

    def test_the_app_builds_the_gbrain_backend_and_serves_the_tools(self, tmp_path) -> None:
        from mimir.app import create_app

        key = tmp_path / "key"
        key.write_text("k")
        config = MimirServiceConfig(
            path=str(tmp_path / "m"),
            backend=_BACKEND,
            facts=MimirFactsConfig(
                kwargs={"mcp_url": MCP, "api_token": "tok", "signing_key_file": str(key)}
            ),
        )
        app = create_app(config)
        with TestClient(app) as client:
            listing = client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            ).json()
        assert "mimir_fact_remember" in {t["name"] for t in listing["result"]["tools"]}

    def test_the_app_fails_at_startup_when_the_backend_cannot_be_built(self, tmp_path) -> None:
        from mimir.app import create_app

        config = MimirServiceConfig(
            path=str(tmp_path / "m"),
            backend=_BACKEND,
            facts=MimirFactsConfig(kwargs={"mcp_url": MCP}),
        )
        with pytest.raises(ValueError, match="API token"):
            create_app(config)


class TestWire:
    from mimir import fact_wire as wire

    @pytest.mark.parametrize(
        "args",
        [
            {"subject": "", "statement": "s", "evidence": ["e"]},
            {"subject": "a", "statement": "s", "evidence": [1]},
            {"subject": "a", "statement": "s", "evidence": ["e"], "kind": "rumour"},
            {"subject": "a", "statement": "s", "evidence": ["e"], "replaces": {"x": 1}},
        ],
    )
    def test_bad_drafts_are_refused(self, args) -> None:
        with pytest.raises(FactValidationError):
            self.wire.parse_draft(args)

    def test_a_single_evidence_string_is_accepted(self) -> None:
        draft = self.wire.parse_draft({"subject": "a", "statement": "s", "evidence": "one"})
        assert draft.evidence == ("one",)

    def test_a_naive_timestamp_is_taken_as_utc(self) -> None:
        moment = self.wire.parse_moment("2026-01-01T00:00:00", "as_of")
        assert moment.tzinfo is not None

    def test_provenance_chains_and_origin_are_serialised(self) -> None:
        service = FactService(MemoryFacts(), source="a", chars_per_token=4)

        async def run():
            base = (await service.remember(TOOL, _draft("base"))).fact
            derived = (
                await service.remember(TOOL, _draft("next", derived_from=(base.id,), replaces=()))
            ).fact
            received = (await service.receive(REGIN, await service.share(base.id))).fact
            return derived, received

        import asyncio

        derived, received = asyncio.run(run())
        assert self.wire.fact_json(derived)["derived_from"] == list(derived.provenance.derived_from)
        shown = self.wire.fact_json(received)
        assert shown["origin"]["author"] == "tool:scanner" and shown["imported_by"] == "agent:regin"
        replacing = Fact(
            **{
                **derived.__dict__,
                "provenance": FactProvenance(
                    author=derived.provenance.author, evidence=("e",), replaces=("1@x",)
                ),
            }
        )
        assert self.wire.fact_json(replacing)["replaces"] == ["1@x"]

    def test_a_shared_fact_with_an_origin_round_trips(self) -> None:
        raw = {
            "fact_id": "1@x",
            "subject": "s",
            "statement": "t",
            "trust": "tool",
            "author": "tool:a",
            "evidence": ["e"],
            "valid_from": NOW.isoformat(),
            "origin": {
                "author": "tool:a",
                "claimed_trust": "tool",
                "fact_id": "1@x",
                "source": "peer",
                "evidence": ["e"],
            },
        }
        parsed = self.wire.parse_shared({"fact": raw})
        assert parsed.origin.source == "peer"
        assert self.wire.shared_json(parsed)["origin"]["fact_id"] == "1@x"

    def test_a_shared_fact_with_an_empty_start_is_refused(self) -> None:
        raw = {
            "fact_id": "1@x",
            "subject": "s",
            "statement": "t",
            "trust": "tool",
            "author": "tool:a",
            "evidence": ["e"],
            "valid_from": "",
        }
        with pytest.raises(FactValidationError, match="empty valid_from"):
            self.wire.parse_shared({"fact": raw})
