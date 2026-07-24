from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.adapters.tools.ask_user import AskUserTool
from ravn.adapters.tools.file_tools import (
    GlobSearchTool,
    GrepSearchTool,
    ReadFileTool,
    WriteFileTool,
)
from ravn.agent import RavnAgent
from ravn.domain.events import RavnEventType
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.drive_loop import _parse_outcome_for_persona
from tests.ravn.fixtures.fakes import InMemoryChannel

KANUCK_MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "You are its resident Ravn. "
    "Help it become easier to run, more creative, and more successful. "
    "Ask before spending money or operating physical machines."
)


class RecordingLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict] = []

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: str,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        return next(self._responses)

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: str,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        response = await self.generate(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            thinking=thinking,
        )
        if response.content:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response.content)
        for tool_call in response.tool_calls:
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
        yield StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=response.usage)


def _tool_response(content: str, tool_call: ToolCall) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[tool_call],
        stop_reason=StopReason.TOOL_USE,
        usage=TokenUsage(input_tokens=40, output_tokens=12),
    )


def _text_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=120, output_tokens=60),
    )


def _outcome_text() -> str:
    return """\
---outcome---
verdict: oriented
orientation_summary: Inspected local catalog and ops notes for a small 3D printing business.
domain_hypotheses:
  - product catalog needs mapping from model files to sellable products
  - inventory and material tracking is inferred from stock/material signals
  - printability depends on supports, material, and test status
open_questions:
  - Where do the live store listings live?
  - Which printers or slicers may be observed read-only?
self_authored_work:
  - Map product/model catalog from discovered files
  - Draft inventory and material tracking shape from observed stock notes
  - Capture printability signals without operating machines
capability_gaps:
  - STL/3MF metadata inspection
  - slicer/support automation
  - printer telemetry observation
selected_next_action: Keep the local domain map and ask only for live listing/printer boundaries.
rationale: The resident produced a safe artifact without spending or operating machines.
---end---
"""


def _seed_workspace(workspace: Path) -> None:
    (workspace / "catalog").mkdir()
    (workspace / "ops").mkdir()
    (workspace / "catalog" / "models.md").write_text(
        "# Kanuck Valley Models Catalog\n"
        "- Forest barricade STL: two variants, PLA, needs support review.\n"
        "- Dungeon crates bundle: 12 finished units in stock, resin test pending.\n",
        encoding="utf-8",
    )
    (workspace / "ops" / "fulfillment.md").write_text(
        "# Fulfillment Notes\n"
        "- Orders are packed manually.\n"
        "- No single source of truth for raw material or finished inventory yet.\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_domain_drive_persona_runs_tools_and_produces_observed_outcome(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    persona = FilesystemPersonaAdapter().load("domain-drive")
    assert persona is not None
    assert persona.iteration_budget == 120
    assert persona.stop_on_outcome is True

    write_content = (
        "# Resident Domain Map\n\n"
        "Observed catalog files and fulfillment notes.\n\n"
        "- Hypothesis: inventory matters because stock/material notes were found.\n"
        "- Safe next action: keep mapping files and ask before printer operation.\n"
    )
    llm = RecordingLLM(
        [
            _tool_response(
                "I will inspect the local workspace before asking for anything.",
                ToolCall(
                    id="tc-glob",
                    name="glob_search",
                    input={"pattern": "**/*.md"},
                ),
            ),
            _tool_response(
                "I found candidate notes and will read the catalog.",
                ToolCall(
                    id="tc-read",
                    name="read_file",
                    input={"path": str(tmp_path / "catalog" / "models.md")},
                ),
            ),
            _tool_response(
                "I will search for inventory, material, and printability signals.",
                ToolCall(
                    id="tc-grep",
                    name="grep_search",
                    input={
                        "pattern": "inventory|stock|PLA|support|resin",
                        "path": str(tmp_path),
                        "glob": "**/*.md",
                    },
                ),
            ),
            _tool_response(
                "I have enough safe context to write a local domain map.",
                ToolCall(
                    id="tc-write",
                    name="write_file",
                    input={
                        "path": str(tmp_path / "resident-domain-map.md"),
                        "content": write_content,
                    },
                ),
            ),
            _text_response(_outcome_text()),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[
            GlobSearchTool(tmp_path),
            ReadFileTool(tmp_path),
            GrepSearchTool(tmp_path),
            WriteFileTool(tmp_path),
        ],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt=persona.system_prompt_template,
        model="claude-sonnet-4-6",
        max_tokens=4096,
        max_iterations=persona.iteration_budget,
        persona_config=persona,
        stop_on_outcome=persona.stop_on_outcome,
    )

    result = await agent.run_turn(KANUCK_MANDATE)

    tool_starts = [event for event in channel.events if event.type == RavnEventType.TOOL_START]
    assert [event.payload["tool_name"] for event in tool_starts] == [
        "glob_search",
        "read_file",
        "grep_search",
        "write_file",
    ]

    tool_result_text = "\n".join(
        event.payload.get("result", "")
        for event in channel.events
        if event.type == RavnEventType.TOOL_RESULT
    )
    assert "catalog/models.md" in tool_result_text
    assert "PLA" in tool_result_text
    assert "stock" in tool_result_text
    assert "Written:" in tool_result_text

    written_map = tmp_path / "resident-domain-map.md"
    assert written_map.exists()
    assert "inventory matters" in written_map.read_text(encoding="utf-8")

    last_llm_messages = repr(llm.calls[-1]["messages"])
    assert "Forest barricade STL" in last_llm_messages
    assert "No single source of truth" in last_llm_messages
    assert "Written:" in last_llm_messages

    parsed = _parse_outcome_for_persona(result.response, persona)
    assert parsed is not None
    assert parsed.valid
    assert parsed.fields["verdict"] == "oriented"
    assert "inventory and material tracking" in repr(parsed.fields["domain_hypotheses"])


@pytest.mark.asyncio
async def test_domain_drive_allows_model_selected_operator_question(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    persona = FilesystemPersonaAdapter().load("domain-drive")
    assert persona is not None

    async def _operator_input(question: str) -> str:
        assert question == "What should I focus on?"
        return "Focus on the product catalog."

    llm = RecordingLLM(
        [
            _tool_response(
                "I need clarification before I look.",
                ToolCall(
                    id="tc-ask-first",
                    name="ask_user",
                    input={"question": "What should I focus on?"},
                ),
            ),
            _tool_response(
                "I will inspect the workspace with that intent.",
                ToolCall(
                    id="tc-glob-after-defer",
                    name="glob_search",
                    input={"pattern": "**/*.md"},
                ),
            ),
            _tool_response(
                "I found domain files and will read one.",
                ToolCall(
                    id="tc-read-after-glob",
                    name="read_file",
                    input={"path": str(tmp_path / "catalog" / "models.md")},
                ),
            ),
            _text_response(_outcome_text()),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[AskUserTool(), GlobSearchTool(tmp_path), ReadFileTool(tmp_path)],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt=persona.system_prompt_template,
        model="claude-sonnet-4-6",
        max_tokens=4096,
        max_iterations=persona.iteration_budget,
        user_input_fn=_operator_input,
        persona_config=persona,
        stop_on_outcome=persona.stop_on_outcome,
    )

    result = await agent.run_turn(KANUCK_MANDATE)

    tool_results = [event for event in channel.events if event.type == RavnEventType.TOOL_RESULT]
    assert tool_results[0].payload["result"] == "Focus on the product catalog."
    assert tool_results[0].payload["is_error"] is False
    assert tool_results[1].payload["tool_name"] == "glob_search"
    assert "catalog/models.md" in tool_results[1].payload["result"]
    assert tool_results[2].payload["tool_name"] == "read_file"
    assert "Forest barricade STL" in tool_results[2].payload["result"]
    assert "verdict: oriented" in result.response


@pytest.mark.asyncio
async def test_domain_drive_does_not_enforce_tool_order(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    persona = FilesystemPersonaAdapter().load("domain-drive")
    assert persona is not None

    llm = RecordingLLM(
        [
            _tool_response(
                "I will read a guessed file before discovery.",
                ToolCall(
                    id="tc-read-first",
                    name="read_file",
                    input={"path": str(tmp_path / "catalog" / "models.md")},
                ),
            ),
            _tool_response(
                "I will now broaden the workspace search.",
                ToolCall(
                    id="tc-glob-after-defer",
                    name="glob_search",
                    input={"pattern": "**/*.md"},
                ),
            ),
            _tool_response(
                "I found the catalog and will read it now.",
                ToolCall(
                    id="tc-read-after-glob",
                    name="read_file",
                    input={"path": str(tmp_path / "catalog" / "models.md")},
                ),
            ),
            _tool_response(
                "I have enough safe context to leave a local map.",
                ToolCall(
                    id="tc-write",
                    name="write_file",
                    input={
                        "path": str(tmp_path / "resident-domain-map.md"),
                        "content": "# Resident Domain Map\n\nRecovered through discovery first.\n",
                    },
                ),
            ),
            _text_response(_outcome_text()),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[
            GlobSearchTool(tmp_path),
            ReadFileTool(tmp_path),
            WriteFileTool(tmp_path),
        ],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt=persona.system_prompt_template,
        model="claude-sonnet-4-6",
        max_tokens=4096,
        max_iterations=persona.iteration_budget,
        persona_config=persona,
        stop_on_outcome=persona.stop_on_outcome,
    )

    result = await agent.run_turn(KANUCK_MANDATE)

    tool_results = [event for event in channel.events if event.type == RavnEventType.TOOL_RESULT]
    assert tool_results[0].payload["tool_name"] == "read_file"
    assert "Forest barricade STL" in tool_results[0].payload["result"]
    assert tool_results[0].payload["is_error"] is False
    assert tool_results[1].payload["tool_name"] == "glob_search"
    assert "catalog/models.md" in tool_results[1].payload["result"]
    assert tool_results[2].payload["tool_name"] == "read_file"
    assert "Forest barricade STL" in tool_results[2].payload["result"]
    assert (tmp_path / "resident-domain-map.md").exists()
    assert "verdict: oriented" in result.response


@pytest.mark.asyncio
async def test_domain_drive_runtime_accepts_model_judgment_without_recipe(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    persona = FilesystemPersonaAdapter().load("domain-drive")
    assert persona is not None

    llm = RecordingLLM(
        [
            _tool_response(
                "I will list the workspace.",
                ToolCall(
                    id="tc-glob",
                    name="glob_search",
                    input={"pattern": "**/*.md"},
                ),
            ),
            _text_response(_outcome_text()),
            _tool_response(
                "The runtime rejected my shallow outcome. I will read a relevant file.",
                ToolCall(
                    id="tc-read",
                    name="read_file",
                    input={"path": str(tmp_path / "catalog" / "models.md")},
                ),
            ),
            _tool_response(
                "I have grounded context and will leave a local domain map.",
                ToolCall(
                    id="tc-write",
                    name="write_file",
                    input={
                        "path": str(tmp_path / "resident-domain-map.md"),
                        "content": "# Resident Domain Map\n\nGrounded after file inspection.\n",
                    },
                ),
            ),
            _text_response(_outcome_text()),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[
            GlobSearchTool(tmp_path),
            ReadFileTool(tmp_path),
            WriteFileTool(tmp_path),
        ],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt=persona.system_prompt_template,
        model="claude-sonnet-4-6",
        max_tokens=4096,
        max_iterations=persona.iteration_budget,
        persona_config=persona,
        stop_on_outcome=persona.stop_on_outcome,
    )

    result = await agent.run_turn(KANUCK_MANDATE)

    tool_starts = [event for event in channel.events if event.type == RavnEventType.TOOL_START]
    assert [event.payload["tool_name"] for event in tool_starts] == ["glob_search"]
    assert len(llm.calls) == 2
    assert "domain-drive response rejected" not in repr(llm.calls[-1]["messages"])
    assert not (tmp_path / "resident-domain-map.md").exists()
    assert "verdict: oriented" in result.response
