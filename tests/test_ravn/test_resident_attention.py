"""Per-slot System One opinions on resident inbox slots, the attention gate, and its calibration.

§3.1 of docs/design/system-one-decision-models.md: judge each coalescing slot when a home turn
selects it, observe-only first; the gate that lets a routine slot skip its turn is built but off
until §5's offline calibration fits a threshold.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.config import (
    InitiativeConfig,
    ResidentAttentionGateConfig,
    ResidentFirstOpinionConfig,
    Settings,
)
from ravn.domain.models import (
    RESIDENT_HOME_TRIGGER,
    AgentTask,
    OutputMode,
    TokenUsage,
    TurnResult,
)
from ravn.domain.resident_continuation import ResidentTurnRecord, ResidentWorkingStateRecord
from ravn.drive_loop import DriveLoop
from ravn.ports.decision import ChoiceAnswer, DecisionResult, NoulAnswer
from ravn.resident_attention import (
    PURPOSE,
    QUESTIONS,
    AttentionGatePolicy,
    ResidentSlotAttention,
    SlotOpinion,
    gate_eligible,
    opinion_mandate,
    working_state_from_page,
)
from ravn.resident_attention_calibration import CalibrationRow, calibrate, sweep
from ravn.resident_first_opinion import ResidentFirstOpinion
from ravn.resident_inbox import (
    LocalResidentInbox,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ShapeAggregate,
)
from ravn.resident_runtime import ResidentRuntime

ROUTINE = "ROUTINE"


class _Span:
    def set_attribute(self, *_args: Any) -> None:
        return None


class _Telemetry:
    def __init__(self) -> None:
        self.counts: list[tuple[str, dict]] = []
        self.records: list[tuple[str, float, dict]] = []
        self.events: list[tuple[str, dict, Any]] = []

    @contextmanager
    def span(self, *_args: Any, **_kwargs: Any):
        yield _Span()

    def count(self, name: str, value: int = 1, *, attributes=None, **_kwargs: Any) -> None:
        self.counts.append((name, dict(attributes or {})))

    def record(self, name: str, value: float, *, attributes=None, **_kwargs: Any) -> None:
        self.records.append((name, value, dict(attributes or {})))

    def event(self, name: str, *, attributes=None, content=None, **_kwargs: Any) -> None:
        self.events.append((name, dict(attributes or {}), content))

    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


@pytest.fixture
def telemetry(monkeypatch: pytest.MonkeyPatch) -> _Telemetry:
    recorder = _Telemetry()
    monkeypatch.setattr("ravn.resident_attention.get_observability", lambda: recorder)
    return recorder


def _distribution(choice: str, options: tuple[str, ...], mass: float) -> ChoiceAnswer:
    rest = (1.0 - mass) / (len(options) - 1)
    return ChoiceAnswer(
        choice=choice,
        confidence=mass,
        probabilities={option: mass if option == choice else rest for option in options},
    )


class _Decisions:
    """Routine-looking slots get a confident routine opinion; anything else wakes."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[dict, str]] = []

    async def decide(self, state, questions, *, purpose):
        self.calls.append((state, purpose))
        if self.error is not None:
            raise self.error
        routine = ROUTINE in state["observation"]
        tiers = tuple(questions["tier"].options)
        decisions = tuple(questions["decision"].options)
        return DecisionResult(
            model="jev-1.14.0",
            answers={
                "tier": _distribution("silent" if routine else "urgent", tiers, 0.94),
                "decision": _distribution("ignore" if routine else "escalate", decisions, 0.94),
                "needs_investigation": NoulAnswer(0.02 if routine else 0.9),
            },
            input_tokens=120,
        )


def _policy(threshold: float | None = None) -> AttentionGatePolicy:
    return AttentionGatePolicy(
        skip_tiers=frozenset({"silent", "ambient"}),
        skip_decisions=frozenset({"ignore", "watch"}),
        min_skip_probability=threshold,
    )


def _attention(decisions: _Decisions, *, gate: float | None = None) -> ResidentSlotAttention:
    return ResidentSlotAttention(
        decisions,
        policy=_policy(gate),
        gate_enabled=gate is not None,
        max_observation_chars=20000,
    )


def _signal(message: str, *, field: str, severity: str = "warning", **extra) -> ResidentInboxSignal:
    return ResidentInboxSignal(
        id=f"evt-{field}-{message[:6]}",
        source="kubernetes-events",
        kind="signal.kubernetes.event",
        summary=message,
        payload={"severity": severity, "message": message, field: 1, **extra},
        observed_at="2026-10-28T09:00:00+00:00",
    )


async def _runtime(tmp_path, decisions: _Decisions, *, gate: float | None = None):
    inbox = LocalResidentInbox(tmp_path / "inbox")
    state = LocalResidentState(tmp_path / "state")
    runtime = ResidentRuntime(
        state=state,
        inbox=inbox,
        charter="Keep ymir healthy.",
        resident_personality="terse",
        resident_id="sigrun",
    )
    runtime.bind_slot_attention(_attention(decisions, gate=gate))
    return runtime, inbox, state


async def _home(runtime: ResidentRuntime) -> AgentTask | None:
    return await runtime.next_home_task(limit=50, persona=None, output_mode=OutputMode.AMBIENT)


# ---------------------------------------------------------------------------
# What is asked
# ---------------------------------------------------------------------------


def test_options_are_ordered_wake_first() -> None:
    # Choice answers lean toward the first option; that bias must favour attention.
    assert list(QUESTIONS["tier"].options) == ["urgent", "present", "ambient", "silent"]
    decisions = list(QUESTIONS["decision"].options)
    assert decisions[0] == "escalate"
    assert decisions[-2:] == ["watch", "ignore"]
    assert sorted(decisions) == sorted(
        ["ignore", "watch", "investigate", "propose_action", "escalate", "learn", "blocked"]
    )


@pytest.mark.asyncio
async def test_observe_mode_judges_each_selected_slot_and_changes_nothing(tmp_path) -> None:
    decisions = _Decisions()
    runtime, inbox, state = await _runtime(tmp_path, decisions)
    await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="sigrun",
            state={"observations": ["api pods flap after deploys"]},
            source_turn_ref="t",
            source_case_id="c",
            source_task_id="x",
        )
    )
    for _ in range(3):
        await inbox.write_signal(_signal(f"{ROUTINE} probe", field="probe"))
    await inbox.write_signal(_signal("etcd leader lost", field="etcd"))

    task = await _home(runtime)

    assert task is not None
    assert [purpose for _state, purpose in decisions.calls] == [PURPOSE, PURPOSE]
    states = [state for state, _purpose in decisions.calls]
    assert all(state["mandate"] == "Keep ymir healthy.\n\nterse" for state in states)
    assert all("api pods flap after deploys" in state["working_state"] for state in states)
    probe = next(state for state in states if ROUTINE in state["observation"])
    # Counting is done in code and handed over, not left to the model.
    assert "observations: 3" in probe["observation"]
    # Observe mode: every slot is in the turn in full, and every opinion travels with it.
    assert sorted(task.resident_slot_opinions) == sorted(task.resident_inbox_refs)
    assert len(task.resident_inbox_refs) == 2
    assert "Since you last thought" not in task.initiative_context


@pytest.mark.asyncio
async def test_a_failing_decision_model_fails_the_home_wake(tmp_path) -> None:
    runtime, inbox, _state = await _runtime(
        tmp_path, _Decisions(error=RuntimeError("jev unreachable"))
    )
    await inbox.write_signal(_signal("anything", field="x"))

    with pytest.raises(RuntimeError, match="jev unreachable"):
        await _home(runtime)


@pytest.mark.asyncio
async def test_the_turn_records_each_slot_opinion_beside_its_outcome(tmp_path, telemetry) -> None:
    runtime, inbox, _state = await _runtime(tmp_path, _Decisions())
    await inbox.write_signal(_signal(f"{ROUTINE} probe", field="probe"))
    task = await _home(runtime)
    assert task is not None

    await runtime.handle_completed_turn(
        task=task,
        prompt="p",
        result=TurnResult(
            response="r",
            tool_calls=[],
            tool_results=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            episode=SimpleNamespace(structured_outcome={}),
        ),
        response_text="r",
        outcome_fields={"tier": "ambient", "decision": "watch", "continuation": "stop"},
        outcome_valid=True,
    )

    comparisons = [a for name, a in telemetry.counts if name.endswith("slot_opinion.comparisons")]
    assert {a["ravn.decision.question"] for a in comparisons} == {"tier", "decision"}
    tier = next(a for a in comparisons if a["ravn.decision.question"] == "tier")
    assert tier["ravn.decision.first_opinion"] == "silent"
    assert tier["ravn.decision.resident"] == "ambient"
    assert tier["ravn.decision.agreed"] is False
    assert tier["ravn.decision.turn_needed"] is False
    # No threshold fitted yet, so there is no would-skip to report.
    assert "ravn.decision.gate.would_skip" not in tier
    [(_name, skip_probability, _attrs)] = telemetry.records
    assert skip_probability > 0.9


def test_turn_level_first_opinion_leaves_home_turns_to_the_slots() -> None:
    home = AgentTask(
        task_id="h",
        title="home",
        initiative_context="x",
        triggered_by=RESIDENT_HOME_TRIGGER,
        output_mode=OutputMode.AMBIENT,
        resident_case_id="resident-home-1",
    )
    answer = AgentTask(
        task_id="a",
        title="answer",
        initiative_context="x",
        triggered_by="resident:operator_answer",
        output_mode=OutputMode.SURFACE,
        resident_case_id="case-1",
    )

    assert ResidentFirstOpinion.applies_to(home) is False
    assert ResidentFirstOpinion.applies_to(answer) is True


def test_slot_opinions_survive_the_queue_journal(tmp_path) -> None:
    opinion = SlotOpinion(
        ref="pending/a.md",
        last_archive_ref="raw/2026-10-28.ndjson#4",
        model="jev-1.14.0",
        tier="silent",
        tier_probabilities={"silent": 0.9, "urgent": 0.1},
        decision="ignore",
        decision_probabilities={"ignore": 0.9, "escalate": 0.1},
        needs_investigation=0.1,
        eligible=True,
    )
    task = AgentTask(
        task_id="h",
        title="home",
        initiative_context="x",
        triggered_by=RESIDENT_HOME_TRIGGER,
        output_mode=OutputMode.AMBIENT,
        resident_slot_opinions={"pending/a.md": opinion.to_record()},
    )
    (tmp_path / "queue.json").write_text(
        json.dumps({"queue": [DriveLoop._task_journal_record(task)], "inflight": []})
    )
    loop = DriveLoop(
        agent_factory=lambda **_kwargs: None,
        config=InitiativeConfig(enabled=True, queue_journal_path=str(tmp_path / "queue.json")),
        settings=Settings(),
    )

    loop._load_journal()

    restored = next(task for _p, _c, task in loop._queue._queue)
    assert SlotOpinion.from_record(restored.resident_slot_opinions["pending/a.md"]) == opinion


# ---------------------------------------------------------------------------
# What may be gated, and the gate itself (built, off by default)
# ---------------------------------------------------------------------------


def test_only_ordinary_slots_are_ever_eligible() -> None:
    assert gate_eligible(_signal("probe", field="probe")) is True
    assert gate_eligible(_signal("BackOff", field="probe", severity="critical")) is False
    folded_critical = _signal("probe", field="probe").with_updates(
        aggregate=ShapeAggregate(categorical={"severity": ("warning", "critical")})
    )
    assert gate_eligible(folded_critical) is False
    unknown_severity = _signal("probe", field="probe").with_updates(
        aggregate=ShapeAggregate(high_cardinality=("severity",))
    )
    assert gate_eligible(unknown_severity) is False
    assert gate_eligible(_signal("x", field="p").with_updates(shape_key="abc-novel")) is False
    assert gate_eligible(_signal("x", field="p").with_updates(source="a2a:push")) is False
    assert (
        gate_eligible(_signal("x", field="p").with_updates(kind="operator.directed_message"))
        is False
    )


@pytest.mark.asyncio
async def test_gate_keeps_routine_slots_out_of_the_turn_but_in_its_receipt(tmp_path) -> None:
    runtime, inbox, _state = await _runtime(tmp_path, _Decisions(), gate=0.9)
    await inbox.write_signal(_signal(f"{ROUTINE} probe", field="probe"))
    await inbox.write_signal(_signal("etcd leader lost", field="etcd"))

    task = await _home(runtime)

    assert task is not None
    context = task.initiative_context
    observations, receipt = context.split("Since you last thought")
    assert "etcd leader lost" in observations
    assert ROUTINE not in observations
    assert f"summary={ROUTINE} probe" in receipt
    # The turn acknowledges both; only the slot it judged in full is compared.
    assert len(task.resident_inbox_refs) == 2
    assert len(task.resident_slot_opinions) == 1


@pytest.mark.asyncio
async def test_a_wholly_routine_inbox_gets_no_turn_and_is_not_asked_twice(tmp_path) -> None:
    decisions = _Decisions()
    runtime, inbox, _state = await _runtime(tmp_path, decisions, gate=0.9)
    await inbox.write_signal(_signal(f"{ROUTINE} probe", field="probe"))

    assert await _home(runtime) is None
    assert await _home(runtime) is None
    # The slot stays pending for the next turn's receipt, and an unchanged slot
    # is not judged again.
    assert len(await inbox.list_signals(limit=10)) == 1
    assert len(decisions.calls) == 1

    await inbox.write_signal(_signal("etcd leader lost", field="etcd"))
    task = await _home(runtime)
    assert task is not None
    assert "Since you last thought" in task.initiative_context
    assert len(decisions.calls) == 2


def test_gate_cannot_run_without_a_fitted_threshold() -> None:
    with pytest.raises(ValueError, match="min_skip_probability"):
        ResidentSlotAttention(
            _Decisions(), policy=_policy(), gate_enabled=True, max_observation_chars=10
        )
    with pytest.raises(ValidationError, match="min_skip_probability"):
        ResidentAttentionGateConfig(enabled=True)
    with pytest.raises(ValidationError, match="requires"):
        ResidentFirstOpinionConfig(
            gate=ResidentAttentionGateConfig(enabled=True, min_skip_probability=0.9)
        )
    with pytest.raises(ValidationError, match="unknown"):
        ResidentAttentionGateConfig(skip_tiers=["quiet"])
    assert Settings().decision.resident_first_opinion.gate.enabled is False


def test_skip_probability_needs_all_three_questions_routine() -> None:
    policy = _policy(0.8)
    opinion = SlotOpinion(
        ref="r",
        last_archive_ref="",
        model="m",
        tier="silent",
        tier_probabilities={"silent": 0.6, "ambient": 0.3, "urgent": 0.1},
        decision="watch",
        decision_probabilities={"watch": 0.5, "ignore": 0.45, "escalate": 0.05},
        needs_investigation=0.2,
        eligible=True,
    )

    assert policy.skip_probability(opinion) == pytest.approx(0.8)
    assert policy.would_skip(opinion) is True
    assert policy.would_skip(SlotOpinion(**{**opinion.__dict__, "eligible": False})) is False
    assert _policy().would_skip(opinion) is None
    assert policy.turn_needed({"tier": "present", "decision": "watch"}, outcome_valid=True)
    assert not policy.turn_needed({"tier": "silent", "decision": "ignore"}, outcome_valid=True)
    assert policy.turn_needed({"tier": "silent"}, outcome_valid=True) is None
    assert policy.turn_needed({"tier": "silent", "decision": "x"}, outcome_valid=False) is None


def test_working_state_page_must_carry_its_state() -> None:
    page = '# Resident Working State\n\n## State\n\n```json\n{"observations": ["a"]}\n```\n'
    assert working_state_from_page(page) == {"observations": ["a"]}
    with pytest.raises(ValueError, match="no `## State`"):
        working_state_from_page("# Resident Working State\n")
    with pytest.raises(ValueError, match="not a mapping"):
        working_state_from_page("## State\n```json\n[1]\n```")
    assert opinion_mandate("", "") == "(no charter configured)"


def test_observation_is_bounded_and_the_cut_reported(telemetry) -> None:
    attention = ResidentSlotAttention(
        _Decisions(), policy=_policy(), gate_enabled=False, max_observation_chars=40
    )
    state = attention.state(
        "pending/a.md", _signal("x" * 200, field="p"), mandate="m", working_state=None
    )

    assert len(state["observation"]) == 40
    assert state["working_state"] == "(no working state recorded yet)"


# ---------------------------------------------------------------------------
# Offline calibration (§5 step 2)
# ---------------------------------------------------------------------------


def _row(skip: float, needed: bool | None) -> CalibrationRow:
    return CalibrationRow(
        ref="r",
        case_id="c",
        turn_index=1,
        observations=1,
        eligible=True,
        model="m",
        tier="silent",
        decision="ignore",
        needs_investigation=0.0,
        skip_probability=skip,
        turn_needed=needed,
        resident_tier="silent",
        resident_decision="ignore",
    )


def test_sweep_reports_coverage_and_misses_at_every_observed_probability() -> None:
    points = sweep([_row(0.95, False), _row(0.9, False), _row(0.8, True), _row(0.5, False)])

    assert [point.threshold for point in points] == [0.95, 0.9, 0.8, 0.5]
    assert [point.misses for point in points] == [0, 0, 1, 1]
    assert points[1].coverage == 0.5
    assert points[2].miss_rate == pytest.approx(1 / 3)
    assert sweep([]) == []


async def _judged(state, inbox, *, case_id: str, outcome: dict, message: str, field: str):
    ref = await inbox.write_signal(_signal(message, field=field))
    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            mandate="m",
            prompt="p",
            response="r",
            outcome_fields=outcome,
            tool_names=(),
            tool_results=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            cumulative_usage=TokenUsage(input_tokens=1, output_tokens=1),
            selected_next_action=None,
            case_id=case_id,
            root_correlation_id=case_id,
            task_id=f"task-{case_id}",
            triggered_by=RESIDENT_HOME_TRIGGER,
            persona="",
            evidence_refs=(),
            inbox_refs=(ref,),
        )
    )
    await inbox.acknowledge(
        (ref,),
        status=ResidentInboxStatus.REMEMBERED.value,
        reason=f"recorded by resident case {case_id} turn 1",
    )


@pytest.mark.asyncio
async def test_calibration_replays_each_judged_slot_against_its_turn(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")
    state = LocalResidentState(tmp_path / "state")
    decisions = _Decisions()
    await _judged(
        state,
        inbox,
        case_id="resident-home-a",
        outcome={
            "tier": "silent",
            "decision": "ignore",
            "working_state": {
                "objectives": [],
                "observations": ["probes flap"],
                "hypotheses": [],
                "unknowns": [],
                "capability_gaps": [],
                "attempts": [],
            },
        },
        message=f"{ROUTINE} probe",
        field="probe",
    )
    await _judged(
        state,
        inbox,
        case_id="resident-home-b",
        outcome={"tier": "urgent", "decision": "escalate"},
        message="etcd leader lost",
        field="etcd",
    )
    # A slot whose judging turn was pruned cannot be labelled.
    orphan = await inbox.write_signal(_signal("pruned", field="pruned"))
    await inbox.acknowledge((orphan,), reason="recorded by resident case gone turn 1")

    report = await calibrate(
        state=state,
        inbox=inbox,
        attention=_attention(decisions),
        mandate="m",
        limit=100,
    )

    assert report.judged_slots == 3
    assert report.unmatched_slots == 1
    assert report.replayed_slots == 2
    assert report.models == ["jev-1.14.0"]
    by_label = {row.turn_needed: row for row in report.rows}
    assert by_label[False].tier == "silent"
    assert by_label[True].tier == "urgent"
    # The second turn's slot is judged with the working state the first turn left.
    etcd_state = next(s for s, _p in decisions.calls if "etcd" in s["observation"])
    assert "probes flap" in etcd_state["working_state"]
    assert report.zero_miss_threshold == pytest.approx(by_label[False].skip_probability)


def test_calibrate_command_writes_the_report(tmp_path, monkeypatch) -> None:
    from ravn.cli import runtime_builders
    from ravn.cli.resident_commands import resident_app

    config = tmp_path / "ravn.yaml"
    config.write_text(f"state_dir: {tmp_path / 'state'}\n")
    monkeypatch.setattr(runtime_builders, "build_decision_port", lambda _settings: _Decisions())
    out = tmp_path / "report.json"

    result = CliRunner().invoke(
        resident_app, ["attention-calibrate", "--config", str(config), "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["judged_slots"] == 0
    assert "zero-miss threshold: none" in result.output

    monkeypatch.setattr(runtime_builders, "build_decision_port", lambda _settings: None)
    refused = CliRunner().invoke(
        resident_app, ["attention-calibrate", "--config", str(config), "--out", str(out)]
    )
    assert refused.exit_code == 1

    negative = CliRunner().invoke(
        resident_app,
        ["attention-calibrate", "--config", str(config), "--out", str(out), "--limit", "-1"],
    )
    assert negative.exit_code == 2
