"""Tests for the LLM-backed A2A workflow gate reviewer."""

from __future__ import annotations

import pytest

from ravn.adapters.tool_build.gate_review import (
    build_llm_gate_reviewer,
    build_llm_question_answerer,
    parse_gate_verdict,
)
from ravn.domain.exceptions import OutputTruncatedError
from ravn.domain.models import LLMResponse, StopReason, TokenUsage
from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest


def _request() -> ToolBuildRequest:
    return ToolBuildRequest(
        name="mimir_metric_window",
        description="Read a metric window.",
        build_request="Build a tool that reads pod restart counts.",
        input_schema={"type": "object"},
        required_permission="mimir:read",
        declared_reach=[],
        entry_point="run",
    )


_GATE = {
    "gateId": "gate-1",
    "label": "Confirm capability specification",
    "condition": "The framed spec must be confirmed.",
    "instructions": "Approve when the spec captures the intended tool.",
    "summary": "",
}


class _ScriptedLLM:
    def __init__(
        self, contents: list[str], *, stop_reason: StopReason = StopReason.END_TURN
    ) -> None:
        self._contents = list(contents)
        self._stop_reason = stop_reason
        self.calls: list[dict] = []

    async def generate(self, messages, *, tools, system, model, max_tokens):
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse(
            content=self._contents.pop(0),
            tool_calls=[],
            stop_reason=self._stop_reason,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )


class TestParseGateVerdict:
    def test_parses_approve_with_rationale(self) -> None:
        assert parse_gate_verdict("APPROVE the spec matches the request") == (
            "approve",
            "the spec matches the request",
        )

    def test_parses_request_changes(self) -> None:
        assert parse_gate_verdict("REQUEST_CHANGES declare network reach explicitly") == (
            "request_changes",
            "declare network reach explicitly",
        )

    def test_skips_preamble_lines(self) -> None:
        content = "Let me review the spec.\n\nAPPROVE: matches the commissioned tool"
        assert parse_gate_verdict(content) == ("approve", "matches the commissioned tool")

    def test_unparseable_returns_none(self) -> None:
        assert parse_gate_verdict("The spec looks fine to me.") is None


class TestLlmGateReviewer:
    async def test_approves_with_gate_context_in_prompt(self) -> None:
        llm = _ScriptedLLM(["APPROVE spec matches the commissioned capability"])
        reviewer = build_llm_gate_reviewer(
            llm=llm, model="qwen-test", valkyrie_id="valkyrie-valhalla-k8s"
        )

        decision, notes = await reviewer(_request(), _GATE)

        assert decision == "approve"
        assert notes == "spec matches the commissioned capability"
        prompt = llm.calls[0]["messages"][0]["content"]
        assert "Confirm capability specification" in prompt
        assert "Build a tool that reads pod restart counts." in prompt
        assert llm.calls[0]["model"] == "qwen-test"
        assert "valkyrie-valhalla-k8s" in llm.calls[0]["system"]

    async def test_retries_once_then_fails_loud(self) -> None:
        llm = _ScriptedLLM(["no verdict here", "still no verdict"])
        reviewer = build_llm_gate_reviewer(llm=llm, model="m", valkyrie_id="v")

        with pytest.raises(ToolBuildError, match="no parseable"):
            await reviewer(_request(), _GATE)

        assert len(llm.calls) == 2

    async def test_second_attempt_can_succeed(self) -> None:
        llm = _ScriptedLLM(["garbage", "REQUEST_CHANGES pin the input schema types"])
        reviewer = build_llm_gate_reviewer(llm=llm, model="m", valkyrie_id="v")

        decision, notes = await reviewer(_request(), _GATE)

        assert decision == "request_changes"
        assert notes == "pin the input schema types"


class TestLlmQuestionAnswerer:
    async def test_answers_with_build_context_in_prompt(self) -> None:
        from ravn.adapters.tool_build.gate_review import build_llm_question_answerer

        llm = _ScriptedLLM(["All namespaces; read-only; report GiB per namespace."])
        answerer = build_llm_question_answerer(
            llm=llm, model="qwen-test", valkyrie_id="valkyrie-valhalla-k8s"
        )
        question = {
            "persona": "specification-framer",
            "question": "Which namespaces are in scope?",
            "reason": "needs_context",
            "recommendation": "All namespaces.",
            "attempted": ["re-read the request"],
        }

        answer = await answerer(_request(), question)

        assert answer.startswith("All namespaces")
        prompt = llm.calls[0]["messages"][0]["content"]
        assert "Which namespaces are in scope?" in prompt
        assert "Build a tool that reads pod restart counts." in prompt
        assert "re-read the request" in prompt

    async def test_empty_answer_fails_loud(self) -> None:
        from ravn.adapters.tool_build.gate_review import build_llm_question_answerer

        llm = _ScriptedLLM(["   "])
        answerer = build_llm_question_answerer(llm=llm, model="m", valkyrie_id="v")

        with pytest.raises(ToolBuildError, match="empty answer"):
            await answerer(_request(), {"question": "anything"})


class TestNoAnswerCap:
    """A reasoning model thinks before its verdict; a cap sized for the verdict cut that off."""

    async def test_review_sends_no_cap_and_refuses_a_cut_off_verdict(self) -> None:
        llm = _ScriptedLLM(["APPROVE look"], stop_reason=StopReason.MAX_TOKENS)
        reviewer = build_llm_gate_reviewer(llm=llm, model="m", valkyrie_id="v")

        with pytest.raises(OutputTruncatedError, match="tool_build.gate_review"):
            await reviewer(_request(), _GATE)
        assert llm.calls[0]["max_tokens"] is None

    async def test_answer_sends_no_cap_and_refuses_a_cut_off_answer(self) -> None:
        llm = _ScriptedLLM(["All namespa"], stop_reason=StopReason.MAX_TOKENS)
        answerer = build_llm_question_answerer(llm=llm, model="m", valkyrie_id="v")

        with pytest.raises(OutputTruncatedError, match="tool_build.question_answer"):
            await answerer(_request(), {"question": "Which namespaces?"})
        assert llm.calls[0]["max_tokens"] is None
