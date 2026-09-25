from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ravn.domain.persona_document import PersonaDependency
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import (
    WorkflowPlacement,
    document_from_workflow,
    dump_workflow_document,
    load_workflow_document,
    load_workflow_placement,
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


def _workflow_with_placement(placement: dict, *, schema_version: int = 2) -> object:
    workflow = _workflow()
    return replace(
        workflow,
        schema_version=schema_version,
        graph={**workflow.graph, "placement": placement},
    )


class TestWorkflowGraphPlacement:
    def test_absent_placement_parses_to_none(self) -> None:
        document = load_workflow_document(dump_workflow_document(_workflow()))
        assert load_workflow_placement(document.graph) is None

    def test_tags_selector_defaults_to_match_all(self) -> None:
        workflow = _workflow_with_placement({"tags": ["gpu", "us-west"]})
        document = load_workflow_document(dump_workflow_document(workflow))
        assert load_workflow_placement(document.graph) == WorkflowPlacement(
            tags=("gpu", "us-west"), match="all"
        )

    def test_tags_selector_honors_explicit_match_any(self) -> None:
        workflow = _workflow_with_placement({"tags": ["gpu"], "match": "any"})
        document = load_workflow_document(dump_workflow_document(workflow))
        assert load_workflow_placement(document.graph) == WorkflowPlacement(
            tags=("gpu",), match="any"
        )

    def test_instance_selector_round_trips(self) -> None:
        workflow = _workflow_with_placement({"instance": "spark-01"})
        document = load_workflow_document(dump_workflow_document(workflow))
        assert load_workflow_placement(document.graph) == WorkflowPlacement(instance="spark-01")

    def test_placement_changes_the_pinned_document_digest(self) -> None:
        unplaced = _workflow()
        placed = _workflow_with_placement({"instance": "spark-01"})
        assert workflow_document_revision(unplaced) != workflow_document_revision(placed)

    @pytest.mark.parametrize(
        ("placement", "reason"),
        [
            ({}, "mapping with 'tags' or 'instance'"),
            ({"match": "all"}, "exactly one of 'tags' or 'instance'"),
            (
                {"tags": ["gpu"], "instance": "spark-01"},
                "exactly one of 'tags' or 'instance'",
            ),
            ({"instance": "spark-01", "match": "all"}, "not combine 'instance' with 'match'"),
            ({"instance": "spark-01", "region": "eu"}, "unknown field"),
            ({"tags": []}, "non-empty list"),
            ({"tags": "gpu"}, "non-empty list"),
            ({"tags": ["gpu", "gpu"]}, "not repeat tag names"),
            ({"tags": ["gpu"], "match": "sometimes"}, "must be 'all' or 'any'"),
            ({"instance": "  "}, "non-empty string"),
        ],
    )
    def test_rejects_invalid_placement(self, placement: dict, reason: str) -> None:
        workflow = _workflow_with_placement(placement)
        with pytest.raises(WorkflowDocumentError, match=reason):
            load_workflow_document(dump_workflow_document(workflow))

    def test_rejects_stage_level_placement(self) -> None:
        workflow = _workflow()
        node = dict(workflow.graph["nodes"][0])
        node["placement"] = {"instance": "spark-01"}
        staged = replace(workflow, graph={**workflow.graph, "nodes": [node]})
        with pytest.raises(WorkflowDocumentError, match="stage-level placement"):
            load_workflow_document(dump_workflow_document(staged))

    def test_rejects_placement_on_a_schema_version_1_document(self) -> None:
        workflow = _workflow_with_placement({"instance": "spark-01"}, schema_version=1)
        with pytest.raises(WorkflowDocumentError, match="requires workflow schema_version 2"):
            load_workflow_document(dump_workflow_document(workflow))
