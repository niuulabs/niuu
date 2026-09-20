"""Gates used when no pack specialises an execution."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from ting.adapters import plain_child_gates
from ting.adapters.plain_child_gates import (
    SchemaOnlyChildResultVerifier,
    UndeclaredReviewAttestor,
)
from ting.domain.workflow_document import WorkflowDocument, load_workflow_document
from ting.domain.workflow_execution import WorkflowExecutionError
from ting.system_workflows import BUNDLED_SYSTEM_WORKFLOWS_PATH


def _pinned(monkeypatch: pytest.MonkeyPatch, workflow: object) -> None:
    monkeypatch.setattr(plain_child_gates, "pinned_child_workflow", lambda *_: workflow)


def _bundled(name: str) -> WorkflowDocument:
    return load_workflow_document(
        (BUNDLED_SYSTEM_WORKFLOWS_PATH / name).read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_result_is_accepted_with_a_stable_digest() -> None:
    verifier = SchemaOnlyChildResultVerifier()
    execution, child = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())

    first = await verifier.validate(execution, child, {"b": 2, "a": 1})
    second = await verifier.validate(execution, child, {"a": 1, "b": 2})

    assert first.accepted
    assert first.manifest_digest == second.manifest_digest


@pytest.mark.asyncio
async def test_result_passes_through_when_no_review_is_attested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _bundled("code-review-flow.yaml")
    assert "reviewAttestation" not in workflow.graph
    _pinned(monkeypatch, workflow)
    result = {"summary": "published"}

    attested = await UndeclaredReviewAttestor().attest(
        SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4()), result
    )

    assert attested == result


@pytest.mark.asyncio
async def test_a_workflow_that_requires_attested_review_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning the result unchanged would read as though reviews were attested."""
    workflow = _bundled("developer-workstream.yaml")
    assert "reviewAttestation" in workflow.graph
    _pinned(monkeypatch, workflow)

    with pytest.raises(WorkflowExecutionError, match="no review attestor"):
        await UndeclaredReviewAttestor().attest(
            SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4()), {"summary": "published"}
        )
