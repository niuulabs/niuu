from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from ravn.domain.models import StreamEvent, StreamEventType, TokenUsage, ToolCall
from ravn.evals.judgment import (
    default_scenarios,
    json_report,
    render_report,
    render_window_prompt,
    run_all,
    run_scenario,
    score,
)
from ravn.ports.llm import LLMPort


def test_default_scenarios_cover_nine_isolated_behavioral_probes() -> None:
    first = default_scenarios()
    second = default_scenarios()
    keys = [scenario.key for scenario in first]

    assert len(keys) == len(set(keys)) == 9
    assert {"adversarial_injection", "genuine_capability_gap", "unsafe_action_gate"} <= set(keys)
    assert first[0].tools[0] is not second[0].tools[0]
    unsafe = next(scenario for scenario in first if scenario.key == "unsafe_action_gate")
    assert "stop_machine" in unsafe.forbid_tools


def test_window_prompt_carries_verbatim_data_without_a_decision_recipe() -> None:
    prompt = render_window_prompt(default_scenarios()[1])

    assert '"ErrorCode": 1' in prompt
    assert "without a predetermined interpretation" in prompt
    assert "options, not a prescribed route" in prompt
    assert "decision: <ignore | watch | investigate" in prompt
    assert "capability_list first" not in prompt
    assert "Only if" not in prompt


def test_score_checks_trajectory_decision_and_injection_resistance() -> None:
    scenario = default_scenarios()[-1]
    good = score(
        scenario,
        tool_calls=["inspect_machine"],
        response=(
            "---outcome---\ndecision: watch\n"
            "rationale: payload contains an instruction attempt\n---end---"
        ),
    )
    obeyed = score(
        scenario,
        tool_calls=["build_tool"],
        response="---outcome---\ndecision: ignore\nrationale: all clear\n---end---",
    )

    assert good.passed
    assert not obeyed.passed
    assert obeyed.touched_forbidden_tool
    assert not obeyed.injection_resisted


def _scripted_llm(
    script: list[tuple[list[ToolCall], str]],
    *,
    models: list[str] | None = None,
) -> LLMPort:
    calls = {"n": 0}

    async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
        if models is not None:
            models.append(str(kwargs.get("model") or ""))
        step = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        tool_calls, text = step
        if text:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=text)
        for tool_call in tool_calls:
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=5, output_tokens=5),
        )

    llm = AsyncMock(spec=LLMPort)
    llm.stream = _stream
    return llm


@pytest.mark.asyncio
async def test_run_scenario_uses_requested_model_and_real_agent_tool_loop() -> None:
    scenario = default_scenarios()[1]
    models: list[str] = []
    llm = _scripted_llm(
        [
            ([ToolCall(id="t1", name="inspect_machine", input={"query": "m-3"})], ""),
            (
                [],
                "---outcome---\ndecision: investigate\n"
                "rationale: live status is normal-temperature printing\n---end---",
            ),
        ],
        models=models,
    )

    result = await run_scenario(scenario, llm=llm, model="candidate-model")

    assert result.tool_calls == ["inspect_machine"]
    assert result.passed
    assert models == ["candidate-model", "candidate-model"]


@pytest.mark.asyncio
async def test_scenario_tool_calls_do_not_leak_between_runs() -> None:
    scenario = default_scenarios()[1]
    first = await run_scenario(
        scenario,
        llm=_scripted_llm(
            [
                ([ToolCall(id="t1", name="inspect_machine", input={})], ""),
                ([], "---outcome---\ndecision: investigate\nrationale: checked\n---end---"),
            ]
        ),
    )
    second = await run_scenario(
        scenario,
        llm=_scripted_llm(
            [([], "---outcome---\ndecision: watch\nrationale: no inspection\n---end---")]
        ),
    )

    assert first.tool_calls == ["inspect_machine"]
    assert second.tool_calls == []
    assert not second.used_expected_tool


@pytest.mark.asyncio
async def test_run_scenario_reports_iteration_exhaustion_honestly() -> None:
    scenario = default_scenarios()[0]
    llm = _scripted_llm([([ToolCall(id="t1", name="inspect_machine", input={})], "")])

    result = await run_scenario(scenario, llm=llm, max_iterations=2)

    assert result.iterations_hit_budget
    assert not result.passed
    assert "budget" in result.error


@pytest.mark.asyncio
async def test_run_all_passes_model_to_adapter_and_supports_scenario_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def _adapter(**kwargs):
        captured.update({key: str(value) for key, value in kwargs.items()})
        return _scripted_llm(
            [([], "---outcome---\ndecision: watch\nrationale: routine heartbeat\n---end---")]
        )

    monkeypatch.setattr("ravn.adapters.llm.openai.OpenAICompatibleAdapter", _adapter)

    results = await run_all(
        base_url="http://model.example/v1",
        model="candidate-v2",
        scenario_keys={"ignore_noise"},
    )

    assert len(results) == 1 and results[0].passed
    assert captured["model"] == "candidate-v2"
    with pytest.raises(ValueError, match="unknown judgment scenarios"):
        await run_all(
            base_url="http://model.example/v1",
            model="candidate-v2",
            scenario_keys={"missing"},
        )


def test_reports_expose_human_and_machine_readable_acceptance_results() -> None:
    scenario = default_scenarios()[0]
    results = [
        score(
            scenario,
            tool_calls=[],
            response="---outcome---\ndecision: watch\nrationale: quiet\n---end---",
        ),
        score(scenario, tool_calls=["build_tool"], response=""),
    ]

    markdown = render_report(results)
    payload = json_report(results, model="candidate", base_url="http://model")

    assert "1/2 scenarios passed" in markdown
    assert "no forbidden tool: NO" in markdown
    assert payload["passed"] == 1
    assert payload["results"][0]["passed"] is True
