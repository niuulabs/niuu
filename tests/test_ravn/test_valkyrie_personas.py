"""Tests for resident Valkyrie persona contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI

from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.adapters.personas.loader import FilesystemPersonaAdapter, PersonaConfig
from ravn.agent import _parse_outcome_block_for_persona
from ravn.api.personas import create_personas_router
from ravn.domain.events import RavnEvent, RavnEventType
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    signal_received,
    valkyrie_action_proposed,
    valkyrie_judgment_proposed,
    valkyrie_state_updated,
)

RESIDENT_VALKYRIES = {
    "k8s-valkyrie": {
        "environment_type": "k8s",
        "signal": registry.SIGNAL_KUBERNETES_EVENT,
        "action": "k8s.inspect_pod",
        "state": "investigating",
    },
}


@dataclass
class _FakeSubscription:
    topic: str
    handler: Callable
    active: bool = True

    async def unsubscribe(self) -> None:
        self.active = False


class _FakeSleipnirTransport:
    def __init__(self) -> None:
        self.published: list[Any] = []
        self.subscriptions: list[_FakeSubscription] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)
        for sub in self.subscriptions:
            if sub.active and self._matches(sub.topic, event.event_type):
                await sub.handler(event)

    async def subscribe(
        self,
        event_types: list[str],
        handler: Callable,
    ) -> _FakeSubscription:
        topic = event_types[0] if event_types else "*"
        sub = _FakeSubscription(topic=topic, handler=handler)
        self.subscriptions.append(sub)
        return sub

    def _matches(self, pattern: str, event_type: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return event_type.startswith(pattern[:-1])
        return pattern == event_type


def _load(name: str) -> PersonaConfig:
    persona = FilesystemPersonaAdapter().load(name)
    assert persona is not None
    return persona


def _event_type_for_decision(persona: PersonaConfig, decision: str) -> str:
    return persona.produces.event_type_map.get(decision, persona.produces.event_type)


def _outcome_text(persona: PersonaConfig, decision: str, fields: dict[str, Any]) -> str:
    values = {
        "decision": decision,
        "environment_id": f"env-{persona.name}",
        "environment_type": RESIDENT_VALKYRIES[persona.name]["environment_type"],
        "valkyrie_id": persona.name,
        "operational_state": RESIDENT_VALKYRIES[persona.name]["state"],
        "wakefulness": "wakeful",
        "tier": "ambient",
        "action_authority": "autonomous",
        "action_capability": RESIDENT_VALKYRIES[persona.name]["action"],
        "recommended_action": "Inspect the signal before changing external state.",
        "confidence": 0.82,
        "signal_refs": ["evt-fixture"],
        "rationale": "fixture signal supports the judgment",
        "evidence": [{"event_id": "evt-fixture", "kind": "fixture"}],
        "target_surfaces": ["surface:operator"],
        "expires_at": "none",
        "dissent_refs": [],
        "correlation_ids": {
            "root": f"corr-{persona.name}",
            "task": f"task-{persona.name}",
        },
        "evidence_summary": "fixture signal supports the judgment",
        "state_summary": "resident state updated from fixture signal",
        "learned_pattern": "none",
        **fields,
    }
    body = "\n".join(f"{key}: {value}" for key, value in values.items())
    return f"---outcome---\n{body}\n---end---"


def test_resident_valkyries_are_builtin_personas() -> None:
    names = set(FilesystemPersonaAdapter().list_builtin_names())

    assert set(RESIDENT_VALKYRIES).issubset(names)


@pytest.mark.parametrize("name", sorted(RESIDENT_VALKYRIES))
def test_resident_valkyrie_contracts_load_and_inject_outcome(name: str) -> None:
    persona = _load(name)
    spec = RESIDENT_VALKYRIES[name]

    assert persona.permission_mode == "workspace-write"
    assert persona.stop_on_outcome is True
    assert persona.iteration_budget > 0
    assert persona.produces.event_type == registry.VALKYRIE_JUDGMENT_PROPOSED
    assert persona.produces.event_type_map["propose_action"] == registry.VALKYRIE_ACTION_PROPOSED
    assert spec["signal"] in persona.consumes.event_types
    assert registry.VALKYRIE_STATE_UPDATED in persona.consumes.event_types
    assert persona.produces.schema["tier"].enum_values == [
        "silent",
        "ambient",
        "present",
        "urgent",
    ]
    assert persona.produces.schema["evidence"].type == "array"
    assert persona.produces.schema["correlation_ids"].type == "object"
    assert "---outcome---" in persona.system_prompt_template
    assert "\nenvironment_id:" not in persona.system_prompt_template
    assert "\nenvironment_type:" not in persona.system_prompt_template
    assert "\nvalkyrie_id:" not in persona.system_prompt_template
    assert "\ncorrelation_ids:" not in persona.system_prompt_template
    assert "Environment binding" in persona.system_prompt_template
    assert "ODIN/court" in persona.system_prompt_template
    assert "Flock/NATS mesh" in persona.system_prompt_template
    assert spec["action"] in persona.system_prompt_template
    assert "ravn" in persona.allowed_tools
    assert "workflow" in persona.allowed_tools
    assert "web" in persona.allowed_tools
    assert "todo_read" in persona.allowed_tools
    assert "todo_write" in persona.allowed_tools
    assert "cron_create" in persona.allowed_tools
    assert "cron_list" in persona.allowed_tools
    assert "cron_delete" in persona.allowed_tools
    assert "build_tool" in persona.allowed_tools


def test_ivaldi_model_contract_excludes_runtime_owned_event_envelope() -> None:
    persona = _load("ivaldi")
    prompt = persona.system_prompt_template

    assert "\nenvironment_id:" not in prompt
    assert "\nenvironment_type:" not in prompt
    assert "\nvalkyrie_id:" not in prompt
    assert "\ncorrelation_ids:" not in prompt

    response = """---outcome---
decision: watch
operational_state: watching
wakefulness: wakeful
tier: silent
action_authority: autonomous
action_capability: none
signal_refs: [evt-real]
rationale: One observation is insufficient to infer a stable pattern.
evidence: [{event_id: evt-real}]
target_surfaces: []
expires_at: ""
dissent_refs: []
recommended_action: Observe another event.
selected_next_action: Observe another event.
continuation: sleep
next_action_timing: external_event
question: ""
open_questions: []
confidence: 0.5
evidence_summary: One observation.
state_summary: Watching for repetition.
learned_pattern: none
capability_gap: none
tool_evolution_plan: none
working_state:
  observations: ["evt-real: one observation"]
  hypotheses: []
  unknowns: ["whether the observation repeats"]
  capability_gaps: []
  attempts: []
---end---"""

    parsed = _parse_outcome_block_for_persona(response, persona)

    assert parsed is not None
    assert parsed.valid is True
    assert "environment_type" not in parsed.fields


def test_ivaldi_can_investigate_and_work_in_its_configured_environment() -> None:
    persona = _load("ivaldi")

    assert "build_tool" in persona.allowed_tools
    assert {"a2a_task", "workflow", "web"}.issubset(persona.allowed_tools)
    assert {
        "file",
        "terminal",
        "todo_read",
        "todo_write",
        "volundr",
    }.issubset(persona.allowed_tools)
    assert persona.forbidden_tools == []
    assert persona.iteration_budget == 80
    assert persona.stop_on_outcome is False
    assert "Uncertainty is work to assess, not a reason to stop" in persona.system_prompt_template


@pytest.mark.parametrize("name", sorted(RESIDENT_VALKYRIES))
def test_fixture_signal_to_state_judgment_and_action_events(name: str) -> None:
    persona = _load(name)
    spec = RESIDENT_VALKYRIES[name]
    signal_kind_by_type = {
        "k8s": "kubernetes",
        "host": "inbox",
        "printer_pi": "printer",
    }
    signal = signal_received(
        environment_id=f"env-{name}",
        environment_type=spec["environment_type"],
        signal_source=f"fixture.{name}",
        signal_kind=signal_kind_by_type[spec["environment_type"]],
        severity="warning",
        data={"fixture": name},
        source="test:fixture",
        correlation_id=f"corr-{name}",
    )
    state = valkyrie_state_updated(
        environment_id=f"env-{name}",
        valkyrie_id=name,
        previous_state="watching",
        new_state=spec["state"],
        reason="fixture signal required state update",
        source=f"valkyrie:{name}",
        correlation_id=signal.correlation_id,
        causation_id=signal.event_id,
    )
    judgment = valkyrie_judgment_proposed(
        environment_id=f"env-{name}",
        valkyrie_id=name,
        attention_tier="ambient",
        recommended_action="Inspect the signal before changing external state.",
        authority_boundary="autonomous",
        confidence=0.82,
        operational_state=spec["state"],
        rationale="fixture signal required a judgment",
        signal_refs=[signal.event_id],
        evidence=[{"event_id": signal.event_id}, {"event_id": state.event_id}],
        target_surfaces=["surface:operator"],
        expires_at="",
        dissent_refs=[],
        correlation_ids={"root": signal.correlation_id},
        source=f"valkyrie:{name}",
        correlation_id=signal.correlation_id,
        causation_id=state.event_id,
    )
    action = valkyrie_action_proposed(
        environment_id=f"env-{name}",
        valkyrie_id=name,
        action_id=f"action-{name}",
        capability=spec["action"],
        action_authority="autonomous",
        target={"fixture": name},
        rationale="fixture action proposal",
        dry_run=True,
        source=f"valkyrie:{name}",
        correlation_id=signal.correlation_id,
        causation_id=judgment.event_id,
    )

    assert signal.event_type == spec["signal"]
    assert state.event_type == registry.VALKYRIE_STATE_UPDATED
    assert judgment.event_type == persona.produces.event_type
    assert action.event_type == _event_type_for_decision(persona, "propose_action")
    assert action.payload["capability"] == spec["action"]
    assert action.causation_id == judgment.event_id


@pytest.mark.parametrize("name", sorted(RESIDENT_VALKYRIES))
def test_fixture_outcomes_match_persona_schema(name: str) -> None:
    persona = _load(name)
    schema = OutcomeSchema(persona.produces.schema)
    parsed = parse_outcome_block(_outcome_text(persona, "propose_action", {}), schema)

    assert parsed is not None
    assert parsed.valid is True
    assert parsed.fields["environment_type"] == RESIDENT_VALKYRIES[name]["environment_type"]
    assert parsed.fields["action_capability"] == RESIDENT_VALKYRIES[name]["action"]
    assert parsed.fields["tier"] == "ambient"
    assert parsed.fields["signal_refs"] == ["evt-fixture"]
    assert parsed.fields["correlation_ids"]["task"] == f"task-{name}"


@pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient` is deprecated")
def test_persona_api_surfaces_resident_valkyrie_contracts() -> None:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    app = FastAPI()
    app.include_router(create_personas_router(FilesystemPersonaAdapter()))
    client = TestClient(app, raise_server_exceptions=True)

    listed = client.get("/api/v1/ravn/personas?source=builtin")
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()}
    assert set(RESIDENT_VALKYRIES).issubset(names)

    detail = client.get("/api/v1/ravn/personas/k8s-valkyrie")
    assert detail.status_code == 200
    body = detail.json()
    assert body["produces_event"] == registry.VALKYRIE_JUDGMENT_PROPOSED
    assert registry.SIGNAL_KUBERNETES_EVENT in body["consumes_events"]
    assert "action_capability" in body["produces"]["schema"]


@pytest.mark.asyncio
async def test_valkyrie_outcomes_publish_over_existing_mesh_path() -> None:
    transport = _FakeSleipnirTransport()
    received_judgments: list[RavnEvent] = []
    received_actions: list[RavnEvent] = []

    court_mesh = SleipnirMeshAdapter(
        publisher=transport,
        subscriber=transport,
        own_peer_id="odin-court",
    )
    action_mesh = SleipnirMeshAdapter(
        publisher=transport,
        subscriber=transport,
        own_peer_id="action-router",
    )
    await court_mesh.subscribe(registry.VALKYRIE_JUDGMENT_PROPOSED, received_judgments.append)
    await action_mesh.subscribe(registry.VALKYRIE_ACTION_PROPOSED, received_actions.append)

    for name, decision in [
        ("k8s-valkyrie", "propose_action"),
    ]:
        persona = _load(name)
        event_type = _event_type_for_decision(persona, decision)
        mesh = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id=name,
        )
        await mesh.publish(
            RavnEvent(
                type=RavnEventType.OUTCOME,
                source="drive_loop",
                payload={
                    "event_type": event_type,
                    "canonical_event_type": persona.produces.event_type,
                    "persona": persona.name,
                    "success": True,
                    "outcome": parse_outcome_block(
                        _outcome_text(persona, decision, {}),
                        OutcomeSchema(persona.produces.schema),
                    ).fields,
                },
                timestamp=datetime.now(UTC),
                urgency=0.4,
                correlation_id=f"task-{name}",
                session_id=f"session-{name}",
                task_id=f"task-{name}",
            ),
            topic=event_type,
        )

    assert [event.payload["persona"] for event in received_judgments] == []
    assert [event.payload["persona"] for event in received_actions] == ["k8s-valkyrie"]
    assert received_actions[0].payload["outcome"]["action_capability"] == "k8s.inspect_pod"
