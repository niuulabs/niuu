"""Golden corpus coverage for the credential-free execution-tape replay harness."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.ravn.replay.execution_tape import (
    ExecutionTape,
    ExecutionTapeRunner,
    ScriptedModel,
    TapeInvariantError,
    TapeSchemaError,
    structural_diff,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "execution_tapes" / "v1"
_EXPECTED_CASES = {
    "plain_conversation",
    "multiple_tool_calls",
    "tool_failure",
    "pending_approval",
    "crash_before_dispatch",
    "crash_after_dispatch",
    "unknown_side_effect",
    "provider_session_loss",
    "compaction",
    "runtime_switch",
    "oversized_tool_result",
}


def _tapes() -> list[ExecutionTape]:
    return [ExecutionTape.load(path) for path in sorted(_FIXTURE_ROOT.glob("*.json"))]


def test_golden_execution_tape_corpus_replays_deterministically() -> None:
    tapes = _tapes()

    assert {tape.case for tape in tapes} == _EXPECTED_CASES
    for tape in tapes:
        first = ExecutionTapeRunner().run(tape)
        second = ExecutionTapeRunner().run(tape)
        assert first == second


def test_scripted_model_fails_for_unscripted_provider_calls() -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / "plain_conversation.json")
    model = ScriptedModel(tape.events)

    assert model.next_response()["kind"] == "model"
    with pytest.raises(TapeInvariantError, match="unscripted"):
        model.next_response()


def test_native_and_cli_backed_differential_mode_compares_only_canonical_facts() -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / "multiple_tool_calls.json")
    native = ExecutionTapeRunner(harness_kind="native").run(tape)
    cli_backed = ExecutionTapeRunner(harness_kind="cli_backed").run(tape)

    assert (native.harness_kind, cli_backed.harness_kind) == ("native", "cli_backed")
    assert structural_diff(native, cli_backed) == {}
    differences = structural_diff(native, replace(cli_backed, outcome="partial"))
    assert differences == {"outcome": ("success", "partial")}


@pytest.mark.parametrize(
    ("fixture", "runner"),
    [
        (
            "multiple_tool_calls.json",
            ExecutionTapeRunner(fold_event=lambda _events, _kind: None),
        ),
        (
            "multiple_tool_calls.json",
            ExecutionTapeRunner(ledger_result=lambda _completed, _call_id: None),
        ),
        (
            "compaction.json",
            ExecutionTapeRunner(context_safe=lambda _protected, _removed: False),
        ),
    ],
    ids=["fold", "ledger", "compaction"],
)
def test_deliberate_fold_ledger_and_compaction_regressions_fail(
    fixture: str, runner: ExecutionTapeRunner
) -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / fixture)

    with pytest.raises(TapeInvariantError):
        runner.run(tape)


def test_deliberate_capability_regression_fails() -> None:
    tape = ExecutionTape(
        case="capability-regression",
        capabilities=frozenset(),
        events=(
            {
                "kind": "model",
                "response": {"tool_calls": [{"id": "call-1"}]},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            {"kind": "dispatch", "tool_call_id": "call-1", "capability": "tool:read"},
            {"kind": "final", "outcome": "success"},
        ),
        expected={
            "event_kinds": ["model", "dispatch", "final"],
            "tool_order": [],
            "paired_tool_ids": [],
            "resume_points": [],
            "outcome": "success",
            "usage_shape": "reported",
            "context_safe": True,
            "capability_violations": 1,
            "unknown_side_effect": False,
        },
    )

    with pytest.raises(TapeInvariantError):
        ExecutionTapeRunner(capability_check=lambda _capabilities, _capability: True).run(tape)


def test_wrong_tape_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text('{"schema_version": 2}', encoding="utf-8")

    with pytest.raises(TapeSchemaError, match="schema_version"):
        ExecutionTape.load(path)
