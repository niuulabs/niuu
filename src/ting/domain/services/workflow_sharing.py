"""Pure planning for portable workflow imports and exports.

Imports retain exact persona source documents in the workflow's own scope. No
preview or import writes to the caller's global persona registry.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ravn.domain.persona_document import (
    PortablePersonaDefinition,
    PortablePersonaSource,
    parse_portable_persona,
    persona_mapping_compatibility_issues,
    portable_persona_yaml,
    resolve_persona_dependencies,
)
from ting.domain.models import PersonaDependency, WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import (
    WorkflowDocument,
    document_from_workflow,
    dump_workflow_document,
    load_workflow_document,
    workflow_document_payload,
    workflow_document_revision,
)
from ting.domain.workflow_snapshot import pin_workflow_personas


@dataclass(frozen=True)
class WorkflowImportPlan:
    document: WorkflowDocument
    persona_definitions: dict[str, dict[str, Any]]
    personas: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    errors: list[str]
    workflow_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: list[dict[str, Any]] = field(default_factory=list)

    def preview(self, *, context: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "workflow": {
                "id": str(self.document.id),
                "name": self.document.name,
                "description": self.document.description,
                "version": self.document.version,
            },
            "personas": self.personas,
            "workflows": self.workflows,
            "requirements": self.requirements,
            "errors": self.errors,
            "can_apply": not self.errors,
        }
        fingerprint = {
            "preview": payload,
            "document": dump_workflow_document(self.document),
            "definitions": self.persona_definitions,
            "workflow_definitions": self.workflow_definitions,
            "context": context,
        }
        encoded = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()
        payload["preview_digest"] = hashlib.sha256(encoded).hexdigest()
        return payload


def plan_workflow_import(
    document: WorkflowDocument,
    files: dict[str, str],
    *,
    source: PortablePersonaSource | None,
    mappings: dict[str, str],
    bindings: dict[str, str],
    local_workflows: dict[str, WorkflowDefinition] | None = None,
) -> WorkflowImportPlan:
    """Resolve the entire closure into the importing workflow's private scope.

    Mapping names use slash-separated dependency aliases for child scopes. Only
    the caller's authorized catalog entries may be supplied as local workflows.
    """
    consumed: set[str] = set()
    used_mappings: set[str] = set()
    used_bindings: set[str] = set()

    def visit(doc, scope, ancestors, embedded=None):
        if str(doc.id) in ancestors:
            raise ValueError(f"Cyclic workflow dependency: {scope or doc.name}")
        if len(ancestors) >= 32:
            raise ValueError("Workflow dependency nesting exceeds 32 levels")
        ancestors = (*ancestors, str(doc.id))
        if embedded:
            doc = replace(
                doc,
                persona_dependencies={
                    alias: replace(pin, path=None)
                    if alias in embedded.get("persona_definitions", {})
                    else pin
                    for alias, pin in doc.persona_dependencies.items()
                },
            )
        scoped_mappings = {}
        scoped_bindings = {}
        for alias in doc.persona_dependencies:
            key = scope + alias
            if key in mappings:
                scoped_mappings[alias] = mappings[key]
                used_mappings.add(key)
        for node in doc.graph.get("nodes", []):
            key = scope + str(node["id"])
            if key in bindings:
                scoped_bindings[str(node["id"])] = bindings[key]
                used_bindings.add(key)
        persona_files = {}
        for pin in doc.persona_dependencies.values():
            if pin.path and pin.path in files:
                persona_files[pin.path] = files[pin.path]
                consumed.add(pin.path)
        # Scoped content loaded from an authorized catalog is an exact source,
        # never an implicit update to the global persona registry.
        scoped_source = _ScopedPersonaSource(embedded or {}, source)
        result = _plan_single_workflow_import(
            doc,
            persona_files,
            source=scoped_source,
            mappings=scoped_mappings,
            bindings=scoped_bindings,
        )
        definitions = {}
        pins = dict(doc.workflow_dependencies)
        personas = [{**item, "alias": scope + item["alias"]} for item in result.personas]
        requirements = [{**item, "id": scope + item["id"]} for item in result.requirements]
        errors = [scope + error for error in result.errors]
        workflows = []
        for alias, pin in doc.workflow_dependencies.items():
            child_scope = scope + alias + "/"
            aggregate = (embedded or {}).get("workflow_definitions", {}).get(alias)
            if aggregate:
                child_doc = load_workflow_document(json.dumps(aggregate["document"]))
                child_embedded = aggregate
            elif pin.path:
                if pin.path not in files:
                    raise ValueError(f"Declared bundled workflow file is missing: {pin.path}")
                consumed.add(pin.path)
                child_doc = load_workflow_document(files[pin.path])
                child_embedded = None
            else:
                local = (local_workflows or {}).get(str(pin.id))
                if local is None:
                    errors.append(
                        f"{scope}{alias}: Pinned workflow is unavailable; import a bundle"
                    )
                    workflows.append(
                        {"alias": scope + alias, "id": str(pin.id), "status": "missing"}
                    )
                    continue
                child_doc = document_from_workflow(local)
                child_embedded = {
                    "persona_definitions": local.persona_definitions,
                    "workflow_definitions": local.workflow_definitions,
                }
            revision = workflow_document_revision(child_doc)
            if child_doc.id != pin.id or revision != pin.revision or revision != pin.digest:
                raise ValueError(f"Workflow dependency pin mismatch: {scope}{alias}")
            child = visit(child_doc, child_scope, ancestors, child_embedded)
            new_revision = workflow_document_revision(child.document)
            pins[alias] = replace(pin, path=None, revision=new_revision, digest=new_revision)
            definitions[alias] = {
                "document": workflow_document_payload(child.document),
                "persona_definitions": child.persona_definitions,
                "workflow_definitions": child.workflow_definitions,
            }
            workflows.append(
                {
                    "alias": scope + alias,
                    "id": str(pin.id),
                    "revision": new_revision,
                    "status": "bundled",
                }
            )
            workflows.extend(child.workflows)
            personas.extend(child.personas)
            requirements.extend(child.requirements)
            errors.extend(child.errors)
        return replace(
            result,
            document=replace(result.document, workflow_dependencies=pins),
            workflow_definitions=definitions,
            workflows=workflows,
            personas=personas,
            requirements=requirements,
            errors=errors,
        )

    plan = visit(document, "", ())
    errors = list(plan.errors)
    if set(files) - consumed:
        errors.append(
            "Bundle contains undeclared files: " + ", ".join(sorted(set(files) - consumed))
        )
    errors.extend(
        f"Persona mapping refers to unknown alias: {key}"
        for key in sorted(set(mappings) - used_mappings)
    )
    errors.extend(
        f"Resource mapping refers to unknown node: {key}"
        for key in sorted(set(bindings) - used_bindings)
    )
    return replace(plan, errors=errors)


class _ScopedPersonaSource:
    def __init__(self, aggregate, source):
        self._definitions = aggregate.get("persona_definitions", {})
        self._source = source

    def load_portable(self, persona_id, revision):
        for value in self._definitions.values():
            definition = PortablePersonaDefinition.from_dict(value)
            if definition.id == persona_id and definition.revision == revision:
                return definition
        return self._source.load_portable(persona_id, revision) if self._source else None

    def load_current_portable(self, persona_id):
        return self._source.load_current_portable(persona_id) if self._source else None


def _plan_single_workflow_import(
    document: WorkflowDocument,
    files: dict[str, str],
    *,
    source: PortablePersonaSource | None,
    mappings: dict[str, str],
    bindings: dict[str, str],
) -> WorkflowImportPlan:
    dependencies = dict(document.persona_dependencies)
    definitions: dict[str, dict[str, Any]] = {}
    personas: list[dict[str, Any]] = []
    errors: list[str] = []
    consumed_files: set[str] = set()
    for unknown in sorted(set(mappings) - set(dependencies)):
        errors.append(f"Persona mapping refers to unknown alias: {unknown}")
    for alias, dependency in dependencies.items():
        bundled = None
        if dependency.path:
            if dependency.path not in files:
                raise ValueError(f"Declared bundled persona file is missing: {dependency.path}")
            consumed_files.add(dependency.path)
            bundled = parse_portable_persona(files[dependency.path])
            # Corrupt bundled content must not be ignored even if a local copy exists.
            resolve_persona_dependencies({alias: asdict(dependency)}, {alias: bundled}, None)
        dependencies[alias] = replace(dependency, path=None)
        local = source.load_portable(dependency.id, dependency.revision) if source else None
        if local is None and source is not None:
            local = source.load_current_portable(dependency.id)
        item: dict[str, Any] = {"alias": alias, **asdict(dependency)}
        if alias in mappings:
            candidate = source.load_current_portable(mappings[alias]) if source else None
            _map_persona(alias, dependency, bundled, local, candidate, item, errors)
            if candidate is not None and item["status"] == "mapped":
                dependencies[alias] = PersonaDependency(
                    id=candidate.id,
                    revision=candidate.revision,
                    digest=candidate.digest,
                )
                definitions[alias] = candidate.to_dict()
            personas.append(item)
            continue
        if (
            local is not None
            and local.digest == dependency.digest
            and local.revision == dependency.revision
            and local.id == dependency.id
        ):
            item.update(status="reuse", message="Exact persona content is available locally")
            definitions[alias] = local.to_dict()
        elif bundled is not None:
            # A distinct revision remains isolated rather than overwriting global state.
            item.update(
                status="bundled",
                message=(
                    "Use the bundled persona in this workflow only; the local persona differs"
                    if local is not None
                    else "Install the bundled persona for this workflow"
                ),
            )
            definitions[alias] = bundled.to_dict()
        else:
            state = "conflict" if local is not None else "missing"
            message = (
                "Local persona revision differs; map explicitly or import a bundle"
                if local is not None
                else "Persona revision is missing; map it or import a bundle"
            )
            item.update(status=state, message=message)
            errors.append(f"{alias}: {message}")
        if alias in definitions:
            item["definition"] = definitions[alias]["definition"]
        personas.append(item)
    unexpected = set(files) - consumed_files
    if unexpected:
        errors.append("Bundle contains undeclared persona files: " + ", ".join(sorted(unexpected)))
    graph, requirements = _bind_resources(document.graph, bindings, errors)
    return WorkflowImportPlan(
        document=replace(document, persona_dependencies=dependencies, graph=graph),
        persona_definitions=definitions,
        personas=personas,
        requirements=requirements,
        errors=errors,
    )


def _map_persona(
    alias: str,
    dependency: PersonaDependency,
    bundled: PortablePersonaDefinition | None,
    local: PortablePersonaDefinition | None,
    candidate: PortablePersonaDefinition | None,
    item: dict[str, Any],
    errors: list[str],
) -> None:
    if candidate is None:
        item.update(status="missing", message="Selected local persona is unavailable")
        errors.append(f"{alias}: Selected local persona is unavailable")
        return
    original = bundled or (local if local and local.digest == dependency.digest else None)
    issues = persona_mapping_compatibility_issues(original, candidate) if original else []
    if issues:
        message = "; ".join(issues)
        item.update(status="conflict", message=message)
        errors.append(f"{alias}: {message}")
        return
    item.update(
        status="mapped",
        id=candidate.id,
        revision=candidate.revision,
        digest=candidate.digest,
        definition=candidate.definition,
        message=(
            "Explicit local mapping; persona dependency pin will be updated"
            if original
            else "Explicit local mapping; original definition unavailable for comparison"
        ),
    )


def _bind_resources(
    original_graph: dict[str, Any],
    bindings: dict[str, str],
    errors: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    graph = copy.deepcopy(original_graph)
    requirements: list[dict[str, Any]] = []
    resource_ids: set[str] = set()
    for node in graph.get("nodes", []):
        if node.get("kind") != "resource" or node.get("resourceType") != "mimir":
            continue
        node_id = str(node["id"])
        resource_ids.add(node_id)
        if node.get("bindingMode") == "ephemeral_local" and not node.get("seedFromRegistryId"):
            continue
        binding = bindings.get(node_id, "").strip()
        requirements.append(
            {
                "id": node_id,
                "kind": "mimir",
                "message": f"Select a local memory resource for {node.get('label') or node_id}",
                "resolved": bool(binding),
                "binding": binding or None,
                "source_binding": node.get("registryEntryId") or node.get("seedFromRegistryId"),
            }
        )
        if not binding:
            continue
        for key in ("path", "url", "adapter", "kwargs", "secretKwargsEnv", "authRef", "auth_ref"):
            node.pop(key, None)
        if node.get("bindingMode") == "ephemeral_local":
            node["seedFromRegistryId"] = binding
        else:
            node["bindingMode"] = "registry"
            node["registryEntryId"] = binding
    for unknown in sorted(set(bindings) - resource_ids):
        errors.append(f"Resource mapping refers to unknown node: {unknown}")
    return graph, requirements


def export_workflow(
    workflow: WorkflowDefinition,
    *,
    source: PortablePersonaSource | None,
    bundle: bool,
) -> tuple[str, dict[str, str]]:
    """Produce portable files while preserving pins and scoped persona content."""
    return _export_workflow(workflow, source=source, bundle=bundle, ancestors=())


def _export_workflow(workflow, *, source, bundle, ancestors):
    if workflow.id in ancestors:
        raise ValueError("Cyclic workflow dependency")
    if len(ancestors) >= 32:
        raise ValueError("Workflow dependency nesting exceeds 32 levels")
    reject_inline_workflow_secrets(workflow.graph)
    workflow = pin_workflow_personas(workflow, source)
    resolved = resolve_persona_dependencies(
        {alias: asdict(pin) for alias, pin in workflow.persona_dependencies.items()},
        workflow.persona_definitions,
        source,
    )
    document = document_from_workflow(workflow)
    dependencies = {}
    files = {}
    for alias, dependency in sorted(document.persona_dependencies.items()):
        persona_yaml = portable_persona_yaml(resolved[alias])
        path = (
            f"personas/{hashlib.sha256(persona_yaml.encode()).hexdigest()}.yaml" if bundle else None
        )
        dependencies[alias] = replace(dependency, path=path)
        if path:
            files[path] = persona_yaml
    child_pins = {}
    for alias, pin in document.workflow_dependencies.items():
        aggregate = workflow.workflow_definitions.get(alias)
        if not aggregate:
            raise ValueError(f"Pinned child workflow definition is unavailable: {alias}")
        child_doc = load_workflow_document(json.dumps(aggregate["document"]))
        revision = workflow_document_revision(child_doc)
        if child_doc.id != pin.id or pin.revision != revision or pin.digest != revision:
            raise ValueError(f"Workflow dependency pin mismatch: {alias}")
        child = child_doc.to_workflow(
            scope=WorkflowScope.USER,
            owner_id=None,
            persona_definitions=aggregate.get("persona_definitions", {}),
            workflow_definitions=aggregate.get("workflow_definitions", {}),
        )
        child_yaml, child_files = _export_workflow(
            child,
            source=source,
            bundle=bundle,
            ancestors=(*ancestors, workflow.id),
        )
        path = f"workflows/{pin.digest.removeprefix('sha256:')}.yaml" if bundle else None
        child_pins[alias] = replace(pin, path=path)
        if path:
            files[path] = child_yaml
            files.update(child_files)
    return dump_workflow_document(
        replace(document, persona_dependencies=dependencies, workflow_dependencies=child_pins)
    ), files


def reject_inline_workflow_secrets(value: Any) -> None:
    """Reject credential-bearing configuration instead of silently exporting it."""
    if isinstance(value, list):
        for item in value:
            reject_inline_workflow_secrets(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).lower().replace("-", "_")
        if normalized == "secret_kwargs_env":
            if not isinstance(item, dict) or any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                for name in item.values()
            ):
                raise ValueError(
                    "Secret environment references must contain environment variable names"
                )
            continue
        if (
            normalized
            in {
                "password",
                "api_key",
                "apikey",
                "access_token",
                "refresh_token",
                "client_secret",
                "private_key",
                "authorization",
                "bearer_token",
                "token",
                "auth_token",
                "secret",
                "credential",
                "credentials",
                "secret_key",
                "signing_key",
            }
            and item
        ):
            raise ValueError(
                f"Remove inline credential field {key!r} before exporting the workflow"
            )
        reject_inline_workflow_secrets(item)
