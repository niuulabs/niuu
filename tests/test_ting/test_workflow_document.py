from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ravn.domain.persona_document import PersonaDependency
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import (
    document_from_workflow,
    dump_workflow_document,
    load_workflow_document,
    workflow_document_revision,
)


def _workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Review flow",
        description="Line one\nLine two",
        version="2.1.0",
        scope=WorkflowScope.USER,
        owner_id="owner",
        graph={
            "nodes": [
                {
                    "id": "review",
                    "kind": "stage",
                    "stageMembers": [{"personaId": "reviewer", "budget": 25}],
                    "position": {"x": 1.5, "y": 2},
                }
            ],
            "edges": [],
            "artifactPaths": ["reviews/{slug}.md"],
            "futureExtension": {"preserved": True},
        },
        created_at=now,
        updated_at=now,
        persona_dependencies={
            "reviewer": PersonaDependency(
                id="reviewer",
                revision="initial",
                digest="sha256:" + "a" * 64,
                path="personas/reviewer.yaml",
            )
        },
    )


def test_workflow_document_round_trip_is_lossless_and_stable() -> None:
    workflow = _workflow()
    text = dump_workflow_document(workflow)
    document = load_workflow_document(text)

    assert document == document_from_workflow(workflow)
    assert dump_workflow_document(document) == text
    assert document.graph["futureExtension"] == {"preserved": True}
    assert workflow_document_revision(document) == workflow_document_revision(workflow)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "schema_version: 1\nschema_version: 1\nid: x\n",
            "Duplicate YAML mapping key",
        ),
        (
            "schema_version: true\nid: 12a3cb9e-f9f7-400b-bf33-b97c446637c8\n"
            "name: test\ndescription: ''\nversion: draft\npersona_dependencies: {}\n"
            "graph: {nodes: [], edges: []}\n",
            "Unsupported workflow schema_version",
        ),
        (
            "schema_version: 1\nid: 12a3cb9e-f9f7-400b-bf33-b97c446637c8\n"
            "name: test\ndescription: ''\nversion: draft\npersona_dependencies: {}\n"
            "graph: {nodes: [{stageMembers: [{personaId: reviewer}]}], edges: []}\n",
            "undeclared persona alias",
        ),
    ],
)
def test_workflow_document_rejects_invalid_documents(text: str, message: str) -> None:
    with pytest.raises(WorkflowDocumentError, match=message):
        load_workflow_document(text)


def test_workflow_document_rejects_unsafe_persona_path() -> None:
    text = dump_workflow_document(_workflow()).replace(
        "personas/reviewer.yaml", "personas/../reviewer.yaml"
    )
    with pytest.raises(WorkflowDocumentError, match="normalized personas"):
        load_workflow_document(text)


def test_workflow_document_requires_pin_for_legacy_persona_ids() -> None:
    workflow = _workflow()
    legacy = replace(
        workflow,
        graph={
            "nodes": [{"id": "stage", "stageMembers": [], "personaIds": ["coder"]}],
            "edges": [],
        },
        persona_dependencies={},
    )
    with pytest.raises(WorkflowDocumentError, match="undeclared persona alias.*coder"):
        load_workflow_document(dump_workflow_document(legacy))
