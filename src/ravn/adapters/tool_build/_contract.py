"""Shared build contract: the prompt, the response parser, and polling.

A commissioned build (Forge session or Ting workflow) is instructed to finish
by emitting one JSON object — ``{"manifest": {...}, "tool_code": "..."}`` —
which both backends parse here so the contract cannot drift between them.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest, ToolBuildResult

_TOOL_BUILD_SYSTEM = (
    "You are a resident Valkyrie's build agent. You develop one small, "
    "dependency-light Python tool that a resident agent will call as an "
    "instrument during operational investigations. Build and verify the tool, "
    "then deliver it as a single JSON object and nothing else."
)


def build_prompts(request: ToolBuildRequest) -> tuple[str, str]:
    """Return (system_prompt, initial_prompt) for a commissioned tool build."""
    reach = json.dumps(request.declared_reach, indent=2, sort_keys=True)
    schema = json.dumps(request.input_schema, indent=2, sort_keys=True)
    initial = f"""Build a reusable agent tool for a resident Valkyrie.

Tool name: {request.name}
Environment: {request.environment_id} (domain {request.domain})
What it must do: {request.build_request}

Requirements:
- Implement `def {request.entry_point}(input: dict) -> dict` returning a
  JSON-serializable object.
- Input schema the resident will call it with:
{schema}
- Required permission: {request.required_permission}
- Declared reach (what it is allowed to touch):
{reach}
- {request.signal_context or "No additional signal context."}

Deliver exactly one JSON object and nothing else:
{{"manifest": {{"name": "{request.name}", "description": "...",
  "input_schema": {{...}}, "required_permission": "{request.required_permission}",
  "declared_reach": [...], "entry_point": "{request.entry_point}"}},
 "tool_code": "def {request.entry_point}(input): ..."}}"""
    return _TOOL_BUILD_SYSTEM, initial


def parse_tool_build_response(content: str, *, tool_name: str) -> ToolBuildResult:
    """Extract {manifest, tool_code} from a build agent's final output."""
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ToolBuildError(f"build output for {tool_name!r} contained no JSON object")
    try:
        document = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ToolBuildError(f"build output for {tool_name!r} is not valid JSON: {exc}") from exc
    manifest = document.get("manifest")
    tool_code = str(document.get("tool_code") or "")
    if not isinstance(manifest, dict) or not manifest:
        raise ToolBuildError(f"build output for {tool_name!r} is missing a manifest object")
    if not tool_code.strip():
        raise ToolBuildError(f"build output for {tool_name!r} is missing tool_code")
    manifest.setdefault("name", tool_name)
    return ToolBuildResult(manifest=manifest, tool_code=tool_code)


async def poll_until(
    fetch: Callable[[], Awaitable[Any]],
    is_done: Callable[[Any], bool],
    *,
    max_attempts: int,
    interval_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    """Poll ``fetch`` until ``is_done`` or attempts run out; returns the last value."""
    last: Any = None
    for attempt in range(max(max_attempts, 1)):
        last = await fetch()
        if is_done(last):
            return last
        if attempt + 1 < max_attempts:
            await sleep(interval_seconds)
    return last
