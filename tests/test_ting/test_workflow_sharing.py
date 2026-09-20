"""Portable workflow preview/apply and exact persona sharing behavior."""

import base64
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ravn.domain.persona_document import PortablePersonaDefinition, portable_persona_yaml
from tests.test_ting.test_workflows_api import (
    InMemoryWorkflowRepository,
    _headers,
    _make_client,
)
from ting.adapters.workflow_bundle import WorkflowBundleCodec
from ting.domain.models import PersonaDependency, WorkflowDefinition, WorkflowScope
from ting.domain.services.workflow_sharing import export_workflow, plan_workflow_import
from ting.domain.workflow_document import document_from_workflow, dump_workflow_document


class Source:
    def __init__(self, *documents):
        self.documents = {(doc.id, doc.revision): doc for doc in documents}

    def load_portable(self, name, revision):
        return self.documents.get((name, revision))

    def load_current_portable(self, name):
        return next((doc for (id_, _), doc in self.documents.items() if id_ == name), None)


@pytest.fixture
def persona():
    return PortablePersonaDefinition(
        id="reviewer",
        revision="initial",
        definition={
            "name": "reviewer",
            "system_prompt_template": "Review carefully.\nKeep evidence.\n",
            "allowed_tools": ["read"],
            "produces": {"event_type": "review.completed"},
        },
    )


@pytest.fixture
def workflow(persona):
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Review",
        description="Portable review",
        version="1.0",
        scope=WorkflowScope.USER,
        owner_id="alice",
        tenant_id="tenant-a",
        created_at=now,
        updated_at=now,
        graph={
            "nodes": [
                {
                    "id": "review",
                    "kind": "stage",
                    "label": "Review",
                    "stageMembers": [{"personaId": "reviewer", "budget": 5}],
                }
            ],
            "edges": [],
            "artifactPaths": ["reviews/{slug}/result.md"],
            "extension": {"preserved": True},
        },
        persona_dependencies={
            "reviewer": PersonaDependency(
                id=persona.id, revision=persona.revision, digest=persona.digest
            )
        },
        persona_definitions={"reviewer": persona.to_dict()},
    )


def test_bundle_shares_exact_definition_without_global_install(workflow, persona):
    text, files = export_workflow(workflow, source=None, bundle=True)
    from ting.domain.workflow_document import load_workflow_document

    changed_local = replace(
        persona, definition={**persona.definition, "system_prompt_template": "New"}
    )
    source = Source(changed_local)
    plan = plan_workflow_import(
        load_workflow_document(text), files, source=source, mappings={}, bindings={}
    )
    assert not plan.errors
    assert plan.personas[0]["status"] == "bundled"
    assert plan.persona_definitions["reviewer"] == persona.to_dict()
    assert source.load_portable("reviewer", "initial") == changed_local
    assert plan.document.graph == workflow.graph
    assert "owner_id" not in text
    assert "tenant-a" not in text


def test_standalone_missing_conflicting_and_exact_personas(workflow, persona):
    document = document_from_workflow(workflow)
    missing = plan_workflow_import(document, {}, source=Source(), mappings={}, bindings={})
    assert missing.personas[0]["status"] == "missing"
    assert missing.errors
    changed = replace(persona, definition={**persona.definition, "system_prompt_template": "New"})
    conflict = plan_workflow_import(document, {}, source=Source(changed), mappings={}, bindings={})
    assert conflict.personas[0]["status"] == "conflict"
    matched = plan_workflow_import(document, {}, source=Source(persona), mappings={}, bindings={})
    assert matched.personas[0]["status"] == "reuse"
    assert not matched.errors


def test_explicit_mapping_updates_dependency_and_preserves_alias(workflow, persona):
    candidate = replace(
        persona, id="local-reviewer", definition={**persona.definition, "name": "local-reviewer"}
    )
    plan = plan_workflow_import(
        document_from_workflow(workflow),
        {},
        source=Source(persona, candidate),
        mappings={"reviewer": "local-reviewer"},
        bindings={},
    )
    assert not plan.errors
    assert plan.document.persona_dependencies["reviewer"].id == "local-reviewer"
    assert plan.document.graph == workflow.graph
    assert plan.personas[0]["status"] == "mapped"


def test_incompatible_mapping_is_rejected(workflow, persona):
    candidate = replace(persona, id="local-reviewer", definition={"name": "local-reviewer"})
    plan = plan_workflow_import(
        document_from_workflow(workflow),
        {},
        source=Source(persona, candidate),
        mappings={"reviewer": "local-reviewer"},
        bindings={},
    )
    assert plan.errors
    assert plan.personas[0]["status"] == "conflict"


def test_tampered_bundled_persona_is_rejected(workflow, persona):
    document = document_from_workflow(workflow)
    document = replace(
        document,
        persona_dependencies={
            "reviewer": replace(
                document.persona_dependencies["reviewer"], path="personas/reviewer.yaml"
            )
        },
    )
    changed = replace(
        persona, definition={**persona.definition, "system_prompt_template": "Tampered"}
    )
    with pytest.raises(ValueError, match="digest conflict"):
        plan_workflow_import(
            document,
            {"personas/reviewer.yaml": portable_persona_yaml(changed)},
            source=Source(persona),
            mappings={},
            bindings={},
        )


def test_bindings_are_explicit_and_remove_source_connection_details(workflow):
    graph = {
        **workflow.graph,
        "nodes": [
            *workflow.graph["nodes"],
            {
                "id": "memory",
                "kind": "resource",
                "resourceType": "mimir",
                "registryEntryId": "source-memory",
                "url": "https://source.invalid",
                "adapter": "custom.Adapter",
                "kwargs": {"mount": "source"},
            },
        ],
    }
    document = document_from_workflow(replace(workflow, graph=graph))
    draft = plan_workflow_import(document, {}, source=None, mappings={}, bindings={})
    assert draft.requirements[0]["resolved"] is False
    mapped = plan_workflow_import(
        document, {}, source=None, mappings={}, bindings={"memory": "local-memory"}
    )
    assert mapped.requirements[0]["resolved"] is True
    node = mapped.document.graph["nodes"][-1]
    assert node["registryEntryId"] == "local-memory"
    assert not {"url", "adapter", "kwargs"} & node.keys()


def test_export_refuses_inline_credentials(workflow):
    unsafe = replace(workflow, graph={**workflow.graph, "config": {"api_key": "secret"}})
    with pytest.raises(ValueError, match="credential"):
        export_workflow(unsafe, source=None, bundle=True)


@pytest.mark.parametrize("key", ["token", "auth_token", "clientSecret", "secret", "signing_key"])
def test_export_rejects_structured_secrets(workflow, key):
    unsafe = replace(workflow, graph={**workflow.graph, "kwargs": {key: "secret-value"}})
    with pytest.raises(ValueError, match="credential"):
        export_workflow(unsafe, source=None, bundle=True)


def test_export_preserves_safe_secret_environment_references(workflow):
    graph = {**workflow.graph, "secretKwargsEnv": {"api_key": "OPENAI_API_KEY"}}
    text, _ = export_workflow(replace(workflow, graph=graph), source=None, bundle=True)
    assert "OPENAI_API_KEY" in text


def test_missing_declared_bundle_persona_is_not_replaced_with_local(workflow, persona):
    document = document_from_workflow(workflow)
    document = replace(
        document,
        persona_dependencies={
            "reviewer": replace(
                document.persona_dependencies["reviewer"],
                path="personas/reviewer.yaml",
            )
        },
    )
    with pytest.raises(ValueError, match="Declared bundled persona file is missing"):
        plan_workflow_import(document, {}, source=Source(persona), mappings={}, bindings={})


def _upload(workflow):
    text, files = export_workflow(workflow, source=None, bundle=True)
    content = WorkflowBundleCodec.write(text, files)
    return {"filename": "review.zip", "content": base64.b64encode(content).decode()}


def test_api_preview_apply_round_trip_assigns_importer_ownership(workflow):
    repo = InMemoryWorkflowRepository()
    client = _make_client(repo)
    body = _upload(workflow)
    headers = _headers(user_id="bob")
    preview = client.post("/api/v1/ting/workflows/imports/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_apply"] is True
    assert not repo._workflows
    applied = client.post(
        "/api/v1/ting/workflows/imports/apply",
        headers=headers,
        json={**body, "preview_digest": preview.json()["preview_digest"]},
    )
    assert applied.status_code == 201, applied.text
    saved = next(iter(repo._workflows.values()))
    assert saved.id != workflow.id
    assert saved.owner_id == "bob"
    assert saved.scope == WorkflowScope.USER
    assert saved.persona_definitions == workflow.persona_definitions
    assert saved.graph == workflow.graph
    exported = client.get(
        f"/api/v1/ting/workflows/{saved.id}/export?format=bundle", headers=headers
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"


def test_api_apply_requires_fresh_preview(workflow):
    repo = InMemoryWorkflowRepository()
    client = _make_client(repo)
    body = _upload(workflow)
    response = client.post(
        "/api/v1/ting/workflows/imports/apply", headers=_headers(user_id="bob"), json=body
    )
    assert response.status_code == 409
    assert not repo._workflows


def test_missing_bundle_member_is_rejected_before_registry_lookup(workflow):
    client = _make_client(InMemoryWorkflowRepository())
    client.app.state.volundr_factory = object()
    text, _ = export_workflow(workflow, source=None, bundle=True)
    response = client.post(
        "/api/v1/ting/workflows/imports/preview",
        headers=_headers(user_id="bob"),
        json={
            "filename": "incomplete.zip",
            "content": base64.b64encode(WorkflowBundleCodec.write(text, {})).decode(),
        },
    )
    assert response.status_code == 422
    assert "Declared persona file is missing" in response.json()["detail"]


def test_private_workflow_export_is_not_public(workflow):
    client = _make_client(InMemoryWorkflowRepository([workflow]))
    response = client.get(
        f"/api/v1/ting/workflows/{workflow.id}/export", headers=_headers(user_id="bob")
    )
    assert response.status_code == 404


def test_stale_workflow_save_is_rejected(workflow):
    repo = InMemoryWorkflowRepository([replace(workflow, revision="revision-2")])
    client = _make_client(repo)
    response = client.put(
        f"/api/v1/ting/workflows/{workflow.id}",
        headers=_headers(user_id="alice"),
        json={"name": "Changed", "expected_revision": "revision-1"},
    )
    assert response.status_code == 409
    assert repo._workflows[workflow.id].name == workflow.name


def test_two_file_catalogs_export_import_and_restart(tmp_path, workflow):
    from ting.adapters.filesystem_workflows import FilesystemWorkflowRepository

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    source_client = _make_client(FilesystemWorkflowRepository(str(source_dir)))
    target_client = _make_client(FilesystemWorkflowRepository(str(target_dir)))
    headers = _headers(user_id="alice")

    def import_bundle(client, body):
        preview = client.post("/api/v1/ting/workflows/imports/preview", headers=headers, json=body)
        assert preview.status_code == 200, preview.text
        response = client.post(
            "/api/v1/ting/workflows/imports/apply",
            headers=headers,
            json={**body, "preview_digest": preview.json()["preview_digest"]},
        )
        assert response.status_code == 201, response.text
        return response.json()

    first = import_bundle(source_client, _upload(workflow))
    exported = source_client.get(
        f"/api/v1/ting/workflows/{first['id']}/export?format=bundle",
        headers=headers,
    )
    assert exported.status_code == 200, exported.text
    second = import_bundle(
        target_client,
        {
            "filename": "workflow.zip",
            "content": base64.b64encode(exported.content).decode(),
        },
    )
    restarted = _make_client(FilesystemWorkflowRepository(str(target_dir)))
    saved = restarted.get(f"/api/v1/ting/workflows/{second['id']}", headers=headers)
    assert saved.status_code == 200
    assert saved.json()["graph"] == workflow.graph
    assert saved.json()["persona_dependencies"] == second["persona_dependencies"]
    assert saved.json()["revision"] == second["revision"]
    assert len(list(target_dir.glob("*.yaml"))) == 1


def test_import_update_cannot_overwrite_another_owners_workflow(workflow):
    repo = InMemoryWorkflowRepository([workflow])
    client = _make_client(repo)
    response = client.post(
        "/api/v1/ting/workflows/imports/preview",
        headers=_headers(user_id="bob"),
        json={**_upload(workflow), "mode": "update", "workflow_id": str(workflow.id)},
    )
    assert response.status_code == 404
    assert repo._workflows[workflow.id] == workflow


def test_import_preview_detects_persona_change_before_apply(workflow, persona):
    repo = InMemoryWorkflowRepository()
    client = _make_client(repo)
    source = Source(persona)
    client.app.state.persona_source = source
    text = dump_workflow_document(workflow)
    body = {"filename": "workflow.yaml", "content": base64.b64encode(text.encode()).decode()}
    headers = _headers(user_id="alice")
    preview = client.post("/api/v1/ting/workflows/imports/preview", headers=headers, json=body)
    assert preview.status_code == 200
    assert preview.json()["can_apply"]
    source.documents.clear()
    response = client.post(
        "/api/v1/ting/workflows/imports/apply",
        headers=headers,
        json={**body, "preview_digest": preview.json()["preview_digest"]},
    )
    assert response.status_code == 422
    assert not repo._workflows


def test_unresolved_imported_bindings_block_launch(workflow):
    from tests.test_ting.test_workflows_api import RecordingVolundrFactory, RecordingVolundrPort

    port = RecordingVolundrPort()
    blocked = replace(
        workflow,
        requirements=[
            {
                "id": "memory",
                "kind": "mimir",
                "message": "Select local memory",
                "resolved": False,
            }
        ],
    )
    client = _make_client(
        InMemoryWorkflowRepository([blocked]), volundr_factory=RecordingVolundrFactory([port])
    )
    response = client.post(
        f"/api/v1/ting/workflows/{workflow.id}/launch",
        headers=_headers(user_id="alice"),
        json={"prompt": "Review"},
    )
    assert response.status_code == 422
    assert not port.requests


def test_copy_preserves_private_scoped_personas(workflow):
    repo = InMemoryWorkflowRepository(
        [replace(workflow, read_only=True, scope=WorkflowScope.SYSTEM, owner_id=None)]
    )
    client = _make_client(repo)
    response = client.post(
        "/api/v1/ting/workflows",
        headers=_headers(user_id="bob"),
        json={"name": "My copy", "copy_from": str(workflow.id), "graph": workflow.graph},
    )
    assert response.status_code == 201, response.text
    copied = next(item for item in repo._workflows.values() if item.id != workflow.id)
    assert copied.persona_definitions == workflow.persona_definitions
    assert copied.owner_id == "bob"


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"filename": "bad.yaml", "content": "not base64!"}, 422),
        (
            {"filename": "bad.yaml", "content": base64.b64encode(b"schema_version: 99").decode()},
            422,
        ),
        ({"filename": "bad.zip", "content": base64.b64encode(b"not zip").decode()}, 422),
    ],
)
def test_invalid_uploads_return_actionable_client_errors(body, expected):
    client = _make_client(InMemoryWorkflowRepository())
    response = client.post(
        "/api/v1/ting/workflows/imports/preview", headers=_headers(user_id="alice"), json=body
    )
    assert response.status_code == expected


def test_apply_updates_existing_workflow_without_changing_owner(workflow):
    repo = InMemoryWorkflowRepository([replace(workflow, revision="v1")])
    client = _make_client(repo)
    body = {
        **_upload(replace(workflow, name="Updated")),
        "mode": "update",
        "workflow_id": str(workflow.id),
        "expected_revision": "v1",
    }
    headers = _headers(user_id="alice")
    preview = client.post("/api/v1/ting/workflows/imports/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    response = client.post(
        "/api/v1/ting/workflows/imports/apply",
        headers=headers,
        json={**body, "preview_digest": preview.json()["preview_digest"]},
    )
    assert response.status_code == 201, response.text
    assert len(repo._workflows) == 1
    assert repo._workflows[workflow.id].name == "Updated"
    assert repo._workflows[workflow.id].owner_id == "alice"
    assert repo._workflows[workflow.id].tenant_id == workflow.tenant_id


def test_refresh_persona_is_explicit_and_changes_only_future_definitions(workflow, persona):
    original_doc = workflow.persona_definitions["reviewer"]
    current = replace(
        persona,
        revision="new",
        definition={**persona.definition, "system_prompt_template": "Updated"},
    )
    repo = InMemoryWorkflowRepository([workflow])
    client = _make_client(repo)
    client.app.state.persona_source = Source(current)
    response = client.put(
        f"/api/v1/ting/workflows/{workflow.id}",
        headers=_headers(user_id="alice"),
        json={"name": workflow.name, "graph": workflow.graph, "refresh_personas": ["reviewer"]},
    )
    assert response.status_code == 200, response.text
    assert repo._workflows[workflow.id].persona_dependencies["reviewer"].revision == "new"
    assert workflow.persona_definitions["reviewer"] == original_doc


def test_create_pins_user_custom_persona_from_owner_registry(persona):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    custom = replace(
        persona,
        id="my-private-reviewer",
        definition={**persona.definition, "name": "my-private-reviewer"},
    )
    repo = InMemoryWorkflowRepository()
    client = _make_client(repo)
    adapter = SimpleNamespace(get_current_portable_persona=AsyncMock(return_value=custom))
    client.app.state.volundr_factory = SimpleNamespace(
        primary_for_principal=AsyncMock(return_value=adapter),
    )
    response = client.post(
        "/api/v1/ting/workflows",
        headers=_headers(user_id="alice"),
        json={
            "name": "Private review",
            "nodes": [
                {
                    "id": "review",
                    "kind": "stage",
                    "stageMembers": [{"personaId": "my-private-reviewer"}],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    saved = next(iter(repo._workflows.values()))
    assert saved.persona_definitions["my-private-reviewer"] == custom.to_dict()
    assert adapter.get_current_portable_persona.await_args.kwargs["principal"].user_id == "alice"
