"""LLM-backed reviewer for A2A workflow gates on commissioned tool builds.

When a tool-builder workflow pauses at a gate (``TASK_STATE_INPUT_REQUIRED``),
the commissioning Valkyrie — not a human, within her realm's autonomy grant —
answers it: she reads the gate's question against the build she commissioned
and replies ``approve`` or ``request_changes`` with a rationale. This module
turns the resident's own LLM into that reviewer. There is deliberately no
non-LLM fallback: a gate without a configured reviewer fails the build loudly
rather than being silently auto-approved.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest

logger = logging.getLogger(__name__)

#: (request, gate_context) -> (decision, notes); decision is
#: "approve" | "request_changes".
GateReviewer = Callable[[ToolBuildRequest, dict[str, Any]], Awaitable[tuple[str, str]]]

_APPROVE = "approve"
_REQUEST_CHANGES = "request_changes"

#: One retry on an unparseable verdict before failing the build loudly.
_MAX_VERDICT_ATTEMPTS = 2

#: The reviewer emits one short verdict line plus rationale.
_REVIEW_MAX_TOKENS = 400

_SYSTEM_PROMPT = (
    "You are {valkyrie_id}, a resident Valkyrie reviewing a workflow gate for "
    "a tool build YOU commissioned. Decide whether the work presented at the "
    "gate matches what you asked for. Reply with exactly one line starting "
    "with the word APPROVE or REQUEST_CHANGES, followed by a concise "
    "rationale (for REQUEST_CHANGES, state the specific changes needed). "
    "No other text."
)

_REVIEW_PROMPT = """A tool-builder workflow you commissioned has paused at a gate \
and asks for your decision.

## What you commissioned
- Tool name: {name}
- Description: {description}
- Build request: {build_request}
- Required permission: {required_permission}
- Declared reach: {declared_reach}

## The gate
- Label: {label}
- Condition: {condition}
- Instructions: {instructions}
- Summary: {summary}

Approve when the gate's subject matches the capability you commissioned; \
request focused changes when it drifts. One line: APPROVE <rationale> or \
REQUEST_CHANGES <required changes>."""


def build_llm_gate_reviewer(
    *,
    llm: Any,
    model: str,
    valkyrie_id: str,
) -> GateReviewer:
    """Return a GateReviewer that decides gates with the resident's own LLM."""

    async def _review(request: ToolBuildRequest, gate: dict[str, Any]) -> tuple[str, str]:
        prompt = _REVIEW_PROMPT.format(
            name=request.name,
            description=request.description,
            build_request=request.build_request,
            required_permission=request.required_permission,
            declared_reach=request.declared_reach,
            label=str(gate.get("label") or ""),
            condition=str(gate.get("condition") or ""),
            instructions=str(gate.get("instructions") or ""),
            summary=str(gate.get("summary") or ""),
        )
        last_content = ""
        for _attempt in range(_MAX_VERDICT_ATTEMPTS):
            response = await llm.generate(
                [{"role": "user", "content": prompt}],
                tools=[],
                system=_SYSTEM_PROMPT.format(valkyrie_id=valkyrie_id),
                model=model,
                max_tokens=_REVIEW_MAX_TOKENS,
            )
            last_content = str(getattr(response, "content", "") or "")
            verdict = parse_gate_verdict(last_content)
            if verdict is not None:
                decision, notes = verdict
                logger.info(
                    "gate review by %s: %s — %s",
                    valkyrie_id,
                    decision,
                    notes[:200],
                )
                return decision, notes
            logger.warning("gate review verdict unparseable, retrying: %r", last_content[:200])
        raise ToolBuildError(
            "gate reviewer produced no parseable APPROVE/REQUEST_CHANGES verdict: "
            f"{last_content[:200]!r}"
        )

    return _review


#: (request, question_context) -> answer text delivered to the asking peer.
QuestionAnswerer = Callable[[ToolBuildRequest, dict[str, Any]], Awaitable[str]]

_ANSWER_MAX_TOKENS = 700

_ANSWER_SYSTEM_PROMPT = (
    "You are {valkyrie_id}, a resident Valkyrie. An agent building a tool YOU "
    "commissioned is blocked on a question only you can answer. Answer it "
    "directly and concretely from what you know about the capability you "
    "requested. Give decisions, not options; if the question exceeds what you "
    "specified, decide now and say so. Plain text, no preamble."
)

_ANSWER_PROMPT = """An agent in the tool-builder workflow you commissioned is blocked and asks:

## Their question
- Persona: {persona}
- Question: {question}
- Why they are blocked: {reason}
- What they already tried: {attempted}
- Their suggestion: {recommendation}

## What you commissioned
- Tool name: {name}
- Description: {description}
- Build request: {build_request}
- Input schema: {input_schema}
- Required permission: {required_permission}
- Declared reach: {declared_reach}

Answer the question so they can continue. Be specific and decisive."""


def build_llm_question_answerer(
    *,
    llm: Any,
    model: str,
    valkyrie_id: str,
) -> QuestionAnswerer:
    """Return a QuestionAnswerer that answers peer questions with the resident's LLM."""

    async def _answer(request: ToolBuildRequest, question: dict[str, Any]) -> str:
        attempted = question.get("attempted")
        prompt = _ANSWER_PROMPT.format(
            persona=str(question.get("persona") or "unknown"),
            question=str(question.get("question") or question.get("summary") or ""),
            reason=str(question.get("reason") or ""),
            attempted="; ".join(str(item) for item in attempted)
            if isinstance(attempted, list) and attempted
            else "nothing stated",
            recommendation=str(question.get("recommendation") or "none"),
            name=request.name,
            description=request.description,
            build_request=request.build_request,
            input_schema=request.input_schema,
            required_permission=request.required_permission,
            declared_reach=request.declared_reach,
        )
        response = await llm.generate(
            [{"role": "user", "content": prompt}],
            tools=[],
            system=_ANSWER_SYSTEM_PROMPT.format(valkyrie_id=valkyrie_id),
            model=model,
            max_tokens=_ANSWER_MAX_TOKENS,
        )
        answer = str(getattr(response, "content", "") or "").strip()
        if not answer:
            raise ToolBuildError("question answerer produced an empty answer")
        logger.info(
            "question answered by %s for %s: %s",
            valkyrie_id,
            str(question.get("persona") or "peer"),
            answer[:200],
        )
        return answer

    return _answer


def parse_gate_verdict(content: str) -> tuple[str, str] | None:
    """Parse an LLM verdict into (decision, notes), or None when unparseable.

    Accepts the verdict on any line (models sometimes preface with
    reasoning); the first line whose first word is APPROVE or
    REQUEST_CHANGES wins.
    """
    for line in content.splitlines():
        stripped = line.strip().lstrip("*#>-").strip()
        if not stripped:
            continue
        head, _, rest = stripped.partition(" ")
        word = head.strip().strip(":.,").lower()
        if word == _APPROVE:
            return _APPROVE, rest.strip()
        if word in {_REQUEST_CHANGES, "request-changes", "changes_requested"}:
            return _REQUEST_CHANGES, rest.strip()
    return None
