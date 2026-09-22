"""Transitive workflow closures remain portable, isolated, and tamper evident."""

import base64
import copy
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from tests.test_ting.test_workflow_sharing import Source
from tests.test_ting.test_workflow_sharing import persona as portable_persona_fixture
from tests.test_ting.test_workflow_sharing import workflow as portable_workflow_fixture
from tests.test_ting.test_workflows_api import InMemoryWorkflowRepository, _headers, _make_client
from ting.adapters.filesystem_workflows import FilesystemWorkflowRepository
from ting.adapters.workflow_bundle import WorkflowBundleCodec
from ting.domain.models import WorkflowDependency, WorkflowScope
from ting.domain.services.workflow_sharing import export_workflow, plan_workflow_import
from ting.domain.workflow_document import (
    document_from_workflow,
    load_workflow_document,
    workflow_document_payload,
    workflow_document_revision,
)

persona = portable_persona_fixture
workflow = portable_workflow_fixture


def parent(child):
    document = document_from_workflow(child)
    revision = workflow_document_revision(document)
    return replace(
        child,
        id=uuid4(),
        name="Parent",
        schema_version=2,
        workflow_dependencies={"worker": WorkflowDependency(child.id, revision, revision)},
        workflow_definitions={
            "worker": {
                "document": workflow_document_payload(document),
                "persona_definitions": child.persona_definitions,
                "workflow_definitions": child.workflow_definitions,
            }
        },
    )


def import_bundle(root, **kwargs):
    text, files = export_workflow(root, source=None, bundle=True)
    archive = WorkflowBundleCodec.write(text, files)
    contents = WorkflowBundleCodec(
        max_upload_bytes=1_000_000, max_expanded_bytes=2_000_000, max_entries=100
    ).read(archive, filename="delivery.zip")
    return plan_workflow_import(
        load_workflow_document(contents.workflow),
        contents.files,
        source=kwargs.get("source"),
        mappings=kwargs.get("mappings", {}),
        bindings=kwargs.get("bindings", {}),
    )


@pytest.mark.asyncio
async def test_closure_survives_catalog_transfer_and_restart(tmp_path, workflow):
    root = parent(parent(workflow))
    source = FilesystemWorkflowRepository(
        str(tmp_path / "source"), include_bundled=False, create_directory=True
    )
    stored = await source.save_workflow(root)
    plan = import_bundle(stored)
    assert not plan.errors
    assert [item["alias"] for item in plan.workflows] == ["worker", "worker/worker"]
    assert {item["alias"] for item in plan.personas} == {
        "reviewer",
        "worker/reviewer",
        "worker/worker/reviewer",
    }
    destination = FilesystemWorkflowRepository(
        str(tmp_path / "destination"), include_bundled=False, create_directory=True
    )
    imported = plan.document.to_workflow(
        scope=WorkflowScope.USER,
        owner_id="bob",
        persona_definitions=plan.persona_definitions,
        workflow_definitions=plan.workflow_definitions,
    )
    saved = await destination.save_workflow(imported)
    restarted = FilesystemWorkflowRepository(str(tmp_path / "destination"), include_bundled=False)
    loaded = await restarted.get_workflow(saved.id)
    assert loaded.workflow_definitions == plan.workflow_definitions
    assert not import_bundle(loaded).errors
    assert await restarted.list_workflows(owner_id="alice") == []


def test_child_tampering_and_missing_members_fail(workflow):
    root = parent(workflow)
    text, files = export_workflow(root, source=None, bundle=True)
    document = load_workflow_document(text)
    child_path = document.workflow_dependencies["worker"].path
    tampered = {**files, child_path: files[child_path].replace("Portable review", "Tampered")}
    with pytest.raises(ValueError, match="pin mismatch"):
        plan_workflow_import(document, tampered, source=None, mappings={}, bindings={})
    del files[child_path]
    with pytest.raises(ValueError, match="workflow file is missing"):
        plan_workflow_import(document, files, source=None, mappings={}, bindings={})


def test_mapping_is_scoped_and_recomputes_parent_pin(workflow, persona):
    alternative = replace(
        persona,
        id="alternative",
        revision="new",
        definition={**persona.definition, "name": "alternative"},
    )
    root = parent(workflow)
    plan = import_bundle(
        root, source=Source(alternative), mappings={"worker/reviewer": "alternative"}
    )
    assert not plan.errors
    assert plan.document.persona_dependencies["reviewer"].id == persona.id
    child = plan.workflow_definitions["worker"]
    assert child["document"]["persona_dependencies"]["reviewer"]["id"] == "alternative"
    assert (
        plan.document.workflow_dependencies["worker"].digest
        != root.workflow_dependencies["worker"].digest
    )
    assert plan.document.workflow_dependencies["worker"].digest == workflow_document_revision(
        load_workflow_document(json.dumps(child["document"]))
    )


def test_standalone_uses_only_supplied_local_workflows(workflow, persona):
    root = parent(parent(workflow))
    text, files = export_workflow(root, source=None, bundle=False)
    assert files == {}
    document = load_workflow_document(text)
    missing = plan_workflow_import(document, {}, source=Source(persona), mappings={}, bindings={})
    assert not missing.preview(context={})["can_apply"]
    middle = root.workflow_definitions["worker"]
    local = load_workflow_document(json.dumps(middle["document"])).to_workflow(
        scope=WorkflowScope.USER,
        owner_id="alice",
        persona_definitions=middle["persona_definitions"],
        workflow_definitions=middle["workflow_definitions"],
    )
    plan = plan_workflow_import(
        document,
        {},
        source=Source(persona),
        mappings={},
        bindings={},
        local_workflows={str(local.id): local},
    )
    assert not plan.errors


def test_unused_files_and_unknown_scoped_mapping_rejected(workflow):
    root = parent(workflow)
    text, files = export_workflow(root, source=None, bundle=True)
    plan = plan_workflow_import(
        load_workflow_document(text),
        {**files, "workflows/extra.yaml": "{}"},
        source=None,
        mappings={"bad/reviewer": "reviewer"},
        bindings={},
    )
    assert any("undeclared" in error for error in plan.errors)
    assert any("unknown alias" in error for error in plan.errors)


def test_export_rejects_missing_or_modified_scoped_content(workflow):
    root = parent(workflow)
    with pytest.raises(ValueError, match="unavailable"):
        export_workflow(replace(root, workflow_definitions={}), source=None, bundle=True)
    changed = copy.deepcopy(root.workflow_definitions)
    changed["worker"]["document"]["name"] = "Modified child"
    with pytest.raises(ValueError, match="pin mismatch"):
        export_workflow(replace(root, workflow_definitions=changed), source=None, bundle=True)


def test_api_import_does_not_resolve_inaccessible_child(workflow, persona):
    child = replace(workflow, owner_id="bob")
    root = parent(child)
    text, _ = export_workflow(root, source=None, bundle=False)
    repo = InMemoryWorkflowRepository([child])
    client = _make_client(repo)
    client.app.state.persona_source = Source(persona)
    response = client.post(
        "/api/v1/ting/workflows/imports/preview",
        headers=_headers(),
        json={"content": base64.b64encode(text.encode()).decode(), "filename": "root.yaml"},
    )
    assert response.status_code == 200
    assert response.json()["can_apply"] is False
    assert "unavailable" in " ".join(response.json()["errors"])
