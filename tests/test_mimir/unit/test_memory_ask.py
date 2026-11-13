"""memory_ask and the context pack (contract C9), against a fake Bifröst (respx)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
import respx

from mimir.adapters.bifrost_answers import BifrostAnswerModel
from mimir.memory.answers import AnswerRefusedError, MemoryAnswers
from mimir.memory.gateway import MemoryGateway
from mimir.memory.models import Attribution, Evidence, PageWrite
from mimir.memory.systems import MemorySystems
from mimir.memory.tools import MemoryTools
from mimir.ports.answer_model import ModelCaller
from niuu.cap_telemetry import OutputTruncatedError, cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import TrustLevel
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    PROJECT_ID,
    FakeGBrain,
    context,
    gbrain_backend,
    session_token,
)

BIFROST = "http://bifrost.test"
ANSWERER = "answer-model-1"
URL = "url:https://linear.app/acme/LIN-482"
PROJECT = f"project:{PROJECT_ID}"


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


@pytest.fixture
def services():
    brain = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=brain.handle)
        yield brain, router


@pytest.fixture
async def gateway(services):
    systems = MemorySystems({"acme-brain": gbrain_backend()}, {"acme": "acme-brain"})
    yield MemoryGateway(systems, default_result_count=10)
    await systems.close()


def _answers(gateway, **overrides) -> MemoryAnswers:
    settings = {
        "answer_model": ANSWERER,
        "retrieval_count": 5,
        **overrides,
    }
    model = BifrostAnswerModel(BIFROST, timeout_seconds=5)
    return MemoryAnswers(gateway, model, **settings)


async def _seed(session, slug: str, text: str, layer: str = PROJECT) -> None:
    await session.backend.put_page(
        session.view.layer(layer).namespace,
        slug,
        PageWrite(text, title=slug),
        Attribution(
            "operator:owner", TrustLevel.OPERATOR, (Evidence.parse(URL),), datetime.now(UTC)
        ),
        expected_revision=None,
    )


def _reply(answer: dict, *, stop_reason: str = "end_turn") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": ANSWERER,
            "content": [
                {"type": "thinking", "thinking": "the records say Ines"},
                {"type": "text", "text": json.dumps(answer)},
            ],
            "stop_reason": stop_reason,
        },
    )


class TestAsk:
    async def test_an_answer_cites_what_was_retrieved_and_is_made_as_the_session(
        self, services, gateway
    ) -> None:
        _, router = services
        session = await gateway.open(context())
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        call = router.post(f"{BIFROST}/v1/messages").mock(
            return_value=_reply(
                {
                    "answer": "Ines owns the booking cut-over.",
                    "citations": [{"layer": PROJECT, "ref": "people/ines"}],
                    "gaps": [],
                }
            )
        )
        token = session_token()
        answer = await _answers(gateway).ask(
            session,
            "who owns the booking cut-over?",
            layers=None,
            caller=ModelCaller.session(token),
        )
        assert answer.answer == "Ines owns the booking cut-over."
        assert answer.citations == ((PROJECT, "people/ines"),)
        sent = call.calls.last.request
        assert sent.headers["authorization"] == f"Bearer {token}"
        body = json.loads(sent.content)
        assert body["model"] == ANSWERER and "max_tokens" not in body
        assert "layer=" + PROJECT + " ref=people/ines" in body["messages"][0]["content"]
        assert "Ines owns the booking cut-over." in body["messages"][0]["content"]

    async def test_the_answer_is_not_stored(self, services, gateway) -> None:
        brain, router = services
        session = await gateway.open(context())
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        router.post(f"{BIFROST}/v1/messages").mock(
            return_value=_reply(
                {"answer": "Ines.", "citations": [{"layer": PROJECT, "ref": "people/ines"}]}
            )
        )
        before = {n for n, _ in brain.calls if n in ("put_page", "remember", "add_timeline_entry")}
        writes = len([1 for n, _ in brain.calls if n in before])
        await _answers(gateway).ask(
            session, "booking cut-over", layers=None, caller=ModelCaller.session("t")
        )
        assert len([1 for n, _ in brain.calls if n in before]) == writes

    async def test_a_cut_off_answer_raises_naming_the_knob_and_is_counted(
        self, services, gateway
    ) -> None:
        _, router = services
        session = await gateway.open(context())
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        router.post(f"{BIFROST}/v1/messages").mock(
            return_value=_reply({"answer": "Ines"}, stop_reason="max_tokens")
        )
        with pytest.raises(OutputTruncatedError, match="memory.answer_model"):
            await _answers(gateway).ask(
                session, "booking cut-over", layers=None, caller=ModelCaller.session("t")
            )
        assert cap_hit_counts()["memory.answer_model"] == 1

    async def test_a_citation_outside_the_retrieved_set_is_refused(self, services, gateway) -> None:
        _, router = services
        session = await gateway.open(context())
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        router.post(f"{BIFROST}/v1/messages").mock(
            return_value=_reply(
                {
                    "answer": "Marc owns it.",
                    "citations": [{"layer": "org", "ref": "people/marc"}],
                    "gaps": [],
                }
            )
        )
        with pytest.raises(AnswerRefusedError, match="org:people/marc"):
            await _answers(gateway).ask(
                session, "booking cut-over", layers=None, caller=ModelCaller.session("t")
            )
        assert cap_hit_counts()["memory.ask.citations"] == 1

    @pytest.mark.parametrize(
        "content",
        [
            {"answer": "Ines.", "citations": [], "gaps": []},
            "not json at all",
            {"answer": "Ines.", "citations": ["people/ines"]},
        ],
    )
    async def test_an_uncited_or_malformed_answer_is_refused(
        self, services, gateway, content
    ) -> None:
        _, router = services
        session = await gateway.open(context())
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        text = content if isinstance(content, str) else json.dumps(content)
        router.post(f"{BIFROST}/v1/messages").mock(
            return_value=httpx.Response(
                200, json={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
            )
        )
        with pytest.raises(AnswerRefusedError):
            await _answers(gateway).ask(
                session, "booking cut-over", layers=None, caller=ModelCaller.session("t")
            )

    async def test_nothing_retrieved_answers_with_a_gap_and_no_model_call(
        self, services, gateway
    ) -> None:
        _, router = services
        call = router.post(f"{BIFROST}/v1/messages")
        session = await gateway.open(context())
        answer = await _answers(gateway).ask(
            session, "zebra", layers=None, caller=ModelCaller.session("t")
        )
        assert answer.answer == "" and answer.citations == ()
        assert "Nothing in your layers" in answer.gaps[0]
        assert not call.called

    async def test_a_full_retrieval_is_a_gap_and_counted(self, services, gateway) -> None:
        _, router = services
        session = await gateway.open(context())
        for index in range(2):
            await _seed(session, f"services/booking-{index}", "booking cut-over notes")
        router.post(f"{BIFROST}/v1/messages").mock(
            return_value=_reply(
                {"answer": "", "citations": [], "gaps": ["the owner is not recorded"]}
            )
        )
        answer = await _answers(gateway, retrieval_count=2).ask(
            session, "booking cut-over", layers=None, caller=ModelCaller.session("t")
        )
        assert "the owner is not recorded" in answer.gaps
        assert any("memory.ask_retrieval_count" in gap for gap in answer.gaps)
        assert cap_hit_counts()["memory.ask_retrieval_count"] == 1

    async def test_disagreeing_layers_are_listed_as_gaps(self, services, gateway) -> None:
        _, router = services
        session = await gateway.open(context())
        await _seed(session, "services/booking-api", "p99 budget is 300 ms", layer=PROJECT)
        await _seed(session, "services/booking-api", "p99 budget is 500 ms", layer="org")
        router.post(f"{BIFROST}/v1/messages").mock(
            return_value=_reply(
                {
                    "answer": "300 ms.",
                    "citations": [{"layer": PROJECT, "ref": "services/booking-api"}],
                }
            )
        )
        answer = await _answers(gateway).ask(
            session, "p99 budget", layers=None, caller=ModelCaller.session("t")
        )
        assert any(gap.startswith("Disagreement:") for gap in answer.gaps)

    async def test_the_tool_answers_and_reports_a_cut_off_as_a_tool_error(
        self, services, gateway
    ) -> None:
        _, router = services
        session = await gateway.open(context())
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        tools = MemoryTools(gateway, _answers(gateway))
        assert "memory_ask" in [t["name"] for t in tools.definitions()]
        router.post(f"{BIFROST}/v1/messages").mock(
            side_effect=[
                _reply(
                    {"answer": "Ines.", "citations": [{"layer": PROJECT, "ref": "people/ines"}]}
                ),
                _reply({"answer": "Ines"}, stop_reason="max_tokens"),
            ]
        )
        ok = await tools.call(session, "memory_ask", {"question": "cut-over"}, credential="t")
        assert not ok["isError"]
        assert ok["structuredContent"]["citations"] == [{"layer": PROJECT, "ref": "people/ines"}]
        cut = await tools.call(session, "memory_ask", {"question": "cut-over"}, credential="t")
        assert cut["isError"] and cut["structuredContent"]["knob"] == "memory.answer_model"

    async def test_a_bifrost_refusal_is_a_tool_error(self, services, gateway) -> None:
        _, router = services
        session = await gateway.open(context())
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        router.post(f"{BIFROST}/v1/messages").mock(return_value=httpx.Response(401, text="no"))
        tools = MemoryTools(gateway, _answers(gateway))
        result = await tools.call(session, "memory_ask", {"question": "cut-over"}, credential="t")
        assert result["isError"] and "401" in result["content"][0]["text"]


class TestContextPack:
    async def test_it_holds_the_objectives_entities_and_makes_no_catalog_call(
        self, services, gateway
    ) -> None:
        _, router = services
        ctx = replace(context(), objective="change the booking cut-over plan")
        session = await gateway.open(ctx)
        await _seed(session, "plans/booking-cutover", "The cut-over runs on Friday.")
        pack = await _answers(gateway).context_pack(session)
        assert "plans/booking-cutover" in pack.text and "Friday" in pack.text
        assert not router.calls or all(
            "/models/" not in str(call.request.url) for call in router.calls
        )

    async def test_a_large_page_is_kept_whole_and_nothing_is_counted_as_left_out(
        self, services, gateway
    ) -> None:
        ctx = replace(context(), objective="booking cut-over")
        session = await gateway.open(ctx)
        body = "booking cut-over " + "x" * 20_000
        await _seed(session, "plans/booking-cutover", body)
        await _seed(session, "people/ines", "Ines owns the booking cut-over.")
        pack = await _answers(gateway).context_pack(session)
        assert body in pack.text
        assert "people/ines" in pack.text
        assert "did not fit" not in pack.text
        assert cap_hit_counts() == {}

    async def test_a_session_with_nothing_to_lead_to_gets_just_the_header(
        self, services, gateway
    ) -> None:
        session = await gateway.open(context())
        pack = await _answers(gateway).context_pack(session)
        assert pack.text.startswith("Memory context for this session")
        assert "##" not in pack.text

    async def test_changes_since_the_owners_last_session_are_listed(
        self, services, gateway
    ) -> None:
        session = await gateway.open(context())
        await _seed(session, "runbooks/deploy", "Deploy with the blue/green script.")
        since = datetime(2026, 1, 1, tzinfo=UTC)
        session = await gateway.open(replace(context(), since=since))
        pack = await _answers(gateway).context_pack(session)
        assert f"changed [{PROJECT}] runbooks/deploy" in pack.text
