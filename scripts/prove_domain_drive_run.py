"""Run a visible Resident Domain Drive proof with real Ravn tools.

This is intentionally deterministic: the LLM is scripted, while the Ravn agent,
persona loading, file tools, channel events, and artifact write are real.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.adapters.tools.file_tools import (
    GlobSearchTool,
    GrepSearchTool,
    ReadFileTool,
    WriteFileTool,
)
from ravn.agent import RavnAgent
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.channel import ChannelPort

MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "You are its resident Ravn. "
    "Help it become easier to run, more creative, and more successful. "
    "Ask before spending money or operating physical machines."
)


class TimelineChannel(ChannelPort):
    def __init__(self) -> None:
        self.events: list[RavnEvent] = []

    async def emit(self, event: RavnEvent) -> None:
        self.events.append(event)


class ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = iter(responses)
        self.call_count = 0

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
        self.call_count += 1
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


def _scripted_llm(workspace: Path) -> ScriptedLLM:
    write_content = (
        "# Resident Domain Map\n\n"
        "Observed catalog files and fulfillment notes.\n\n"
        "- Hypothesis: inventory matters because stock/material notes were found.\n"
        "- Safe next action: keep mapping files and ask before printer operation.\n"
    )
    outcome = """\
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
    return ScriptedLLM(
        [
            _tool_response(
                "I will inspect the local workspace before asking for anything.",
                ToolCall(id="tc-glob", name="glob_search", input={"pattern": "**/*.md"}),
            ),
            _tool_response(
                "I found candidate notes and will read the catalog.",
                ToolCall(
                    id="tc-read",
                    name="read_file",
                    input={"path": str(workspace / "catalog" / "models.md")},
                ),
            ),
            _tool_response(
                "I will search for inventory, material, and printability signals.",
                ToolCall(
                    id="tc-grep",
                    name="grep_search",
                    input={
                        "pattern": "inventory|stock|PLA|support|resin",
                        "path": str(workspace),
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
                        "path": str(workspace / "resident-domain-map.md"),
                        "content": write_content,
                    },
                ),
            ),
            _text_response(outcome),
        ]
    )


async def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="domain-drive-proof-", dir="/private/tmp"))
    _seed_workspace(workspace)

    persona = FilesystemPersonaAdapter().load("domain-drive")
    if persona is None:
        raise RuntimeError("domain-drive persona not found")

    channel = TimelineChannel()
    llm = _scripted_llm(workspace)
    agent = RavnAgent(
        llm=llm,
        tools=[
            GlobSearchTool(workspace),
            ReadFileTool(workspace),
            GrepSearchTool(workspace),
            WriteFileTool(workspace),
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

    result = await agent.run_turn(MANDATE)

    print(f"workspace: {workspace}")
    print(f"persona: {persona.name}")
    print(f"iteration_budget: {persona.iteration_budget}")
    print(f"stop_on_outcome: {persona.stop_on_outcome}")
    print(f"llm_calls: {llm.call_count}")
    print("\ntool timeline:")
    for event in channel.events:
        if event.type == RavnEventType.TOOL_START:
            print(f"- start {event.payload['tool_name']}: {event.payload.get('input')}")
        elif event.type == RavnEventType.TOOL_RESULT:
            result_text = event.payload.get("result", "").replace("\n", " ")
            print(f"- result {event.payload['tool_name']}: {result_text[:180]}")

    artifact = workspace / "resident-domain-map.md"
    print(f"\nartifact: {artifact}")
    print(artifact.read_text(encoding="utf-8"))
    print("final outcome:")
    print(result.response)


if __name__ == "__main__":
    asyncio.run(main())
