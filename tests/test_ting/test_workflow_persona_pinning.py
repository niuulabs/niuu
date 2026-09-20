from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from ravn.adapters.personas.loader import PersonaConfig
from ravn.domain.persona_document import portable_persona_from_config
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_snapshot import (
    build_workflow_snapshot,
    pin_workflow_personas,
    workflow_runtime_personas_from_snapshot,
)


class MutablePersonaSource:
    def __init__(self, config: PersonaConfig) -> None:
        self.config = config

    def load_portable(self, persona_id: str, revision: str):  # noqa: ANN201
        current = self.load_current_portable(persona_id)
        if current is None or current.revision != revision:
            return None
        return current

    def load_current_portable(self, persona_id: str):  # noqa: ANN201
        if persona_id != self.config.name:
            return None
        return portable_persona_from_config(self.config)


def _workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Pinned review",
        description="Review using exact persona source",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="user-1",
        graph={
            "nodes": [
                {
                    "id": "review-stage",
                    "kind": "stage",
                    "stageMembers": [
                        {"personaId": "reviewer", "model": "model-a", "label": "Reviewer"}
                    ],
                }
            ],
            "edges": [],
        },
        created_at=now,
        updated_at=now,
    )


def test_workflow_snapshot_and_runtime_remain_pinned_after_catalog_change() -> None:
    source = MutablePersonaSource(
        PersonaConfig(name="reviewer", system_prompt_template="Original behavior")
    )
    pinned_workflow = pin_workflow_personas(_workflow(), source)

    source.config = PersonaConfig(name="reviewer", system_prompt_template="Changed behavior")
    snapshot = build_workflow_snapshot(pinned_workflow, persona_source=source)
    runtime_personas = workflow_runtime_personas_from_snapshot(snapshot)

    assert snapshot["persona_definitions"]["reviewer"]["definition"][
        "system_prompt_template"
    ] == "Original behavior"
    assert runtime_personas[0]["portable_definition"]["definition"][
        "system_prompt_template"
    ] == "Original behavior"
    assert runtime_personas[0]["model"] == "model-a"


def test_repin_adds_new_alias_and_removes_stale_dependency() -> None:
    source = MutablePersonaSource(PersonaConfig(name="reviewer", system_prompt_template="Review"))
    pinned = pin_workflow_personas(_workflow(), source)
    graph = dict(pinned.graph)
    graph["nodes"] = []
    without_stage = WorkflowDefinition(
        **{
            **pinned.__dict__,
            "graph": graph,
        }
    )

    repinned = pin_workflow_personas(without_stage, source)

    assert repinned.persona_dependencies == {}
    assert repinned.persona_definitions == {}


def test_snapshot_is_deeply_immutable_from_workflow_edits() -> None:
    source = MutablePersonaSource(PersonaConfig(name="reviewer", system_prompt_template="Review"))
    pinned = pin_workflow_personas(_workflow(), source)
    snapshot = build_workflow_snapshot(pinned, persona_source=source)

    pinned.graph["nodes"][0]["stageMembers"][0]["label"] = "Mutated"
    pinned.persona_definitions["reviewer"]["definition"]["system_prompt_template"] = "Mutated"

    assert snapshot["graph"]["nodes"][0]["stageMembers"][0]["label"] == "Reviewer"
    assert snapshot["persona_definitions"]["reviewer"]["definition"][
        "system_prompt_template"
    ] == "Review"


def test_snapshot_rejects_unresolved_environment_requirements() -> None:
    workflow = replace(
        _workflow(),
        requirements=[{"kind": "integration", "name": "tracker", "resolved": False}],
    )

    try:
        build_workflow_snapshot(workflow)
    except ValueError as exc:
        assert "Resolve imported workflow bindings" in str(exc)
    else:
        raise AssertionError("unresolved workflow requirements must block snapshotting")
