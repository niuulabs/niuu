"""The memory tools: instructions, results that name their layer, errors that name their cause."""

from __future__ import annotations

import pytest
import respx

from mimir.memory.gateway import MemoryGateway
from mimir.memory.systems import MemorySystems
from mimir.memory.tools import MemoryTools, UnknownToolError, instructions
from niuu.cap_telemetry import reset_cap_hits
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    PROJECT_ID,
    SESSION_ID,
    FakeGBrain,
    context,
    gbrain_backend,
)

URL = "url:https://linear.app/acme/LIN-482"


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


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


@pytest.fixture
def tools(gateway) -> MemoryTools:
    return MemoryTools(gateway)


async def _call(tools, session, tool, /, **arguments):
    return await tools.call(session, tool, arguments)


class TestInstructions:
    async def test_they_name_the_sessions_real_layers_and_prefixes(self, session) -> None:
        text = instructions(session)
        assert text.startswith(
            f"Your memory has layers: this session (session:{SESSION_ID}), project "
            f"{PROJECT_ID} (project:{PROJECT_ID}), and the org (org)."
        )
        assert "memory_search reads all of them" in text
        assert "memory_ask" not in text
        assert "write prefixes (decisions/, runbooks/)" in text
        assert "without evidence" in text

    async def test_a_session_without_a_project_has_no_project_rule(self, gateway) -> None:
        text = instructions(await gateway.open(context(project_id=None)))
        assert "project" not in text
        assert f"this session (session:{SESSION_ID}), and the org (org)" in text

    async def test_no_prefixes_says_to_propose(self, gateway) -> None:
        text = instructions(await gateway.open(context(prefixes=())))
        assert "none are set, so propose every change" in text

    def test_memory_ask_is_not_offered_without_an_answer_model(self, tools) -> None:
        names = [tool["name"] for tool in tools.definitions()]
        assert "memory_ask" not in names and len(names) == 10
        for tool in tools.definitions():
            assert tool["inputSchema"]["type"] == "object"


class TestArguments:
    async def test_an_unknown_tool_raises(self, tools, session) -> None:
        with pytest.raises(UnknownToolError):
            await tools.call(session, "memory_ask", {})

    @pytest.mark.parametrize(
        ("name", "arguments", "match"),
        [
            ("memory_search", [], "JSON object"),
            ("memory_search", {"query": "x", "bogus": 1}, "no argument bogus"),
            ("memory_search", {}, "query is required"),
            ("memory_search", {"query": 3}, "query must be a string"),
            ("memory_search", {"query": "x", "limit": "3"}, "limit must be an integer"),
            ("memory_search", {"query": "x", "limit": 0}, "at least 1"),
            ("memory_search", {"query": "x", "kinds": ["facts"]}, "kinds are page and source"),
            ("memory_search", {"query": "x", "layers": "org"}, "list of strings"),
            ("memory_recall", {"about": "x", "kinds": ["rumour"]}, "kinds are"),
            (
                "memory_add_timeline",
                {"ref": "p", "entry": "x", "evidence": [URL], "date": "nov"},
                "YYYY-MM-DD",
            ),
            (
                "memory_remember",
                {
                    "fact": "f",
                    "about": "a",
                    "kind": "fact",
                    "evidence": [URL],
                    "valid_until": "later",
                },
                "ISO 8601",
            ),
            (
                "memory_remember",
                {"fact": "f", "about": "a", "kind": "rumour", "evidence": [URL]},
                "unknown fact kind",
            ),
            (
                "memory_remember",
                {"fact": "f", "about": "a", "kind": "fact", "evidence": ["because"]},
                "free text",
            ),
            (
                "memory_remember",
                {"fact": "f", "about": "a", "kind": "fact", "evidence": []},
                "provenance",
            ),
            (
                "memory_add_source",
                {"title": "t", "content": "c", "kind": "song"},
                "unknown source kind",
            ),
            (
                "memory_propose",
                {"layer": "org", "change": "x", "evidence": [URL]},
                "must be an object",
            ),
            ("memory_propose", {"layer": "org", "change": {}, "evidence": [URL]}, "change.action"),
            (
                "memory_propose",
                {"layer": "org", "change": {"action": "remember"}, "evidence": [], "why": "w"},
                "needs evidence",
            ),
            (
                "memory_propose",
                {"layer": "org", "change": {"action": "remember"}, "evidence": [URL]},
                "why is required",
            ),
            ("memory_retire", {"fact": "1@l/org/x", "reason": " "}, "must not be empty"),
            ("memory_add_timeline", {"ref": "p", "entry": "a\nb", "evidence": [URL]}, "one line"),
        ],
    )
    async def test_bad_arguments_are_errors_that_say_why(
        self, tools, session, name, arguments, match
    ) -> None:
        result = await tools.call(session, name, arguments)
        assert result["isError"] is True
        assert match in result["content"][0]["text"]


class TestResults:
    async def test_writes_and_reads_round_trip_with_layer_labels(self, tools, session) -> None:
        written = await _call(
            tools,
            session,
            "memory_write_page",
            ref="findings/db",
            compiled_truth="staging DB is Postgres 15",
            title="DB",
            evidence=["commit:niuulabs/infra@abc1234"],
        )
        assert written["structuredContent"]["layer"] == f"session:{SESSION_ID}"
        added = await _call(
            tools,
            session,
            "memory_add_timeline",
            ref="findings/db",
            entry="pinned by the chart",
            evidence=["commit:niuulabs/infra@abc1234"],
            date="2026-11-10",
        )
        assert added["isError"] is False
        read = await _call(tools, session, "memory_read", ref="findings/db")
        page = read["structuredContent"]["pages"][0]
        assert page["layer"] == f"session:{SESSION_ID}"
        assert page["trust"] == "agent" and page["attested"] is True
        assert page["timeline"][0]["entry"] == "pinned by the chart"
        assert page["timeline"][0]["evidence"] == ["commit:niuulabs/infra@abc1234"]
        assert "staging DB is Postgres 15" in read["content"][0]["text"]

    async def test_a_stale_revision_is_an_error(self, tools, session) -> None:
        await _call(tools, session, "memory_write_page", ref="p", compiled_truth="one")
        again = await _call(tools, session, "memory_write_page", ref="p", compiled_truth="two")
        assert again["isError"] is True and "revision conflict" in again["content"][0]["text"]

    async def test_sources_search_and_read(self, tools, session) -> None:
        stored = await _call(
            tools,
            session,
            "memory_add_source",
            title="LIN-482",
            content="booking cutover owner is Ines",
            kind="ticket",
            url="https://linear.app/acme/LIN-482",
        )
        source_id = stored["structuredContent"]["source_id"]
        found = await _call(tools, session, "memory_search", query="cutover", kinds=["source"])
        assert found["structuredContent"]["hits"][0]["ref"] == f"source:{source_id}"
        read = await _call(tools, session, "memory_read", ref=f"source:{source_id}")
        assert read["structuredContent"]["sources"][0]["content"] == "booking cutover owner is Ines"
        missing = await _call(tools, session, "memory_read", ref="source:src_0000000000000000")
        assert missing["isError"] is True and "not found" in missing["content"][0]["text"]
        nothing = await _call(tools, session, "memory_search", query="zebra")
        assert nothing["content"][0]["text"] == "No matches in your layers."

    async def test_facts_recall_entity_retire(self, tools, session) -> None:
        remembered = await _call(
            tools,
            session,
            "memory_remember",
            fact="booking cut-over is owned by Ines",
            about="projects/booking-cutover",
            kind="commitment",
            evidence=[URL],
            valid_until="2027-01-01T00:00:00+00:00",
        )
        fact_id = remembered["structuredContent"]["fact_id"]
        recalled = await _call(tools, session, "memory_recall", about="projects/booking-cutover")
        assert recalled["structuredContent"]["facts"][0]["layer"] == f"session:{SESSION_ID}"
        assert "Ines" in recalled["content"][0]["text"]
        card = await _call(tools, session, "memory_entity", name="projects/booking-cutover")
        assert card["structuredContent"]["facts"][0]["id"] == fact_id
        read = await _call(tools, session, "memory_read", ref=f"fact:{fact_id}")
        assert read["structuredContent"]["facts"][0]["kind"] == "commitment"
        retired = await _call(tools, session, "memory_retire", fact=fact_id, reason="handed over")
        assert retired["structuredContent"]["retired"] is True
        empty = await _call(tools, session, "memory_recall", about="projects/booking-cutover")
        assert "No current facts" in empty["content"][0]["text"]
        unknown = await _call(tools, session, "memory_entity", name="people/nobody")
        assert "Nothing in your layers" in unknown["content"][0]["text"]

    async def test_a_proposal_result_names_its_approver(self, tools, session) -> None:
        result = await _call(
            tools,
            session,
            "memory_propose",
            layer="org",
            change={"action": "write_page", "ref": "handbook/oncall", "compiled_truth": "x"},
            evidence=[URL],
            why="On-call moved to a weekly rota.",
        )
        structured = result["structuredContent"]
        assert structured["written"] is False
        assert structured["proposal"]["approver"] == "an org admin"
        assert "an org admin must approve it" in result["content"][0]["text"]

    async def test_an_unreachable_layer_is_an_error_naming_it(self, brain, tools, session) -> None:
        brain.fail_with = 503
        result = await _call(tools, session, "memory_search", query="x")
        assert result["isError"] is True
        assert "acme-brain" in result["content"][0]["text"]
