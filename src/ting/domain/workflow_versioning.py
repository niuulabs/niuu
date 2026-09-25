"""Pure contracts for immutable workflow version history."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal

from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import (
    load_workflow_document,
    workflow_document_payload,
    workflow_document_revision,
)

WorkflowVersionBump = Literal["patch", "minor", "major"]

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$")


def next_workflow_version(
    latest_version: str,
    bump: WorkflowVersionBump = "patch",
) -> str:
    """Return the server-assigned successor version.

    Legacy/new ``draft`` heads enter the immutable sequence at ``0.1.0``.
    Persisted non-draft labels must be plain semantic versions so ordering and
    uniqueness do not depend on adapter-specific string rules.
    """

    if bump not in {"patch", "minor", "major"}:
        raise ValueError(f"Unsupported workflow version bump: {bump!r}")
    normalized = latest_version.strip()
    if normalized == "draft":
        return "0.1.0"
    match = _SEMVER.fullmatch(normalized)
    if match is None:
        raise ValueError(
            f"Workflow version {latest_version!r} cannot be advanced; "
            "expected MAJOR.MINOR or MAJOR.MINOR.PATCH"
        )
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def workflow_version_tuple(version: str) -> tuple[int, int, int]:
    """Parse a persisted semantic version label for ordering comparisons.

    Uses the same ``_SEMVER`` shape ``next_workflow_version`` parses, so
    ordering and increment agree on what counts as a valid version label.
    """
    normalized = version.strip()
    if normalized == "draft":
        return (0, 0, 0)
    match = _SEMVER.fullmatch(normalized)
    if match is None:
        raise ValueError(
            f"Workflow version {version!r} cannot be compared; "
            "expected MAJOR.MINOR or MAJOR.MINOR.PATCH"
        )
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch or 0))


def serialize_workflow_version(workflow: WorkflowDefinition) -> dict[str, Any]:
    """Serialize the complete immutable aggregate stored for one version."""

    document_revision = workflow_document_revision(workflow)
    return {
        "document": workflow_document_payload(workflow),
        "document_revision": document_revision,
        "scope": workflow.scope.value,
        "owner_id": workflow.owner_id,
        "tenant_id": workflow.tenant_id,
        "persona_definitions": workflow.persona_definitions,
        "workflow_definitions": workflow.workflow_definitions,
        "requirements": workflow.requirements,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
        "origin": workflow.origin,
        "based_on_revision": workflow.based_on_revision,
    }


def deserialize_workflow_version(
    payload: dict[str, Any],
    *,
    head_revision: str | None,
    is_head: bool,
) -> WorkflowDefinition:
    """Validate and restore a complete version aggregate from JSON storage."""

    if not isinstance(payload, dict):
        raise ValueError("Workflow version snapshot must be an object")
    document_payload = payload.get("document")
    if not isinstance(document_payload, dict):
        raise ValueError("Workflow version snapshot document must be an object")
    document = load_workflow_document(json.dumps(document_payload))
    expected_revision = workflow_document_revision(document)
    if payload.get("document_revision") != expected_revision:
        raise ValueError("Workflow version snapshot does not match its document revision")
    try:
        scope = WorkflowScope(str(payload["scope"]))
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("Workflow version snapshot metadata is invalid") from exc
    persona_definitions = payload.get("persona_definitions", {})
    workflow_definitions = payload.get("workflow_definitions", {})
    requirements = payload.get("requirements", [])
    if not isinstance(persona_definitions, dict) or not isinstance(workflow_definitions, dict):
        raise ValueError("Workflow version scoped definitions must be objects")
    if not isinstance(requirements, list):
        raise ValueError("Workflow version requirements must be an array")
    origin = str(payload.get("origin") or "authored")
    workflow = document.to_workflow(
        scope=scope,
        owner_id=(str(payload["owner_id"]) if payload.get("owner_id") is not None else None),
        tenant_id=str(payload.get("tenant_id") or ""),
        created_at=created_at,
        updated_at=updated_at,
        revision=head_revision,
        read_only=not is_head or origin == "bundled",
        source=None,
        persona_definitions=persona_definitions,
        workflow_definitions=workflow_definitions,
        requirements=requirements,
    )
    return replace(
        workflow,
        document_revision=expected_revision,
        is_head=is_head,
        origin=origin,
        based_on_revision=(
            str(payload["based_on_revision"])
            if payload.get("based_on_revision") is not None
            else None
        ),
    )
