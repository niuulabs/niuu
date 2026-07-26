"""Tests for canonical outcome emission from DriveLoop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.outcome import OutcomeField
from ravn.domain.events import RavnEventType
from ravn.domain.models import TokenUsage, TurnResult
from ravn.domain.valkyrie_contracts import normalize_valkyrie_outcome
from ravn.drive_loop import (
    _build_resident_valkyrie_schema_repair_prompt,
    _build_workflow_outcome_repair_prompt,
    _default_success_verdict,
    _extract_mimir_dream_counts,
    _infer_tool_written_verdict,
    _known_verdict_tokens,
    _normalize_outcome_verdict,
    _parse_outcome_for_persona,
    _resident_valkyrie_validation_result,
    _validate_resident_continuation_contract,
)
from sleipnir.domain import registry
from tests.test_ravn.conftest import _make_agent_task, _make_drive_loop


def _valkyrie_judgment_produces() -> SimpleNamespace:
    return SimpleNamespace(
        event_type=registry.VALKYRIE_JUDGMENT_PROPOSED,
        event_type_map={"propose_action": registry.VALKYRIE_ACTION_PROPOSED},
        schema={
            "decision": OutcomeField(
                type="enum",
                description="decision",
                enum_values=["watch", "propose_action", "blocked"],
            ),
            "environment_id": OutcomeField(type="string", description="environment id"),
            "valkyrie_id": OutcomeField(type="string", description="valkyrie id"),
            "signal_refs": OutcomeField(type="array", description="signal refs"),
            "tier": OutcomeField(
                type="enum",
                description="attention tier",
                enum_values=["silent", "ambient", "present", "urgent"],
            ),
            "confidence": OutcomeField(type="number", description="confidence"),
            "operational_state": OutcomeField(type="string", description="operational state"),
            "rationale": OutcomeField(type="string", description="rationale"),
            "evidence": OutcomeField(type="array", description="evidence"),
            "recommended_action": OutcomeField(type="string", description="recommended action"),
            "action_authority": OutcomeField(
                type="enum",
                description="authority",
                enum_values=[
                    "autonomous",
                    "yolo_allowed",
                    "court_required",
                    "human_review_required",
                ],
            ),
            "target_surfaces": OutcomeField(type="array", description="target surfaces"),
            "expires_at": OutcomeField(type="string", description="expiry"),
            "dissent_refs": OutcomeField(type="array", description="dissent refs"),
            "correlation_ids": OutcomeField(type="object", description="correlation ids"),
        },
    )


def _valid_valkyrie_judgment_text(*, tier: str = "ambient") -> str:
    return f"""\
---outcome---
decision: propose_action
environment_id: cluster-a
valkyrie_id: k8s-valkyrie
signal_refs:
  - evt-k8s-1
tier: {tier}
confidence: 0.84
operational_state: investigating
rationale: pod restart signal needs inspection
evidence:
  - event_id: evt-k8s-1
    kind: kubernetes
recommended_action: inspect pod before changing cluster state
action_authority: autonomous
target_surfaces:
  - surface:ops
expires_at: ""
dissent_refs: []
correlation_ids:
  root: corr-k8s-1
  task: task-k8s-1
---end---
"""


class TestDriveLoopOutcomeContract:
    def test_default_success_verdict_prefers_event_map_then_schema(self) -> None:
        assert (
            _default_success_verdict(
                SimpleNamespace(event_type_map={"pass": "code.changed"}, schema={})
            )
            == "pass"
        )
        assert (
            _default_success_verdict(
                SimpleNamespace(
                    event_type_map={},
                    schema={"verdict": {"values": ["blocked", "complete"]}},
                )
            )
            == "complete"
        )
        assert (
            _default_success_verdict(
                SimpleNamespace(
                    event_type_map={},
                    schema={
                        "verdict": OutcomeField(
                            type="enum",
                            description="workflow verdict",
                            enum_values=["blocked", "approved"],
                        )
                    },
                )
            )
            == "approved"
        )

    def test_known_verdict_tokens_and_normalization_cover_wrapped_tokens(self) -> None:
        produces = SimpleNamespace(
            event_type_map={"needs_changes": "review.changes_requested"},
            schema={"verdict": {"values": ["pass", "needs_changes", "blocked"]}},
        )
        assert _known_verdict_tokens(produces) == {"pass", "needs_changes", "blocked"}
        assert _normalize_outcome_verdict("needs changes", produces) == "needs_changes"
        assert _normalize_outcome_verdict("needs\nchanges", produces) == "needs_changes"
        assert _normalize_outcome_verdict("unknown", produces) == "unknown"
        assert _normalize_outcome_verdict("", produces) == ""

    def test_infer_tool_written_verdict_only_when_one_allowed_alias_matches(self) -> None:
        event_type_map = {
            "opinion_submitted": "council.a.opinion.submitted",
            "blocked": "council.a.blocked",
        }
        assert (
            _infer_tool_written_verdict(
                allowed_topics={"council.a.opinion.submitted"},
                event_type_map=event_type_map,
            )
            == "opinion_submitted"
        )
        assert (
            _infer_tool_written_verdict(
                allowed_topics={"council.a.opinion.submitted", "council.a.blocked"},
                event_type_map=event_type_map,
            )
            == ""
        )
        assert _infer_tool_written_verdict(allowed_topics=None, event_type_map=event_type_map) == ""

    def test_parse_outcome_for_persona_uses_schema_and_falls_back_on_parser_error(self) -> None:
        persona = SimpleNamespace(
            produces=SimpleNamespace(
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="verdict",
                        enum_values=["pass"],
                    )
                }
            )
        )
        parsed = _parse_outcome_for_persona(
            "---outcome---\nverdict: pass\n---end---",
            persona,
        )
        assert parsed is not None
        assert parsed.fields["verdict"] == "pass"

        with pytest.MonkeyPatch.context() as mp:
            calls: list[tuple[object, object | None]] = []

            def _fake_parse(text: str, schema=None):
                calls.append((text, schema))
                if schema is not None:
                    raise RuntimeError("bad schema")
                return SimpleNamespace(fields={"summary": "fallback"}, valid=True)

            mp.setattr("ravn.drive_loop.parse_outcome_block", _fake_parse)
            recovered = _parse_outcome_for_persona("plain text", persona)

        assert recovered is not None
        assert recovered.fields["summary"] == "fallback"
        assert calls[0][1] is not None
        assert calls[1][1] is None

    @pytest.mark.asyncio
    async def test_repair_resident_valkyrie_outcome_retries_with_strict_schema(self) -> None:
        dl = _make_drive_loop()
        dl._persona_config = SimpleNamespace(
            name="k8s-valkyrie",
            produces=_valkyrie_judgment_produces(),
        )
        task = _make_agent_task(task_id="task-valkyrie-repair")
        task.initiative_context = (
            "Environment: cluster-a (k8s)\n"
            "Resident peer id: k8s-valkyrie\n"
            "Signal type: kubernetes\n"
        )

        class RepairAgent:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            async def run_turn(self, prompt: str) -> TurnResult:
                self.prompts.append(prompt)
                return TurnResult(
                    response=_valid_valkyrie_judgment_text(),
                    tool_calls=[],
                    tool_results=[],
                    usage=TokenUsage(input_tokens=8, output_tokens=12),
                )

        agent = RepairAgent()
        repair_result = await dl._maybe_repair_resident_valkyrie_outcome(
            agent=agent,
            task=task,
            response_text="Endpoints ticked off queue interCAL question",
        )

        assert repair_result is not None
        assert agent.prompts
        assert "Do not call tools" in agent.prompts[0]
        assert "resident Valkyrie outcome block is missing" in agent.prompts[0]
        _, _, validation_errors = _resident_valkyrie_validation_result(
            repair_result.response,
            dl._persona_config,
        )
        assert validation_errors == []

    @pytest.mark.asyncio
    async def test_repair_unroutable_workflow_outcome_reuses_same_agent(self) -> None:
        dl = _make_drive_loop()
        dl._persona_config = SimpleNamespace(
            name="specification-framer",
            produces=SimpleNamespace(
                event_type="spec.frame.completed",
                event_type_map={
                    "framed": "spec.framed",
                    "blocked": "spec.blocked",
                },
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"spec.framed"})
        task = _make_agent_task(task_id="task-workflow-repair")
        task.workflow_node_id = "capability-frame"

        class RepairAgent:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            async def run_turn(self, prompt: str) -> TurnResult:
                self.prompts.append(prompt)
                return TurnResult(
                    response=(
                        "---outcome---\nverdict: framed\nsummary: brief is ready\n---end---\n"
                    ),
                    tool_calls=[],
                    tool_results=[],
                    usage=TokenUsage(input_tokens=8, output_tokens=12),
                )

        agent = RepairAgent()
        repair_result = await dl._maybe_repair_unroutable_workflow_outcome(
            agent=agent,
            task=task,
            response_text=(
                "---outcome---\n"
                "verdict: blocked\n"
                "summary: artifact store was temporarily unavailable\n"
                "---end---\n"
            ),
        )

        assert repair_result is not None
        assert repair_result.response.startswith("---outcome---\nverdict: framed")
        assert agent.prompts
        assert 'Routable event types: ["spec.framed"]' in agent.prompts[0]
        assert "verdict: help_needed" in agent.prompts[0]

    @pytest.mark.asyncio
    async def test_does_not_repair_routable_workflow_outcome(self) -> None:
        dl = _make_drive_loop()
        dl._persona_config = SimpleNamespace(
            name="specification-framer",
            produces=SimpleNamespace(
                event_type="spec.frame.completed",
                event_type_map={"framed": "spec.framed"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"spec.framed"})
        task = _make_agent_task(task_id="task-workflow-routable")
        task.workflow_node_id = "capability-frame"
        agent = AsyncMock()

        result = await dl._maybe_repair_unroutable_workflow_outcome(
            agent=agent,
            task=task,
            response_text=("---outcome---\nverdict: framed\nsummary: brief is ready\n---end---\n"),
        )

        assert result is None
        agent.run_turn.assert_not_awaited()

    def test_workflow_outcome_repair_prompt_preserves_original_evidence(self) -> None:
        task = _make_agent_task(task_id="task-workflow-prompt")
        task.workflow_node_id = "capability-frame"

        prompt = _build_workflow_outcome_repair_prompt(
            task=task,
            original_response="verdict: blocked\nreason: transient failure",
            allowed_topics={"spec.framed"},
        )

        assert "capability-frame" in prompt
        assert "transient failure" in prompt
        assert "Do not claim success without evidence" in prompt

    def test_resident_control_fields_do_not_depend_on_an_episode(self) -> None:
        dl = _make_drive_loop()
        dl._persona_config = SimpleNamespace(
            name="k8s-valkyrie",
            produces=_valkyrie_judgment_produces(),
        )
        dl._settings = SimpleNamespace(
            environment=SimpleNamespace(id="cluster-a", type="k8s"),
            mesh=SimpleNamespace(own_peer_id="k8s-valkyrie"),
        )
        result = TurnResult(
            response=_valid_valkyrie_judgment_text(),
            tool_calls=[],
            tool_results=[],
            usage=TokenUsage(input_tokens=8, output_tokens=12),
        )

        fields, valid = dl._decorate_turn_result_outcome(
            _make_agent_task(task_id="task-no-episode"),
            result,
            result.response,
        )

        assert result.episode is None
        assert valid is True
        assert fields["decision"] == "propose_action"
        assert fields["environment_id"] == "cluster-a"

    def test_schema_repair_prompt_includes_required_resident_contract_shape(self) -> None:
        task = _make_agent_task(task_id="task-valkyrie-repair-prompt")
        prompt = _build_resident_valkyrie_schema_repair_prompt(
            task=task,
            original_response="bad",
            validation_errors=["resident Valkyrie outcome block is missing"],
            outcome_fields={},
        )

        assert "decision: ignore | watch | investigate" in prompt
        assert "confidence: <number from 0.0 to 1.0>" in prompt
        assert "action_authority: autonomous | yolo_allowed" in prompt
        assert "only a real event/time/operator answer wakes a turn" in prompt
        assert "working_state` must be a mapping" in prompt
        assert "empty list as `field: []`" in prompt
        assert "at most five entries per list" in prompt

        working_state_prompt = _build_resident_valkyrie_schema_repair_prompt(
            task=task,
            original_response="partial state",
            validation_errors=["working_state.hypotheses must be a list"],
            outcome_fields={"working_state": {"observations": ["signal-a"]}},
        )
        assert (
            "working_state:\n  objectives: []\n  observations: []\n  hypotheses: []"
            in working_state_prompt
        )
        assert "preserve valid prior entries" in working_state_prompt

    def test_free_text_continue_is_not_a_supported_wake_source(self) -> None:
        unsupported = (
            "continuation 'continue' is unsupported; call available tools before the final "
            "outcome, or use sleep/ask_operator for a real wake source"
        )
        assert _validate_resident_continuation_contract(
            {
                "continuation": "continue",
                "selected_next_action": "continue",
                "next_action_timing": "immediate",
            }
        ) == [unsupported]
        assert (
            _validate_resident_continuation_contract(
                {
                    "continuation": "sleep",
                    "selected_next_action": "continue",
                    "next_action_timing": "external_event",
                }
            )
            == []
        )
        assert _validate_resident_continuation_contract(
            {
                "continuation": "continue",
                "selected_next_action": "inspect the source",
                "next_action_timing": "external_event",
            }
        ) == [unsupported]
        assert _validate_resident_continuation_contract(
            {
                "continuation": "continue",
                "selected_next_action": "inspect the source",
            }
        ) == [unsupported]
        assert _validate_resident_continuation_contract(
            {
                "selected_next_action": "inspect the source",
                "next_action_timing": "immediate",
            }
        ) == ["selected_next_action requires explicit continuation control"]

    @pytest.mark.asyncio
    async def test_canonical_and_alias_outcomes_are_split(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        skuld_channel = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="reviewer",
            produces=SimpleNamespace(
                event_type="review.completed",
                event_type_map={
                    "pass": "review.passed",
                    "needs_changes": "review.changes_requested",
                },
            ),
        )

        task = _make_agent_task(task_id="task-123")
        task.session_id = "sess-123"
        task.root_correlation_id = "root-123"
        task.workflow_parent_event_id = "code-task-123"
        response_text = """\
---outcome---
verdict: needs_changes
summary: tighten the edge-case handling
comments: fix the null branch
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        assert canonical_topic == "review.completed"
        assert canonical_event.payload["event_type"] == "review.completed"
        assert canonical_event.payload["bubble_up"] is True
        assert canonical_event.payload["collaboration_routing_only"] is True
        assert canonical_event.payload["verdict"] == "needs_changes"
        assert canonical_event.payload["workflow_parent_event_id"] == "code-task-123"

        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]
        assert alias_topic == "review.changes_requested"
        assert alias_event.payload["event_type"] == "review.changes_requested"
        assert alias_event.payload["canonical_event_type"] == "review.completed"
        assert alias_event.payload["routing_only"] is True
        assert alias_event.payload["bubble_up"] is False
        assert "collaboration_routing_only" not in alias_event.payload

        assert skuld_channel.emit.await_count == 2
        emitted = skuld_channel.emit.await_args_list[0].args[0]
        assert emitted.payload["event_type"] == "review.completed"
        assert emitted.payload["bubble_up"] is True
        alias_emitted = skuld_channel.emit.await_args_list[1].args[0]
        assert alias_emitted.payload["event_type"] == "review.changes_requested"
        assert alias_emitted.payload["routing_only"] is True
        assert alias_emitted.payload["bubble_up"] is False

    @pytest.mark.asyncio
    async def test_canonical_outcome_uses_mesh_bridge_when_skuld_absent(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="coder",
            produces=SimpleNamespace(
                event_type="code.completed",
                event_type_map={"pass": "code.changed", "blocked": "code.blocked"},
            ),
        )

        task = _make_agent_task(task_id="task-456")
        task.session_id = "sess-456"
        response_text = """\
---outcome---
verdict: pass
summary: implemented the fix
files_changed: 2
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2
        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        assert canonical_topic == "code.completed"
        assert canonical_event.payload["event_type"] == "code.completed"
        assert canonical_event.payload["bubble_up"] is True
        assert "collaboration_routing_only" not in canonical_event.payload

        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]
        assert alias_topic == "code.changed"
        assert alias_event.payload["event_type"] == "code.changed"
        assert alias_event.payload["canonical_event_type"] == "code.completed"
        assert alias_event.payload["routing_only"] is True

    @pytest.mark.asyncio
    async def test_valid_resident_valkyrie_judgment_preserves_structured_metadata(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        published = []
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._sleipnir_publisher = SimpleNamespace(publish=AsyncMock(side_effect=published.append))
        dl._source_id = "drive_loop"
        dl._settings.environment.id = "cluster-runtime"
        dl._settings.environment.type = "k8s"
        dl._settings.mesh.own_peer_id = "runtime-valkyrie"
        dl._persona_config = SimpleNamespace(
            name="k8s-valkyrie",
            produces=_valkyrie_judgment_produces(),
        )

        task = _make_agent_task(task_id="task-k8s-1")
        task.session_id = "sess-k8s-1"
        task.root_correlation_id = "corr-runtime"
        task.workflow_parent_event_id = "evt-runtime"

        await dl._emit_mesh_outcome_event(
            task,
            _valid_valkyrie_judgment_text(),
            success=True,
        )

        assert mesh.publish.await_count == 2
        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        assert canonical_topic == registry.VALKYRIE_JUDGMENT_PROPOSED
        assert canonical_event.payload["valid"] is True
        assert canonical_event.payload["outcome"]["tier"] == "ambient"
        assert canonical_event.payload["outcome"]["signal_refs"] == ["evt-k8s-1"]
        assert canonical_event.payload["outcome"]["evidence"] == [
            {"event_id": "evt-k8s-1", "kind": "kubernetes"}
        ]
        assert canonical_event.payload["outcome"]["target_surfaces"] == ["surface:ops"]
        outcome = canonical_event.payload["outcome"]
        assert outcome["environment_id"] == "cluster-runtime"
        assert outcome["environment_type"] == "k8s"
        assert outcome["valkyrie_id"] == "runtime-valkyrie"
        assert outcome["correlation_ids"] == {
            "root": "corr-runtime",
            "task": "task-k8s-1",
            "environment": "cluster-runtime",
            "signal": "evt-runtime",
        }

        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]
        alias_event = mesh.publish.await_args_list[1].args[0]
        assert alias_topic == registry.VALKYRIE_ACTION_PROPOSED
        assert alias_event.payload["canonical_event_type"] == registry.VALKYRIE_JUDGMENT_PROPOSED
        assert alias_event.payload["routing_only"] is True
        assert [event.event_type for event in published] == [registry.VALKYRIE_JUDGMENT_PROPOSED]
        assert published[0].payload["task_id"] == "task-k8s-1"
        assert published[0].payload["environment_id"] == "cluster-runtime"
        assert published[0].payload["valid"] is True

    def test_runtime_identity_overrides_model_environment_claim(self) -> None:
        produces = _valkyrie_judgment_produces()
        produces.schema["environment_type"] = OutcomeField(
            type="string",
            description="runtime environment type",
        )
        response = _valid_valkyrie_judgment_text().replace(
            "environment_id: cluster-a",
            "environment_id: claimed-environment\nenvironment_type: k8s",
        )

        _, fields, errors = _resident_valkyrie_validation_result(
            response,
            SimpleNamespace(produces=produces),
            authoritative_fields={
                "environment_id": "workshop",
                "environment_type": "workshop",
                "valkyrie_id": "ivaldi-local",
                "correlation_ids": {
                    "root": "runtime-root",
                    "task": "runtime-task",
                    "environment": "workshop",
                },
            },
        )

        assert errors == []
        assert fields["environment_id"] == "workshop"
        assert fields["environment_type"] == "workshop"
        assert fields["valkyrie_id"] == "ivaldi-local"
        assert fields["correlation_ids"]["root"] == "runtime-root"

    def test_optional_null_outcome_field_is_omitted_without_repair(self) -> None:
        produces = _valkyrie_judgment_produces()
        produces.schema["question"] = OutcomeField(
            type="string",
            description="operator question",
            required=False,
        )
        response = _valid_valkyrie_judgment_text().replace(
            "---end---",
            "question: null\n---end---",
        )

        _, fields, errors = _resident_valkyrie_validation_result(
            response,
            SimpleNamespace(produces=produces),
        )

        assert errors == []
        assert "question" not in fields

    @pytest.mark.asyncio
    async def test_resident_valkyrie_judgment_normalizes_local_model_yaml_drift(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="k8s-valkyrie",
            produces=_valkyrie_judgment_produces(),
        )

        task = _make_agent_task(task_id="task-k8s-drift")
        task.session_id = "sess-k8s-drift"

        await dl._emit_mesh_outcome_event(
            task,
            """\
---outcome---
decision: propose_action
environment_id: cluster-a
valkyrie_id: k8s-valkyrie
signal_refs: evt-k8s-1
tier: ambient
confidence: "0.84"
operational_state: investigating
wakefulness: wakeful
rationale: pod restart signal needs inspection
evidence:
  event_id: evt-k8s-1
  kind: kubernetes
recommended_action: inspect pod before changing cluster state
action_authority: yolo
target_surfaces: surface:ops
expires_at: 2026-06-04T20:30:00Z
dissent_refs: null
correlation_ids:
  root: corr-k8s-1
  task: task-k8s-1
---end---
""",
            success=True,
        )

        canonical_event = mesh.publish.await_args_list[0].args[0]
        outcome = canonical_event.payload["outcome"]
        assert canonical_event.payload["valid"] is True
        assert outcome["signal_refs"] == ["evt-k8s-1"]
        assert outcome["confidence"] == 0.84
        assert outcome["wakefulness"] == "wakeful"
        assert outcome["action_authority"] == "yolo_allowed"
        assert outcome["evidence"] == [{"event_id": "evt-k8s-1", "kind": "kubernetes"}]
        assert outcome["target_surfaces"] == ["surface:ops"]
        assert outcome["dissent_refs"] == []
        assert outcome["expires_at"] == "2026-06-04T20:30:00Z"
        assert outcome["state_summary"].startswith("investigating:")
        json.dumps(canonical_event.payload)

    def test_resident_valkyrie_normalization_makes_yaml_datetime_wire_safe(self) -> None:
        outcome = normalize_valkyrie_outcome(
            registry.VALKYRIE_JUDGMENT_PROPOSED,
            {
                "environment_id": "cluster-a",
                "valkyrie_id": "k8s-valkyrie",
                "signal_refs": ["evt-k8s-1"],
                "tier": "ambient",
                "confidence": 0.84,
                "operational_state": "investigating",
                "rationale": "pod restart signal needs inspection",
                "evidence": [{"event_id": "evt-k8s-1"}],
                "recommended_action": "inspect pod",
                "action_authority": "autonomous",
                "target_surfaces": ["surface:ops"],
                "expires_at": datetime(2026, 6, 4, 20, 30, tzinfo=UTC),
                "dissent_refs": [],
                "correlation_ids": {"root": "corr-k8s-1"},
            },
        )

        assert outcome["expires_at"] == "2026-06-04T20:30:00+00:00"
        json.dumps(outcome)

    def test_resident_valkyrie_normalization_accepts_common_state_aliases(self) -> None:
        outcome = normalize_valkyrie_outcome(
            registry.VALKYRIE_JUDGMENT_PROPOSED,
            {
                "operational_state": "investigate",
                "wakefulness": "watchful",
            },
        )

        assert outcome["operational_state"] == "investigating"
        assert outcome["wakefulness"] == "watching"

    def test_resident_valkyrie_normalization_coerces_loose_correlation_ids(self) -> None:
        list_outcome = normalize_valkyrie_outcome(
            registry.VALKYRIE_JUDGMENT_PROPOSED,
            {"correlation_ids": ["pod/skuld-a", "event/backoff"]},
        )
        scalar_outcome = normalize_valkyrie_outcome(
            registry.VALKYRIE_JUDGMENT_PROPOSED,
            {"correlation_ids": "pod/skuld-a"},
        )
        empty_outcome = normalize_valkyrie_outcome(
            registry.VALKYRIE_JUDGMENT_PROPOSED,
            {"correlation_ids": ""},
        )

        assert list_outcome["correlation_ids"] == {"refs": ["pod/skuld-a", "event/backoff"]}
        assert scalar_outcome["correlation_ids"] == {"root": "pod/skuld-a"}
        assert empty_outcome["correlation_ids"] == {}

    @pytest.mark.asyncio
    async def test_invalid_resident_valkyrie_judgment_is_rejected_before_publication(
        self,
    ) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        skuld_channel = AsyncMock()
        published = []
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._sleipnir_publisher = SimpleNamespace(publish=AsyncMock(side_effect=published.append))
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="k8s-valkyrie",
            produces=_valkyrie_judgment_produces(),
        )

        task = _make_agent_task(task_id="task-k8s-invalid")
        task.session_id = "sess-k8s-invalid"

        await dl._emit_mesh_outcome_event(
            task,
            _valid_valkyrie_judgment_text(tier="watch"),
            success=True,
        )

        mesh.publish.assert_awaited_once()
        rejected_event = mesh.publish.await_args.args[0]
        rejected_topic = mesh.publish.await_args.kwargs["topic"]
        assert rejected_topic == registry.VALKYRIE_JUDGMENT_REJECTED
        assert rejected_event.payload["event_type"] == registry.VALKYRIE_JUDGMENT_REJECTED
        assert rejected_event.payload["canonical_event_type"] == registry.VALKYRIE_JUDGMENT_PROPOSED
        assert rejected_event.payload["valid"] is False
        assert any("tier" in error for error in rejected_event.payload["errors"])
        json.dumps(rejected_event.payload)
        skuld_channel.emit.assert_awaited_once()
        assert [event.event_type for event in published] == [registry.VALKYRIE_JUDGMENT_REJECTED]
        assert published[0].payload["task_id"] == "task-k8s-invalid"
        assert published[0].payload["canonical_event_type"] == registry.VALKYRIE_JUDGMENT_PROPOSED
        assert published[0].payload["valid"] is False

    @pytest.mark.asyncio
    async def test_task_lifecycle_publishes_sleipnir_started_completed_and_dropped(
        self,
    ) -> None:
        dl = _make_drive_loop(queue_max=1)
        published = []
        dl._sleipnir_publisher = SimpleNamespace(publish=AsyncMock(side_effect=published.append))
        dl._source_id = "drive_loop"

        task = _make_agent_task(task_id="task-observed")
        task.title = "observe cluster event"
        task.triggered_by = "signal:signal.kubernetes.event"
        task.root_correlation_id = "root-observed"

        await dl._emit_sleipnir_task_started(task)
        await dl._emit_sleipnir_task_completed(task, "success", response_text="")
        await dl.enqueue(_make_agent_task(task_id="task-kept"))
        await dl.enqueue(_make_agent_task(task_id="task-dropped"))

        assert [event.event_type for event in published] == [
            "ravn.task.started",
            registry.RAVN_TASK_COMPLETED,
            "ravn.task.dropped",
        ]
        assert published[0].payload["task_id"] == "task-observed"
        assert published[0].payload["triggered_by"] == "signal:signal.kubernetes.event"
        assert published[1].payload["task_id"] == "task-observed"
        assert published[1].payload["root_correlation_id"] == "root-observed"
        assert published[2].payload["task_id"] == "task-dropped"
        assert published[2].payload["reason"] == "queue_full"

    @pytest.mark.asyncio
    async def test_success_without_verdict_still_routes_pass_alias(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="coder",
            produces=SimpleNamespace(
                event_type="code.completed",
                event_type_map={"pass": "code.changed", "blocked": "code.blocked"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"code.changed"})

        task = _make_agent_task(task_id="task-no-verdict")
        task.session_id = "sess-no-verdict"
        task.workflow_node_id = "run-coder"
        response_text = "Implemented and pushed the requested proof artifact."

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_event.payload["verdict"] == "pass"
        assert canonical_event.payload["valid"] is True
        assert canonical_event.payload["event_type"] == "code.completed"
        assert alias_topic == "code.changed"
        assert alias_event.payload["event_type"] == "code.changed"
        assert alias_event.payload["canonical_event_type"] == "code.completed"

    @pytest.mark.asyncio
    async def test_success_without_verdict_uses_successful_schema_verdict(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="mimir-memory-curator",
            produces=SimpleNamespace(
                event_type="mimir.curated",
                event_type_map={"blocked": "mimir.curation_blocked"},
                schema={"verdict": {"values": ["complete", "blocked"]}},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"mimir.curated"})

        task = _make_agent_task(task_id="task-curation")
        task.session_id = "sess-curation"
        task.workflow_node_id = "run-memory-curator"
        response_text = "Curated the ingested memory source into canonical wiki knowledge."

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]

        assert canonical_topic == "mimir.curated"
        assert canonical_event.payload["event_type"] == "mimir.curated"
        assert canonical_event.payload["verdict"] == "complete"
        assert canonical_event.payload["valid"] is True

    @pytest.mark.asyncio
    async def test_success_without_verdict_uses_outcome_field_enum_values(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="mimir-memory-curator",
            produces=SimpleNamespace(
                event_type="mimir.curated",
                event_type_map={"blocked": "mimir.curation_blocked"},
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="whether curation succeeded",
                        enum_values=["complete", "blocked"],
                    )
                },
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"mimir.curated"})

        task = _make_agent_task(task_id="task-curation-field-schema")
        task.session_id = "sess-curation-field-schema"
        task.workflow_node_id = "run-memory-curator"
        response_text = "Curated the ingested memory source into canonical wiki knowledge."

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]

        assert canonical_topic == "mimir.curated"
        assert canonical_event.payload["event_type"] == "mimir.curated"
        assert canonical_event.payload["verdict"] == "complete"
        assert canonical_event.payload["valid"] is True

    @pytest.mark.asyncio
    async def test_soft_wrapped_outcome_block_still_routes_alias_when_workflow_only_allows_alias(
        self,
    ) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-wrapped-verdict")
        task.session_id = "sess-wrapped-verdict"
        task.workflow_node_id = "member-b-opinion"
        response_text = """\
---outcome---
verdict: opinion_submitted
summary
: Wrote an
 evidence-driven memo recommending
 SQLite by default.
page_path: council
/example
/opinion-b
.md
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_topic == "council.member.turn.completed"
        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert (
            canonical_event.payload["summary"]
            == "Wrote an evidence-driven memo recommending SQLite by default."
        )
        assert canonical_event.payload["fields"]["page_path"] == "council/example/opinion-b.md"
        assert alias_topic == "council.b.opinion.submitted"
        assert alias_event.payload["event_type"] == "council.b.opinion.submitted"

    @pytest.mark.asyncio
    async def test_page_write_fallback_routes_alias_when_outcome_block_is_missing(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-page-write-fallback")
        task.session_id = "sess-page-write-fallback"
        task.workflow_node_id = "member-b-opinion"
        task.tool_outcomes["mimir.page.written"] = {
            "page_path": "council/example/opinions/opinion-b.md",
            "mount_name": "council-scratch-board",
        }

        await dl._emit_mesh_outcome_event(
            task,
            "SQLite offers a simpler local-first default for this setup.",
            success=True,
        )

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_topic == "council.member.turn.completed"
        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert canonical_event.payload["summary"] == "Wrote council/example/opinions/opinion-b.md"
        assert (
            canonical_event.payload["fields"]["page_path"]
            == "council/example/opinions/opinion-b.md"
        )
        assert canonical_event.payload["valid"] is True
        assert alias_topic == "council.b.opinion.submitted"
        assert alias_event.payload["event_type"] == "council.b.opinion.submitted"

    @pytest.mark.asyncio
    async def test_workflow_mimir_artifacts_are_materialized_into_workspace(
        self,
        tmp_path,
    ) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._settings.permission.workspace_root = str(tmp_path)
        dl._mimir = AsyncMock()
        dl._mimir.get_page = AsyncMock(return_value=SimpleNamespace(content="# Brief\n\nReady."))
        dl._persona_config = SimpleNamespace(
            name="research-framer",
            produces=SimpleNamespace(
                event_type="research.framed",
                event_type_map={"framed": "research.framed"},
            ),
        )

        task = _make_agent_task(task_id="task-research-artifact")
        task.session_id = "sess-research-artifact"
        task.workflow_node_id = "research-framer"
        response_text = """\
---outcome---
verdict: framed
summary: Research brief framed.
brief_path: research/campaigns/example/brief.md
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        materialized = tmp_path / "research" / "campaigns" / "example" / "brief.md"
        assert materialized.read_text(encoding="utf-8") == "# Brief\n\nReady."
        dl._mimir.get_page.assert_awaited_once_with("research/campaigns/example/brief.md")
        event = mesh.publish.await_args_list[0].args[0]
        assert event.payload["fields"]["workspace_paths"] == ["research/campaigns/example/brief.md"]

    @pytest.mark.asyncio
    async def test_workflow_artifact_publish_failure_blocks_routing_alias(
        self,
        tmp_path,
    ) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        skuld_channel = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._settings.permission.workspace_root = str(tmp_path)
        dl._mimir = AsyncMock()
        dl._mimir.upsert_page.side_effect = RuntimeError("unauthorized")
        artifact = tmp_path / "research" / "campaigns" / "example" / "brief.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# Brief\n\nReady.", encoding="utf-8")
        dl._persona_config = SimpleNamespace(
            name="research-framer",
            produces=SimpleNamespace(
                event_type="research.frame.completed",
                event_type_map={"framed": "research.framed"},
            ),
        )

        task = _make_agent_task(task_id="task-missing-artifact")
        task.workflow_node_id = "research-framer"
        response_text = """\
---outcome---
verdict: framed
summary: Research brief framed.
brief_path: research/campaigns/example/brief.md
---end---
"""

        accepted = await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert accepted is False
        mesh.publish.assert_not_awaited()
        canonical = skuld_channel.emit.await_args.args[0]
        assert canonical.payload["event_type"] == "research.frame.completed"
        assert canonical.payload["success"] is False
        assert canonical.payload["valid"] is False
        assert "unauthorized" in canonical.payload["artifact_publish_error"]

    @pytest.mark.asyncio
    async def test_split_outcome_markers_still_route_alias_for_wrapped_codex_output(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-split-outcome-markers")
        task.session_id = "sess-split-outcome-markers"
        task.workflow_node_id = "member-b-opinion"
        response_text = """\
---
out
come
---

ver
dict
:
 opinion
_sub
mitted

summary
:
 W
rote
 an
 evidence
-driven
 opinion
 recommending
 SQLite
.

page
_path
:
 council
/example
/opinion-b
.md

---
end
---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2
        canonical_event = mesh.publish.await_args_list[0].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert canonical_event.payload["valid"] is True
        assert canonical_event.payload["fields"]["page_path"] == "council/example/opinion-b.md"
        assert alias_topic == "council.b.opinion.submitted"

    @pytest.mark.asyncio
    async def test_soft_wrapped_scalar_before_next_key_still_routes_alias(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="workflow verdict",
                        enum_values=["opinion_submitted"],
                    ),
                    "summary": OutcomeField(type="string", description="summary"),
                    "page_path": OutcomeField(type="string", description="page path"),
                },
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-soft-wrapped-scalar-before-key")
        task.session_id = "sess-soft-wrapped-scalar-before-key"
        task.workflow_node_id = "member-b-opinion"
        response_text = """\
---outcome---
ver
dict: opinion_sub
mitted
summary:
 Recommended lightweight human approval by
 default for final council publication
, with autonomous scratch
 work and risk-based graduation
 conditions.
page_path: council
/niu-906
-human-approval-gate
/opinions/opinion
-b.md
---
end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2
        canonical_event = mesh.publish.await_args_list[0].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert canonical_event.payload["valid"] is True
        assert (
            canonical_event.payload["fields"]["page_path"]
            == "council/niu-906-human-approval-gate/opinions/opinion-b.md"
        )
        assert alias_topic == "council.b.opinion.submitted"

    @pytest.mark.asyncio
    async def test_suppresses_outcome_when_workflow_node_disallows_it(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = AsyncMock()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="coordinator",
            produces=SimpleNamespace(event_type="ravn.task.completed", event_type_map={}),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"code.requested"})

        task = _make_agent_task(task_id="task-789")
        task.session_id = "sess-789"
        task.root_correlation_id = "root-789"
        task.workflow_node_id = "run-coordinator-start"
        response_text = """\
---outcome---
verdict: approve
summary: task completed
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        mesh.publish.assert_not_awaited()
        dl._skuld_channel.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_merges_tool_metadata_into_canonical_outcome(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = AsyncMock()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="postmortem-analyst",
            produces=SimpleNamespace(event_type="mimir.source.ingested", event_type_map={}),
        )

        task = _make_agent_task(task_id="task-mimir-123")
        task.session_id = "sess-mimir-123"
        task.root_correlation_id = "root-mimir-123"
        dl.record_tool_outcome_fields(
            task=task,
            event_type="mimir.source.ingested",
            fields={
                "source_id": "src_123",
                "mount_name": "tmp-mimir-test",
                "mount_names": ["tmp-mimir-test"],
            },
        )
        response_text = """\
---outcome---
verdict: complete
source_title: NIU-907 postmortem
summary: post-mortem source captured
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        assert canonical_event.payload["event_type"] == "mimir.source.ingested"
        assert canonical_event.payload["fields"]["source_id"] == "src_123"
        assert canonical_event.payload["fields"]["mount_name"] == "tmp-mimir-test"
        assert canonical_event.payload["fields"]["mount_names"] == ["tmp-mimir-test"]
        assert canonical_event.payload["fields"]["source_title"] == "NIU-907 postmortem"

    @pytest.mark.asyncio
    async def test_infers_mimir_mount_from_runtime_when_tool_metadata_missing(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = AsyncMock()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="postmortem-analyst",
            produces=SimpleNamespace(event_type="mimir.source.ingested", event_type_map={}),
        )
        dl._settings.mimir.instances = [
            SimpleNamespace(name="tmp-mimir-test"),
        ]
        dl._settings.mimir.write_routing.default = []

        task = _make_agent_task(task_id="task-mimir-default-123")
        task.session_id = "sess-mimir-default-123"
        response_text = """\
---outcome---
verdict: complete
source_id: src_456
source_title: NIU-909 postmortem
summary: post-mortem source captured
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        assert canonical_event.payload["fields"]["mount_name"] == "tmp-mimir-test"
        assert canonical_event.payload["fields"]["mount_names"] == ["tmp-mimir-test"]

    def test_resolve_task_context_and_directed_message_context_helpers(self) -> None:
        dl = _make_drive_loop()
        task = _make_agent_task(task_id="task-current")
        dl._active_task_contexts[task.task_id] = task

        assert dl.resolve_task_context(task.task_id) is task
        assert dl.resolve_task_context("missing") is task
        dl._active_task_contexts.clear()
        assert dl.resolve_task_context("missing") is None

        context = dl._directed_message_context(
            "Please prefer the lower-latency option.",
            {
                "help_summary": "Need a product trade-off",
                "help_reason": "Top two options are close",
                "help_attempted": ["compared cost", "compared quality"],
                "help_recommendation": "Choose latency or quality",
                "help_context": {"workflow_node_id": "chair"},
                "reply_context": {
                    "event_type": "room_notification",
                    "content": "[chair] Need a product trade-off",
                },
            },
        )
        assert "Pending help summary: Need a product trade-off" in context
        assert "Already attempted:" in context
        assert "The human replied to this prior room message:" in context
        assert "[chair] Need a product trade-off" in context
        assert "Human reply: Please prefer the lower-latency option." in context

        direct_context = dl._directed_message_context("Please inspect this", None)
        assert direct_context == (
            "This is a directed message from a human.\nHuman message: Please inspect this"
        )

        recent_context = dl._directed_message_context(
            "makes sense, ignore",
            {
                "recent_room_context": {
                    "content": (
                        "I ignored the transport-only check and preserved the printer concern."
                    )
                }
            },
        )
        assert "the human did not explicitly reply to it" in recent_context
        assert "ignored the transport-only check" in recent_context
        assert recent_context.endswith("Human message: makes sense, ignore")

        dl._settings.resident_state.directed_message_context_max_chars = 200
        bounded_context = dl._directed_message_context(
            "current message",
            {"recent_room_context": {"content": "x" * 500}},
        )
        assert "prior room context truncated: 300 characters omitted" in bounded_context
        assert bounded_context.endswith("Human message: current message")

    def test_record_tool_outcome_fields_merges_existing_values(self) -> None:
        dl = _make_drive_loop()
        task = _make_agent_task(task_id="task-tool-outcomes")
        dl.record_tool_outcome_fields(
            task=task,
            event_type="mimir.source.ingested",
            fields={"source_id": "src_1"},
        )
        dl.record_tool_outcome_fields(
            task=task,
            event_type="mimir.source.ingested",
            fields={"mount_name": "tmp-mimir"},
        )
        assert task.tool_outcomes["mimir.source.ingested"] == {
            "source_id": "src_1",
            "mount_name": "tmp-mimir",
        }

    @pytest.mark.asyncio
    async def test_handle_directed_message_carries_workflow_metadata(self) -> None:
        dl = _make_drive_loop()
        dl.enqueue = AsyncMock()
        dl._persona_config = SimpleNamespace(name="council-chair")

        await dl.handle_directed_message(
            "Please continue with option A",
            {
                "root_correlation_id": "root-1",
                "workflow_parent_event_id": "parent-1",
                "workflow_node_id": "chair-node",
                "session_id": "session-1",
                "trace_context": {"traceparent": "00-room-parent-01"},
                "reply_context": {
                    "event_type": "room_message",
                    "content": "[Ivaldi] decision: ignore transport check only",
                },
            },
        )

        enqueued = dl.enqueue.await_args.args[0]
        assert enqueued.human_initiated is True
        assert enqueued.persona == "council-chair"
        assert enqueued.root_correlation_id == "root-1"
        assert enqueued.workflow_parent_event_id == "parent-1"
        assert enqueued.workflow_node_id == "chair-node"
        assert enqueued.session_id == "session-1"
        assert enqueued.trace_context == {"traceparent": "00-room-parent-01"}
        assert "The human replied to this prior room message:" in enqueued.initiative_context
        assert "transport check only" in enqueued.initiative_context

    @pytest.mark.asyncio
    async def test_handle_directed_message_attaches_durable_inbox_ref(self) -> None:
        dl = _make_drive_loop()
        dl.enqueue = AsyncMock(return_value=True)
        resident_runtime = SimpleNamespace(
            capture_directed_message=AsyncMock(
                return_value="resident/inbox/signals/operator-message.md"
            )
        )
        dl.set_resident_runtime(resident_runtime)

        assert await dl.handle_directed_message("Please retain this objective.") is True

        task = dl.enqueue.await_args.args[0]
        assert task.resident_inbox_refs == ["resident/inbox/signals/operator-message.md"]
        resident_runtime.capture_directed_message.assert_awaited_once_with(
            "Please retain this objective.",
            None,
        )

    @pytest.mark.asyncio
    async def test_handle_directed_message_steers_active_agent_when_available(self) -> None:
        dl = _make_drive_loop()
        dl.enqueue = AsyncMock()
        steering_agent = SimpleNamespace(
            supports_steering=True,
            steering_mode="live",
            steer=AsyncMock(return_value=True),
        )
        dl._active_agents["task-1"] = steering_agent

        await dl.handle_directed_message("Please switch to option B")

        steering_agent.steer.assert_awaited_once_with("Please switch to option B")
        dl.enqueue.assert_not_called()

    def test_default_mimir_mount_fields_prefers_write_routing_then_instances(self) -> None:
        dl = _make_drive_loop()
        dl._settings.mimir.write_routing.default = ["permanent", "scratch"]
        dl._settings.mimir.instances = [SimpleNamespace(name="ignored-instance")]
        assert dl._default_mimir_mount_fields() == {
            "mount_names": ["permanent", "scratch"],
        }

        dl._settings.mimir.write_routing.default = []
        dl._settings.mimir.instances = [SimpleNamespace(name="scratch")]
        assert dl._default_mimir_mount_fields() == {
            "mount_name": "scratch",
            "mount_names": ["scratch"],
        }

    @pytest.mark.asyncio
    async def test_emit_sleipnir_task_completed_includes_structured_outcome(self) -> None:
        dl = _make_drive_loop()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            produces=SimpleNamespace(
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="verdict",
                        enum_values=["pass"],
                    ),
                    "summary": OutcomeField(type="string", description="summary"),
                    "files_changed": OutcomeField(
                        type="number",
                        description="count",
                        required=False,
                    ),
                }
            )
        )
        published = []
        dl._sleipnir_publisher = SimpleNamespace(publish=AsyncMock(side_effect=published.append))

        task = _make_agent_task(task_id="task-sleipnir")
        task.persona = "coder"
        task.session_id = "sess-sleipnir"

        from ravn.domain.events import RavnEvent

        fake_event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="sleipnir",
            payload={"raw_observed_at": datetime(2026, 6, 5, 1, 42, tzinfo=UTC)},
            timestamp=datetime.now(UTC),
            urgency=0.1,
            correlation_id=task.task_id,
            session_id="",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("ravn.drive_loop._sleipnir_task_completed", lambda **kwargs: fake_event)
            await dl._emit_sleipnir_task_completed(
                task,
                "success",
                response_text=(
                    "---outcome---\nverdict: pass\nsummary: shipped\nfiles_changed: 2\n---end---"
                ),
            )

        assert published
        payload = published[0].payload
        assert payload["session_id"] == "sess-sleipnir"
        assert payload["structured_outcome"]["verdict"] == "pass"
        assert payload["summary"] == "shipped"
        assert payload["files_changed"] == 2
        assert payload["raw_observed_at"] == "2026-06-05T01:42:00+00:00"

    @pytest.mark.asyncio
    async def test_emit_sleipnir_valkyrie_outcome_normalizes_payload_for_msgpack(self) -> None:
        dl = _make_drive_loop()
        dl._source_id = "drive_loop"
        published = []
        dl._sleipnir_publisher = SimpleNamespace(publish=AsyncMock(side_effect=published.append))

        task = _make_agent_task(task_id="task-valkyrie-safe")
        task.persona = "k8s-valkyrie"
        task.session_id = "session-valkyrie-safe"

        await dl._emit_sleipnir_valkyrie_outcome(
            registry.VALKYRIE_JUDGMENT_PROPOSED,
            {
                "environment_id": "ymir",
                "valkyrie_id": "valkyrie-ymir-k8s",
                "tier": "present",
                "confidence": 0.84,
                "summary": "ImagePullBackOff needs inspection.",
                "observed_at": datetime(2026, 6, 5, 1, 48, tzinfo=UTC),
                "evidence": [{"seen_at": datetime(2026, 6, 5, 1, 47, tzinfo=UTC)}],
            },
            task,
            "root-valkyrie-safe",
            valid=True,
        )

        assert published
        payload = published[0].payload
        assert payload["observed_at"] == "2026-06-05T01:48:00+00:00"
        assert payload["evidence"] == [{"seen_at": "2026-06-05T01:47:00+00:00"}]

    def test_extract_mimir_dream_counts_prefers_outcome_fields_and_falls_back_to_prose(
        self,
    ) -> None:
        persona = SimpleNamespace(
            produces=SimpleNamespace(
                schema={
                    "pages_updated": OutcomeField(type="number", description="pages"),
                    "entities_created": OutcomeField(type="number", description="entities"),
                    "lint_fixes": OutcomeField(type="number", description="lint"),
                }
            )
        )

        counts = _extract_mimir_dream_counts(
            ("---outcome---\npages_updated: 3\nentities_created: 2\nlint_fixes: 1\n---end---"),
            persona,
        )
        assert counts == {"pages_updated": 3, "entities_created": 2, "lint_fixes": 1}

        assert _extract_mimir_dream_counts(
            "maintenance ran; pages_updated=4, entities_created=0, lint_fixes=2",
            None,
        ) == {"pages_updated": 4, "entities_created": 0, "lint_fixes": 2}

    @pytest.mark.asyncio
    async def test_emit_sleipnir_mimir_dream_completed_for_dream_cycle_task(self) -> None:
        dl = _make_drive_loop()
        dl._source_id = "drive_loop"
        published = []
        dl._sleipnir_publisher = SimpleNamespace(publish=AsyncMock(side_effect=published.append))

        task = _make_agent_task(task_id="task-dream")
        task.triggered_by = "dream_cycle:cron"
        task.persona = "mimir-warden"
        task.session_id = "sess-dream"

        await dl._emit_sleipnir_mimir_dream_completed(
            task,
            response_text=(
                "---outcome---\n"
                "verdict: complete\n"
                "pages_updated: 5\n"
                "entities_created: 2\n"
                "lint_fixes: 1\n"
                "---end---"
            ),
        )

        assert published
        event = published[0]
        assert event.event_type == registry.MIMIR_DREAM_COMPLETED
        assert event.correlation_id == "sess-dream"
        assert event.payload["pages_updated"] == 5
        assert event.payload["entities_created"] == 2
        assert event.payload["lint_fixes"] == 1
        assert event.payload["task_id"] == "task-dream"
        assert event.payload["persona"] == "mimir-warden"

    @pytest.mark.asyncio
    async def test_emit_sleipnir_mimir_dream_completed_ignores_non_dream_task(self) -> None:
        dl = _make_drive_loop()
        dl._sleipnir_publisher = SimpleNamespace(publish=AsyncMock())
        task = _make_agent_task(task_id="task-normal")
        task.triggered_by = "thread:inbox"

        await dl._emit_sleipnir_mimir_dream_completed(task, response_text="pages_updated=1")

        dl._sleipnir_publisher.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emit_tool_outcome_event_publishes_and_tolerates_skuld_failure(self) -> None:
        dl = _make_drive_loop()
        dl._mesh = AsyncMock()
        dl._skuld_channel = AsyncMock()
        dl._skuld_channel.emit.side_effect = RuntimeError("skuld down")
        dl._source_id = "drive_loop"

        task = _make_agent_task(task_id="task-tool-event")
        task.session_id = "sess-tool-event"
        task.root_correlation_id = "root-tool-event"
        task.workflow_parent_event_id = "parent-tool-event"

        await dl.emit_tool_outcome_event(
            task=task,
            persona_name="mimir-warden",
            event_type="mimir.page.written",
            fields={"summary": "page written", "page_path": "research/demo.md"},
        )

        dl._mesh.publish.assert_awaited_once()
        published = dl._mesh.publish.await_args.args[0]
        assert published.payload["summary"] == "page written"
        assert published.payload["workflow_parent_event_id"] == "parent-tool-event"
        assert published.payload["collaboration_routing_only"] is True

    @pytest.mark.asyncio
    async def test_emit_tool_outcome_event_respects_workflow_allowed_topics(self) -> None:
        dl = _make_drive_loop()
        dl._mesh = AsyncMock()
        dl._skuld_channel = AsyncMock()
        dl._persona_config = SimpleNamespace(name="mimir-warden")
        dl.set_workflow_allowed_outcomes_resolver(lambda *_args: {"review.completed"})

        task = _make_agent_task(task_id="task-tool-blocked")
        task.workflow_node_id = "node-blocked"

        await dl.emit_tool_outcome_event(
            task=task,
            persona_name="mimir-warden",
            event_type="mimir.page.written",
            fields={"summary": "page written"},
        )

        dl._mesh.publish.assert_not_awaited()
        dl._skuld_channel.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mesh_outcome_event_normalizes_wrapped_verdict_and_files_changed_list(
        self,
    ) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = AsyncMock()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="reviewer",
            produces=SimpleNamespace(
                event_type="review.completed",
                event_type_map={"needs_changes": "review.changes_requested"},
            ),
        )

        task = _make_agent_task(task_id="task-files-list")
        task.session_id = "sess-files-list"
        response_text = """\
---outcome---
verdict: needs changes
summary: tighten the tests
files_changed:
  - src/app.py
  - tests/test_app.py
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        assert canonical_event.payload["verdict"] == "needs_changes"
        assert canonical_event.payload["fields"]["verdict"] == "needs_changes"
        assert canonical_event.payload["files_changed"] == ["src/app.py", "tests/test_app.py"]

    @pytest.mark.asyncio
    async def test_emit_mesh_outcome_event_tolerates_mesh_and_help_notification_failures(
        self,
    ) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        mesh.publish.side_effect = RuntimeError("mesh down")
        skuld_channel = AsyncMock()
        skuld_channel.emit.side_effect = RuntimeError("skuld down")
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-chair",
            produces=SimpleNamespace(
                event_type="council.chair.turn.completed",
                event_type_map={"help_needed": "council.human_input.requested"},
            ),
        )

        task = _make_agent_task(task_id="task-help-failure")
        task.session_id = "sess-help-failure"
        response_text = """\
---outcome---
verdict: help_needed
summary: need a tie-breaker
question: Which option should I use?
reason: ambiguous
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

    @pytest.mark.asyncio
    async def test_help_needed_verdict_emits_help_notification_with_workflow_context(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        skuld_channel = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-chair",
            produces=SimpleNamespace(
                event_type="council.chair.turn.completed",
                event_type_map={"help_needed": "council.human_input.requested"},
            ),
        )

        task = _make_agent_task(task_id="task-help")
        task.session_id = "sess-help"
        task.root_correlation_id = "root-help"
        task.workflow_parent_event_id = "parent-help"
        task.workflow_node_id = "chair-synthesis"
        response_text = """\
---outcome---
verdict: help_needed
summary: need the user's preference between the top two options
question: Should I optimize for latency or quality?
reason: uncertain
attempted:
  - compared the strongest evidence
  - reviewed prior Mimir notes
recommendation: choose whether to optimize for latency or quality
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 3
        help_event = mesh.publish.await_args_list[1].args[0]
        help_topic = mesh.publish.await_args_list[1].kwargs["topic"]
        alias_event = mesh.publish.await_args_list[2].args[0]

        assert help_topic == "help_needed"
        assert help_event.payload["summary"] == "Should I optimize for latency or quality?"
        assert help_event.payload["context"]["root_correlation_id"] == "root-help"
        assert help_event.payload["context"]["workflow_parent_event_id"] == "parent-help"
        assert help_event.payload["context"]["workflow_node_id"] == "chair-synthesis"
        assert help_event.payload["collaboration_routing_only"] is True
        assert alias_event.payload["event_type"] == "council.human_input.requested"

        emitted_types = [call.args[0].type for call in skuld_channel.emit.await_args_list]
        assert emitted_types == ["outcome", "help_needed", "outcome"]
        assert (
            skuld_channel.emit.await_args_list[2].args[0].payload["event_type"]
            == "council.human_input.requested"
        )

    @pytest.mark.asyncio
    async def test_help_needed_without_question_is_failed_without_help_or_alias(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        skuld_channel = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="coder",
            produces=SimpleNamespace(
                event_type="code.completed",
                event_type_map={"help_needed": "code.blocked"},
            ),
        )

        task = _make_agent_task(task_id="task-empty-help")
        response_text = """\
---outcome---
verdict: help_needed
summary: Created the requested artifact.
reason: pytest was unavailable
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        mesh.publish.assert_not_awaited()
        canonical = skuld_channel.emit.await_args.args[0]
        assert canonical.payload["success"] is False
        assert canonical.payload["valid"] is False
        assert canonical.payload["errors"] == ["help_needed requires a non-empty question"]

    @pytest.mark.asyncio
    async def test_help_needed_bypasses_node_outcome_filter_for_human_intervention(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-chair",
            produces=SimpleNamespace(
                event_type="council.chair.turn.completed",
                event_type_map={
                    "research_published": "research.completed",
                    "help_needed": "council.human_input.requested",
                },
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"research.completed"})

        task = _make_agent_task(task_id="task-help-filter")
        task.session_id = "sess-help-filter"
        task.workflow_node_id = "chair-synthesis"
        response_text = """\
---outcome---
verdict: help_needed
summary: need an operator tie-break
question: Which tradeoff should I prefer?
reason: the final two opinions remain split
attempted:
  - compared the submitted reviews
recommendation: reply with the preferred tradeoff
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        published_topics = [call.kwargs["topic"] for call in mesh.publish.await_args_list]
        assert "council.chair.turn.completed" in published_topics
        assert "council.human_input.requested" in published_topics
        assert "help_needed" in published_topics

    @pytest.mark.asyncio
    async def test_help_event_uses_configured_channel_without_platform_fallback(self) -> None:
        dl = _make_drive_loop()
        sleipnir_publisher = AsyncMock()
        skuld_channel = AsyncMock()
        dl._sleipnir_publisher = sleipnir_publisher
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(name="council-chair")
        task = _make_agent_task(task_id="task-room-help")

        await dl._emit_resident_help_needed(
            task,
            SimpleNamespace(
                case_id="case-room-help",
                reason="needs_context",
                question="Which rollout should I choose?",
                operator_ref="operator-needed/case-room-help.json",
            ),
        )

        skuld_channel.emit.assert_awaited_once()
        sleipnir_publisher.publish.assert_not_awaited()
