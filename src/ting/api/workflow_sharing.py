"""Authorized preview/apply and file export endpoints for workflows."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_principal
from ting.adapters.workflow_bundle import WorkflowBundleCodec
from ting.api.workflow_bindings import binding_errors
from ting.api.workflow_personas import authoring_persona_source
from ting.domain.models import WorkflowScope
from ting.domain.services.workflow_sharing import export_workflow, plan_workflow_import
from ting.domain.workflow_document import document_from_workflow, load_workflow_document
from ting.domain.workflow_snapshot import workflow_personas_from_snapshot
from ting.ports.workflow_repository import WorkflowRepository


class WorkflowImportBody(BaseModel):
    content: str
    filename: str = Field(min_length=1, max_length=255)
    mappings: dict[str, str] = Field(default_factory=dict)
    bindings: dict[str, str] = Field(default_factory=dict)
    mode: Literal["copy", "update"] = "copy"
    workflow_id: UUID | None = None
    expected_revision: str | None = None
    preview_digest: str | None = None


def create_workflow_sharing_router() -> APIRouter:
    # The parent router imports this factory lazily to share its CRUD contract.
    from ting.api.workflows import (
        WorkflowResponse,
        _assert_can_manage_existing,
        _assert_revision,
        _can_view_workflow,
        _save_workflow,
        _to_response,
        resolve_workflow_repo,
    )

    router = APIRouter()

    async def prepare(body, request, principal, repo):
        limits = request.app.state.settings.workflow_import
        if len(body.content) > ((limits.max_upload_bytes + 2) // 3) * 4:
            raise HTTPException(status_code=413, detail="Workflow upload exceeds the size limit")
        try:
            raw = base64.b64decode(body.content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(
                status_code=422, detail="Upload content must be valid base64"
            ) from exc
        codec = WorkflowBundleCodec(**limits.model_dump())
        try:
            contents = codec.read(raw, filename=body.filename)
            document = load_workflow_document(contents.workflow)
            local_workflows = {}
            needed = set()

            async def collect(doc, ancestors=(), aggregate=None):
                if doc.id in ancestors or len(ancestors) >= 32:
                    raise ValueError("Cyclic or excessively nested workflow dependencies")
                for alias, pin in doc.persona_dependencies.items():
                    if (
                        pin.path
                        and pin.path not in contents.files
                        and alias not in (aggregate or {}).get("persona_definitions", {})
                    ):
                        raise ValueError(f"Declared persona file is missing: {pin.path}")
                needed.update(
                    pin.id
                    for alias, pin in doc.persona_dependencies.items()
                    if pin.path not in contents.files
                    and alias not in (aggregate or {}).get("persona_definitions", {})
                )
                for alias, pin in doc.workflow_dependencies.items():
                    child_aggregate = (aggregate or {}).get("workflow_definitions", {}).get(alias)
                    if child_aggregate:
                        child_doc = load_workflow_document(json.dumps(child_aggregate["document"]))
                    elif pin.path:
                        if pin.path not in contents.files:
                            raise ValueError(f"Declared workflow file is missing: {pin.path}")
                        child_doc = load_workflow_document(contents.files[pin.path])
                    else:
                        child = await repo.get_workflow(pin.id)
                        if child is None or not _can_view_workflow(child, principal):
                            continue
                        local_workflows[str(pin.id)] = child
                        child_doc = document_from_workflow(child)
                        child_aggregate = {
                            "persona_definitions": child.persona_definitions,
                            "workflow_definitions": child.workflow_definitions,
                        }
                    await collect(child_doc, (*ancestors, doc.id), child_aggregate)

            await collect(document)
            needed.update(body.mappings.values())
            source = await authoring_persona_source(request, principal, needed)
            plan = plan_workflow_import(
                document,
                contents.files,
                source=source,
                mappings=body.mappings,
                bindings=body.bindings,
                local_workflows=local_workflows,
            )
            errors = binding_errors(
                body.bindings,
                registry_path=request.app.state.settings.dispatch.flock.mimir_registry_path,
                tenant_id=principal.tenant_id,
            )
            if errors:
                plan = replace(plan, errors=[*plan.errors, *errors])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        existing = None
        if body.mode == "update":
            target_id = body.workflow_id or document.id
            existing = await repo.get_workflow(target_id)
            if existing is None or not _can_view_workflow(existing, principal):
                raise HTTPException(status_code=404, detail="Workflow not found")
            _assert_can_manage_existing(existing, principal)
            _assert_revision(existing, body.expected_revision)
        preview = plan.preview(
            context={
                "owner": principal.user_id,
                "tenant": principal.tenant_id,
                "mode": body.mode,
                "target": str(existing.id) if existing else None,
                "revision": existing.revision if existing else None,
            }
        )
        return plan, preview, existing

    @router.post("/imports/preview")
    async def preview_import(
        body: WorkflowImportBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> dict[str, Any]:
        _, preview, _ = await prepare(body, request, principal, repo)
        return preview

    @router.post("/imports/apply", response_model=WorkflowResponse, status_code=201)
    async def apply_import(
        body: WorkflowImportBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        plan, preview, existing = await prepare(body, request, principal, repo)
        if not preview["can_apply"]:
            raise HTTPException(status_code=422, detail=preview["errors"])
        if not body.preview_digest or body.preview_digest != preview["preview_digest"]:
            raise HTTPException(status_code=409, detail="Import preview changed; preview again")
        document = replace(plan.document, id=existing.id if existing else uuid4())
        workflow = document.to_workflow(
            scope=existing.scope if existing else WorkflowScope.USER,
            owner_id=existing.owner_id if existing else principal.user_id,
            tenant_id=existing.tenant_id if existing else principal.tenant_id,
            created_at=existing.created_at if existing else None,
            updated_at=datetime.now(UTC),
            revision=existing.revision if existing else None,
            persona_definitions=plan.persona_definitions,
            workflow_definitions=plan.workflow_definitions,
        )
        workflow = replace(workflow, requirements=plan.requirements)
        saved = await _save_workflow(repo, workflow)
        return _to_response(saved)

    @router.get("/{workflow_id}/export")
    async def download_workflow(
        workflow_id: UUID,
        request: Request,
        format: Literal["yaml", "bundle"] = Query(default="yaml"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> Response:
        workflow = await repo.get_workflow(workflow_id)
        if workflow is None or not _can_view_workflow(workflow, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")
        try:
            aliases = {
                item["name"] for item in workflow_personas_from_snapshot({"graph": workflow.graph})
            }
            needed = {
                workflow.persona_dependencies[alias].id
                if alias in workflow.persona_dependencies
                else alias
                for alias in aliases
                if alias not in workflow.persona_definitions
            }
            source = await authoring_persona_source(request, principal, needed)
            yaml_text, personas = export_workflow(
                workflow,
                source=source,
                bundle=format == "bundle",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if format == "bundle":
            return Response(
                content=WorkflowBundleCodec.write(yaml_text, personas),
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{workflow.id}.zip"'},
            )
        return Response(
            content=yaml_text,
            media_type="application/yaml",
            headers={"Content-Disposition": f'attachment; filename="{workflow.id}.yaml"'},
        )

    return router
