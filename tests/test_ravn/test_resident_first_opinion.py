"""The observe-only first opinion: what it asks, and how it records agreement with the resident."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.cap_telemetry import cap_hit_counts
from ravn.config import InitiativeConfig
from ravn.domain.models import AgentTask, OutputMode
from ravn.domain.valkyrie_contracts import (
    VALKYRIE_ATTENTION_TIER_ORDER,
    VALKYRIE_DECISION_ORDER,
    resident_outcome_section,
)
from ravn.drive_loop import DriveLoop
from ravn.ports.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionResult,
    NoulAnswer,
)
from ravn.resident_first_opinion import (
    DECISION_CRITERIA,
    PURPOSE,
    TIER_CRITERIA,
    TIER_DELTA_BUCKETS,
    FirstOpinion,
    ResidentFirstOpinion,
    without_outcome_contract,
)
from tests.test_ravn.test_decision_observability import RecordingTelemetry


def _task(*, case_id: str = "case-1", context: str = "printer 3 reports nozzle 240C") -> AgentTask:
    return AgentTask(
        task_id="t1",
        title="Environment signal",
        initiative_context=context,
        triggered_by="resident:home",
        output_mode=OutputMode.SILENT,
        resident_case_id=case_id,
        resident_mandate="keep the workshop's printers healthy",
    )


def _choice(choice: str, options: tuple[str, ...], confidence: float = 0.8) -> ChoiceAnswer:
    rest = (1.0 - confidence) / (len(options) - 1)
    return ChoiceAnswer(
        choice=choice,
        confidence=confidence,
        probabilities={o: confidence if o == choice else rest for o in options},
    )


class _Decisions:
    def __init__(self, *, tier: str = "ambient", decision: str = "watch") -> None:
        self.tier = tier
        self.decision = decision
        self.calls: list[tuple[dict, dict, str]] = []

    async def decide(self, state, questions, *, purpose):
        self.calls.append((state, dict(questions), purpose))
        return DecisionResult(
            model="jev-1.14.0",
            answers={
                "tier": _choice(self.tier, VALKYRIE_ATTENTION_TIER_ORDER),
                "decision": _choice(self.decision, VALKYRIE_DECISION_ORDER),
                "needs_investigation": NoulAnswer(0.3),
            },
            input_tokens=50,
        )


@pytest.fixture
def telemetry(monkeypatch: pytest.MonkeyPatch) -> RecordingTelemetry:
    recorder = RecordingTelemetry()
    monkeypatch.setattr("ravn.resident_first_opinion.get_observability", lambda: recorder)
    return recorder


def test_descriptions_cover_exactly_the_contract_vocabulary() -> None:
    assert tuple(TIER_CRITERIA) == VALKYRIE_ATTENTION_TIER_ORDER
    assert tuple(DECISION_CRITERIA) == VALKYRIE_DECISION_ORDER


def test_refuses_a_non_positive_observation_bound() -> None:
    with pytest.raises(ValueError, match="positive"):
        ResidentFirstOpinion(_Decisions(), max_observation_chars=0)


def test_applies_only_to_resident_cases() -> None:
    assert ResidentFirstOpinion.applies_to(_task())
    assert not ResidentFirstOpinion.applies_to(_task(case_id=""))


class TestJudge:
    async def test_asks_tier_decision_and_investigation_in_one_request(self) -> None:
        decisions = _Decisions(tier="present", decision="investigate")
        opinion = await ResidentFirstOpinion(decisions, max_observation_chars=1000).judge(_task())

        [(state, questions, purpose)] = decisions.calls
        assert purpose == PURPOSE
        assert state == {
            "mandate": "keep the workshop's printers healthy",
            "trigger": "resident:home",
            "title": "Environment signal",
            "observation": "printer 3 reports nozzle 240C",
        }
        assert set(questions) == {"tier", "decision", "needs_investigation"}
        tier_question = questions["tier"]
        assert isinstance(tier_question, ChoiceQuestion)
        assert tuple(tier_question.options) == VALKYRIE_ATTENTION_TIER_ORDER
        assert opinion.case_id == "case-1"
        assert opinion.model == "jev-1.14.0"
        assert opinion.tier.choice == "present"
        assert opinion.decision.choice == "investigate"
        assert opinion.needs_investigation.probability == 0.3

    async def test_outcome_contract_is_not_sent_as_state(self) -> None:
        context = f"## Signal\n\nnozzle 240C\n\n{resident_outcome_section()}"
        decisions = _Decisions()
        await ResidentFirstOpinion(decisions, max_observation_chars=10_000).judge(
            _task(context=context)
        )

        observation = decisions.calls[0][0]["observation"]
        assert observation == "## Signal\n\nnozzle 240C"

    async def test_long_observation_is_cut_and_the_cut_reported(self) -> None:
        knob = "decision.resident_first_opinion.max_observation_chars"
        before = cap_hit_counts().get(knob, 0)
        decisions = _Decisions()
        await ResidentFirstOpinion(decisions, max_observation_chars=5).judge(
            _task(context="abcdefghij")
        )

        assert decisions.calls[0][0]["observation"] == "abcde"
        assert cap_hit_counts()[knob] == before + 1

    async def test_mistyped_answers_raise(self) -> None:
        class _Wrong(_Decisions):
            async def decide(self, state, questions, *, purpose):
                result = await super().decide(state, questions, purpose=purpose)
                return DecisionResult(
                    model=result.model,
                    answers={**result.answers, "tier": NoulAnswer(0.5)},
                    input_tokens=None,
                )

        with pytest.raises(TypeError, match="mistyped"):
            await ResidentFirstOpinion(_Wrong(), max_observation_chars=100).judge(_task())


class TestWithoutOutcomeContract:
    def test_keeps_sections_after_the_contract(self) -> None:
        text = "## Signal\n\nx\n\n## Required outcome\n\ntemplate\n\n## Charter\n\nbe kind"
        assert without_outcome_contract(text) == "## Signal\n\nx\n\n## Charter\n\nbe kind"

    def test_leaves_contexts_without_a_contract_alone(self) -> None:
        assert without_outcome_contract("just a message") == "just a message"


def _opinion(tier: str = "ambient", decision: str = "watch") -> FirstOpinion:
    return FirstOpinion(
        case_id="case-1",
        model="jev-1.14.0",
        tier=_choice(tier, VALKYRIE_ATTENTION_TIER_ORDER),
        decision=_choice(decision, VALKYRIE_DECISION_ORDER),
        needs_investigation=NoulAnswer(0.2),
    )


def _comparisons(telemetry: RecordingTelemetry) -> dict[str, dict]:
    return {
        attrs["ravn.decision.question"]: attrs
        for name, _, attrs in telemetry.counts
        if name == "ravn.decision.first_opinion.comparisons"
    }


class TestCompare:
    def test_records_agreement_and_disagreement(self, telemetry: RecordingTelemetry) -> None:
        first_opinion = ResidentFirstOpinion(_Decisions(), max_observation_chars=100)
        first_opinion.compare(
            _opinion(tier="ambient", decision="watch"),
            {"tier": "urgent", "decision": "watch", "confidence": 0.9},
            outcome_valid=True,
        )

        comparisons = _comparisons(telemetry)
        assert comparisons["tier"]["ravn.decision.first_opinion"] == "ambient"
        assert comparisons["tier"]["ravn.decision.resident"] == "urgent"
        assert comparisons["tier"]["ravn.decision.agreed"] is False
        assert comparisons["decision"]["ravn.decision.agreed"] is True
        assert comparisons["tier"]["ravn.valkyrie.outcome_valid"] is True
        [(name, delta, attrs)] = telemetry.records
        assert name == "ravn.decision.first_opinion.tier_delta"
        assert delta == -2  # ambient (1) minus urgent (3): the cheap model under-attended
        assert attrs["ravn.decision.resident"] == "urgent"
        assert telemetry.buckets[name] == TIER_DELTA_BUCKETS == (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5)
        assert telemetry.span_attributes == {
            "ravn.decision.first_opinion.tier": "ambient",
            "ravn.decision.first_opinion.decision": "watch",
            "ravn.decision.first_opinion.tier_agreed": "false",
            "ravn.decision.first_opinion.decision_agreed": "true",
        }
        [(event, attrs, content)] = telemetry.events
        assert event == "ravn.decision.first_opinion"
        assert attrs["ravn.resident.case_id"] == "case-1"
        assert content["resident"] == {"tier": "urgent", "decision": "watch", "confidence": 0.9}
        assert content["first_opinion"]["needs_investigation"] == 0.2

    def test_logs_one_line_per_comparison(
        self, telemetry: RecordingTelemetry, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ravn.resident_first_opinion"):
            ResidentFirstOpinion(_Decisions(), max_observation_chars=100).compare(
                _opinion(tier="ambient", decision="watch"),
                {"tier": "silent", "decision": "watch"},
                outcome_valid=True,
            )
            ResidentFirstOpinion(_Decisions(), max_observation_chars=100).compare(
                _opinion(), None, outcome_valid=False
            )

        assert [r.getMessage() for r in caplog.records] == [
            "first_opinion[case-1] model=jev-1.14.0 outcome_valid=True "
            "tier jev=ambient resident=silent agreed=false "
            "decision jev=watch resident=watch agreed=true",
            "first_opinion[case-1] model=jev-1.14.0 outcome_valid=False "
            "tier jev=ambient resident=unavailable agreed=unavailable "
            "decision jev=watch resident=unavailable agreed=unavailable",
        ]

    @pytest.mark.parametrize(
        "outcome",
        [None, {}, {"tier": "loud", "decision": 3}],
    )
    def test_missing_resident_judgment_is_unavailable_not_disagreement(
        self, telemetry: RecordingTelemetry, outcome: dict | None
    ) -> None:
        ResidentFirstOpinion(_Decisions(), max_observation_chars=100).compare(
            _opinion(), outcome, outcome_valid=False
        )

        comparisons = _comparisons(telemetry)
        assert telemetry.span_attributes["ravn.decision.first_opinion.tier_agreed"] == "unavailable"
        for question in ("tier", "decision"):
            assert comparisons[question]["ravn.decision.resident"] == "unavailable"
            assert "ravn.decision.agreed" not in comparisons[question]
        assert telemetry.records == []


def _drive_loop(order: list[str], *, fail_opinion: bool = False) -> DriveLoop:
    def _agent_factory(channel, task_id, persona, triggered_by):
        agent = AsyncMock()

        async def _run_turn(prompt):
            order.append("turn")
            return MagicMock(
                usage=MagicMock(input_tokens=0, output_tokens=0), cost_usd=0.0, response="ok"
            )

        agent.run_turn = AsyncMock(side_effect=_run_turn)
        return agent

    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    settings.mesh.enabled = False
    settings.budget.daily_cap_usd = 100.0
    settings.budget.warn_at_percent = 80
    settings.budget.pricing_source = "flat"
    settings.budget.pricing_overrides = {}
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    loop = DriveLoop(
        agent_factory=_agent_factory,
        config=InitiativeConfig(
            enabled=True, max_concurrent_tasks=1, queue_journal_path="/proc/no_such_dir/q.json"
        ),
        settings=settings,
    )

    class _Recording(ResidentFirstOpinion):
        async def judge(self, task):
            order.append("judge")
            if fail_opinion:
                raise RuntimeError("decision backend down")
            return _opinion()

        def compare(self, opinion, outcome, *, outcome_valid):
            order.append("compare")

    loop.set_first_opinion(_Recording(_Decisions(), max_observation_chars=100))
    return loop


class TestDriveLoopWiring:
    async def test_opinion_before_the_turn_and_comparison_after(self) -> None:
        order: list[str] = []
        await _drive_loop(order)._run_task(_task())

        assert order == ["judge", "turn", "compare"]

    async def test_non_resident_tasks_get_no_opinion(self) -> None:
        order: list[str] = []
        await _drive_loop(order)._run_task(_task(case_id=""))

        assert order == ["turn"]

    async def test_a_failed_opinion_fails_the_task_before_the_turn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        order: list[str] = []
        with caplog.at_level(logging.ERROR, logger="ravn.drive_loop"):
            await _drive_loop(order, fail_opinion=True)._run_task(_task())

        assert order == ["judge"]
        assert "RuntimeError: decision backend down" in caplog.text
