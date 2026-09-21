from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import workflow_document_revision
from ting.domain.workflow_versioning import (
    deserialize_workflow_version,
    next_workflow_version,
    serialize_workflow_version,
)


def _workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Versioned",
        description="",
        version="1.2.3",
        scope=WorkflowScope.USER,
        owner_id="owner",
        tenant_id="tenant",
        graph={"nodes": [], "edges": []},
        created_at=now,
        updated_at=now,
        persona_definitions={"writer": {"id": "writer", "revision": "r1"}},
        requirements=[{"id": "memory", "kind": "mimir", "resolved": True}],
        based_on_revision="sha256:" + "a" * 64,
    )


@pytest.mark.parametrize(
    ("current", "bump", "expected"),
    [
        ("draft", "patch", "0.1.0"),
        ("1.2", "patch", "1.2.1"),
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
    ],
)
def test_next_workflow_version_is_deterministic(current, bump, expected) -> None:
    assert next_workflow_version(current, bump) == expected


def test_next_workflow_version_rejects_ambiguous_legacy_labels() -> None:
    with pytest.raises(ValueError, match="expected MAJOR.MINOR"):
        next_workflow_version("release-one")


def test_next_workflow_version_rejects_unknown_bump() -> None:
    with pytest.raises(ValueError, match="Unsupported workflow version bump"):
        next_workflow_version("1.2.3", "epoch")  # type: ignore[arg-type]


def test_version_snapshot_round_trips_complete_aggregate() -> None:
    workflow = _workflow()
    payload = serialize_workflow_version(workflow)
    restored = deserialize_workflow_version(payload, head_revision="head:2", is_head=False)

    assert restored.id == workflow.id
    assert restored.graph == workflow.graph
    assert restored.persona_definitions == workflow.persona_definitions
    assert restored.requirements == workflow.requirements
    assert restored.revision == "head:2"
    assert restored.document_revision == workflow_document_revision(workflow)
    assert restored.based_on_revision == workflow.based_on_revision
    assert restored.is_head is False
    assert restored.read_only is True


def test_version_snapshot_rejects_tampered_document() -> None:
    payload = serialize_workflow_version(_workflow())
    payload["document"]["name"] = "Tampered"
    with pytest.raises(ValueError, match="does not match"):
        deserialize_workflow_version(payload, head_revision="head:2", is_head=True)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda _payload: [], "must be an object"),
        (lambda payload: {**payload, "document": []}, "document must be an object"),
        (lambda payload: {**payload, "scope": "unknown"}, "metadata is invalid"),
        (
            lambda payload: {**payload, "persona_definitions": []},
            "scoped definitions must be objects",
        ),
        (lambda payload: {**payload, "requirements": {}}, "requirements must be an array"),
    ],
)
def test_version_snapshot_rejects_invalid_storage_payloads(mutation, message) -> None:
    payload = serialize_workflow_version(_workflow())
    with pytest.raises(ValueError, match=message):
        deserialize_workflow_version(mutation(payload), head_revision="head:2", is_head=True)
