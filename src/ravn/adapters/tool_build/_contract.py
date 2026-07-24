"""Shared build contract: the prompt, the response parser, and polling.

A commissioned build (Forge session or Ting workflow) is instructed to write
one canonical file — ``learned_tool.json`` at the workspace root, holding
``{"manifest", "tool_code", "test_code", "requirements"}`` — and to also emit
that same JSON object in its final message as a fallback. Both backends parse
the shape here (via :func:`parse_tool_build_document`) so the contract cannot
drift between them.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest, ToolBuildResult

#: The single canonical artifact file the builder writes at the workspace root.
CANONICAL_ARTIFACT_FILENAME = "learned_tool.json"

_TOOL_BUILD_SYSTEM = (
    "You are a resident Valkyrie's build agent. You develop one small, "
    "dependency-light Python tool that a resident agent will call as an "
    "instrument during operational investigations. Build the tool, write a "
    "test that exercises it, then deliver the canonical artifact file plus a "
    "single JSON object and nothing else."
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
- Also produce `test_code`: a self-contained pytest/asserts module that imports
  `_verify_tool` (the verifier loads `tool_code` under that module name) and
  exercises `_verify_tool.{request.entry_point}` on representative input. The
  tests must not read `learned_tool.json` or depend on the builder workspace.
- Also produce `requirements`: a list of pip package requirement strings the
  tool needs at runtime (use [] when the tool is stdlib-only).

Write the final artifact as a single canonical file named
`{CANONICAL_ARTIFACT_FILENAME}` at the repo/workspace root, containing:
{{"manifest": {{...}}, "tool_code": "...", "test_code": "...", "requirements": [...]}}

Then, as a fallback, deliver that exact same JSON object as your final message
and nothing else:
{{"manifest": {{"name": "{request.name}", "description": "...",
  "input_schema": {{...}}, "required_permission": "{request.required_permission}",
  "declared_reach": [...], "entry_point": "{request.entry_point}"}},
 "tool_code": "def {request.entry_point}(input): ...",
 "test_code": "def test_{request.entry_point}(): ...",
 "requirements": []}}"""
    return _TOOL_BUILD_SYSTEM, initial


def decode_canonical_document(value: Any) -> dict[str, Any] | None:
    """Decode a canonical ``learned_tool.json`` payload from any retrieval surface.

    The Forge file-download route may return a parsed JSON object or raw JSON
    text; the Ting artifact endpoint returns a JSON string (or, defensively, an
    already-parsed object). One decoder serves both backends so their
    interpretation of the canonical artifact can never drift.
    """
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_tool_build_document(document: dict[str, Any], *, tool_name: str) -> ToolBuildResult:
    """Build a :class:`ToolBuildResult` from a parsed contract document.

    Defined once so the canonical-file path and the scrape path produce the
    same shape. ``test_code`` and ``requirements`` are optional (default ``""``
    and ``[]``) so the parser stays backward compatible with older builders.
    """
    if not isinstance(document, dict):
        raise ToolBuildError(f"build output for {tool_name!r} is not a JSON object")
    manifest = document.get("manifest")
    tool_code = str(document.get("tool_code") or "")
    if not isinstance(manifest, dict) or not manifest:
        raise ToolBuildError(f"build output for {tool_name!r} is missing a manifest object")
    if not tool_code.strip():
        raise ToolBuildError(f"build output for {tool_name!r} is missing tool_code")
    manifest.setdefault("name", tool_name)
    test_code = str(document.get("test_code") or "")
    requirements = [
        item.strip()
        for item in list(document.get("requirements") or [])
        if isinstance(item, str) and item.strip()
    ]
    return ToolBuildResult(
        manifest=manifest,
        tool_code=tool_code,
        test_code=test_code,
        requirements=requirements,
    )


def parse_tool_build_response(content: str, *, tool_name: str) -> ToolBuildResult:
    """Extract the contract document from a build agent's final message text."""
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
    return parse_tool_build_document(document, tool_name=tool_name)


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
