"""Helpers for snapshotting compiled workflows onto sagas and dispatches."""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from typing import Any

import yaml

from ravn.domain.persona_document import (
    PersonaDependency,
    PersonaDependencyResolutionError,
    PortablePersonaSource,
    resolve_persona_dependencies,
    validate_persona_identifier,
)
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition
from ting.domain.workflow_document import load_workflow_document, workflow_document_revision


def pin_workflow_personas(
    workflow: WorkflowDefinition,
    persona_source: PortablePersonaSource | None,
) -> WorkflowDefinition:
    """Return a workflow with every referenced alias pinned to exact source.

    Existing scoped definitions are reused exactly. New aliases resolve their
    current content revision by stable persona id (the alias). Definitions for
    aliases removed from the graph are removed from the aggregate.
    """
    referenced = workflow_personas_from_snapshot({"graph": workflow.graph})
    aliases = [str(persona.get("name") or "").strip() for persona in referenced]
    aliases = [alias for alias in aliases if alias]
    existing_dependencies = getattr(workflow, "persona_dependencies", {}) or {}
    scoped_definitions = getattr(workflow, "persona_definitions", {}) or {}

    dependencies: dict[str, PersonaDependency] = {}
    definitions: dict[str, dict[str, Any]] = {}
    for alias in aliases:
        validate_persona_identifier(alias, field="Workflow persona alias")
        existing = existing_dependencies.get(alias)
        if existing is not None:
            resolved = resolve_persona_dependencies(
                {alias: existing},
                scoped_definitions,
                persona_source,
            )[alias]
            path = getattr(existing, "path", None)
            dependencies[alias] = PersonaDependency(
                id=resolved.id,
                revision=resolved.revision,
                digest=resolved.digest,
                path=path,
            )
            definitions[alias] = resolved.to_dict()
            continue

        if persona_source is None:
            raise PersonaDependencyResolutionError(
                f"Workflow persona alias '{alias}' is unpinned and no authoritative persona "
                "source is configured"
            )
        document = persona_source.load_current_portable(alias)
        if document is None:
            raise PersonaDependencyResolutionError(
                f"Workflow persona alias '{alias}' cannot be pinned because its current "
                "portable source is unavailable"
            )
        dependencies[alias] = document.dependency
        definitions[alias] = document.to_dict()

    return replace(
        workflow,
        persona_dependencies=dependencies,
        persona_definitions=definitions,
    )


def build_workflow_snapshot(
    workflow: WorkflowDefinition,
    *,
    persona_source: PortablePersonaSource | None = None,
) -> dict[str, Any]:
    """Build a serializable workflow snapshot for saga assignment and dispatch."""
    unresolved_requirements = [
        requirement
        for requirement in getattr(workflow, "requirements", [])
        if not requirement.get("resolved")
    ]
    if unresolved_requirements:
        raise ValueError("Resolve imported workflow bindings before creating an execution snapshot")

    graph = copy.deepcopy(workflow.graph)
    graph_snapshot = {"graph": graph}
    personas = workflow_personas_from_snapshot(graph_snapshot)
    resource_nodes = workflow_resource_nodes_from_snapshot(graph_snapshot)
    resource_bindings = workflow_resource_bindings_from_snapshot(graph_snapshot)
    mimir = workflow_mimir_from_snapshot(graph_snapshot)
    raw_dependencies = getattr(workflow, "persona_dependencies", {}) or {}
    scoped_definitions = getattr(workflow, "persona_definitions", {}) or {}
    resolved_definitions = resolve_persona_dependencies(
        raw_dependencies,
        scoped_definitions,
        persona_source,
    )
    resolved_workflows = validate_workflow_dependency_closure(workflow)
    snapshot = {
        "schema_version": workflow.schema_version,
        "workflow_id": str(workflow.id),
        "name": workflow.name,
        "version": workflow.version,
        "scope": workflow.scope.value,
        "graph": graph,
        "personas": copy.deepcopy(personas),
        "resource_nodes": copy.deepcopy(resource_nodes),
        "resource_bindings": copy.deepcopy(resource_bindings),
        "mimir": copy.deepcopy(mimir),
        "workflow_dependencies": {
            alias: dependency.to_dict()
            for alias, dependency in workflow.workflow_dependencies.items()
        },
        "workflow_definitions": resolved_workflows,
    }
    if raw_dependencies:
        snapshot["persona_dependencies"] = {
            alias: (
                dependency.to_dict()
                if hasattr(dependency, "to_dict")
                else {
                    "id": dependency.id,
                    "revision": dependency.revision,
                    "digest": dependency.digest,
                    **({"path": dependency.path} if dependency.path is not None else {}),
                }
            )
            for alias, dependency in raw_dependencies.items()
        }
        snapshot["persona_definitions"] = {
            alias: document.to_dict() for alias, document in resolved_definitions.items()
        }
    return snapshot


def validate_workflow_dependency_closure(
    workflow: WorkflowDefinition,
) -> dict[str, dict[str, Any]]:
    """Validate and copy an immutable workflow-scoped dependency closure."""
    return _validate_workflow_dependency_scope(
        workflow.workflow_dependencies,
        workflow.workflow_definitions,
        lineage=(workflow.id,),
        location=f"workflow {workflow.id}",
    )


def _validate_workflow_dependency_scope(
    dependencies: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    *,
    lineage: tuple[Any, ...],
    location: str,
) -> dict[str, dict[str, Any]]:
    missing = set(dependencies) - set(definitions)
    undeclared = set(definitions) - set(dependencies)
    if missing:
        raise WorkflowDocumentError(
            f"{location} is missing scoped workflow definition(s): " + ", ".join(sorted(missing))
        )
    if undeclared:
        raise WorkflowDocumentError(
            f"{location} has undeclared scoped workflow definition(s): "
            + ", ".join(sorted(undeclared))
        )

    resolved: dict[str, dict[str, Any]] = {}
    for alias, dependency in dependencies.items():
        aggregate = definitions[alias]
        if not isinstance(aggregate, dict):
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} scoped definition must be a mapping"
            )
        expected_fields = {"document", "persona_definitions", "workflow_definitions"}
        if set(aggregate) != expected_fields:
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} scoped definition must contain exactly "
                "document, persona_definitions, and workflow_definitions"
            )
        raw_document = aggregate["document"]
        child_personas = aggregate["persona_definitions"]
        child_workflows = aggregate["workflow_definitions"]
        if not isinstance(raw_document, dict):
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} document must be a mapping"
            )
        if not isinstance(child_personas, dict) or not isinstance(child_workflows, dict):
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} scoped definitions must be mappings"
            )
        try:
            child = load_workflow_document(
                yaml.safe_dump(raw_document, sort_keys=False, allow_unicode=True)
            )
        except (TypeError, ValueError, yaml.YAMLError) as exc:
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} has an invalid scoped document: {exc}"
            ) from exc
        revision = workflow_document_revision(child)
        if (
            child.id != dependency.id
            or dependency.revision != revision
            or dependency.digest != revision
        ):
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} does not match exact pin "
                f"{dependency.id}@{dependency.revision} ({dependency.digest})"
            )
        if child.id in lineage:
            cycle = " -> ".join(str(item) for item in (*lineage, child.id))
            raise WorkflowDocumentError(f"Workflow dependency cycle: {cycle}")
        extra_personas = set(child_personas) - set(child.persona_dependencies)
        if extra_personas:
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} has undeclared persona definition(s): "
                + ", ".join(sorted(extra_personas))
            )
        try:
            resolved_personas = resolve_persona_dependencies(
                child.persona_dependencies,
                child_personas,
                None,
            )
        except PersonaDependencyResolutionError as exc:
            raise WorkflowDocumentError(
                f"{location} dependency {alias!r} persona closure is invalid: {exc}"
            ) from exc
        nested = _validate_workflow_dependency_scope(
            child.workflow_dependencies,
            child_workflows,
            lineage=(*lineage, child.id),
            location=f"{location} dependency {alias!r}",
        )
        resolved[alias] = {
            "document": copy.deepcopy(raw_document),
            "persona_definitions": {
                name: definition.to_dict() for name, definition in resolved_personas.items()
            },
            "workflow_definitions": nested,
        }
    return resolved


def workflow_name_from_snapshot(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None

    name = snapshot.get("name")
    if isinstance(name, str) and name:
        return name
    return None


def workflow_artifact_paths_from_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    slug: str,
) -> list[str]:
    """Resolve safe, exact artifact paths declared by a workflow graph."""
    if not snapshot:
        return []
    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []
    raw_paths = graph.get("artifactPaths") or graph.get("artifact_paths") or []
    paths: list[str] = []
    for raw_path in raw_paths:
        path = str(raw_path or "").strip().replace("{slug}", slug)
        if not path or path.startswith("/"):
            continue
        parts = [part for part in path.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            continue
        if path not in paths:
            paths.append(path)
    return paths


def workflow_stage_models_from_snapshot(snapshot: dict[str, Any] | None) -> list[str]:
    """Extract explicit stage-member models from a workflow snapshot graph."""
    if not snapshot:
        return []

    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []

    models: list[str] = []
    for node in list(graph.get("nodes") or []):
        if not isinstance(node, dict) or node.get("kind") != "stage":
            continue
        for member in list(node.get("stageMembers") or []):
            if not isinstance(member, dict):
                continue
            model = str(member.get("model") or "").strip()
            if model:
                models.append(model)
    return models


def workflow_personas_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract an ordered unique persona list from a workflow snapshot graph."""
    if not snapshot:
        return []

    personas = snapshot.get("personas")
    if isinstance(personas, list):
        normalized = [persona for persona in personas if isinstance(persona, dict)]
        if normalized:
            return normalized

    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []

    nodes = list(graph.get("nodes") or [])
    seen: set[str] = set()
    personas: list[dict[str, Any]] = []

    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") != "stage":
            continue

        stage_members = list(node.get("stageMembers") or [])
        if stage_members:
            for member in stage_members:
                persona = _persona_from_member(member)
                if persona is None:
                    continue
                if persona["name"] in seen:
                    continue
                seen.add(persona["name"])
                personas.append(persona)
            continue

        for persona_id in list(node.get("personaIds") or []):
            if not isinstance(persona_id, str) or not persona_id or persona_id in seen:
                continue
            seen.add(persona_id)
            personas.append({"name": persona_id})

    return personas


def workflow_runtime_personas_from_snapshot(
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return personas with their exact portable source documents attached."""
    personas = workflow_personas_from_snapshot(snapshot)
    if not snapshot:
        return personas
    dependencies = snapshot.get("persona_dependencies")
    definitions = snapshot.get("persona_definitions")
    if not isinstance(dependencies, dict) or not dependencies:
        return personas
    if not isinstance(definitions, dict):
        raise ValueError("Workflow snapshot is missing resolved persona definitions")

    runtime_personas: list[dict[str, Any]] = []
    for persona in personas:
        runtime_persona = dict(persona)
        alias = str(runtime_persona.get("name") or "").strip()
        validate_persona_identifier(alias, field="Workflow persona alias")
        if alias not in dependencies:
            raise ValueError(f"Workflow persona alias {alias!r} is not declared as a dependency")
        definition = definitions.get(alias)
        if not isinstance(definition, dict):
            raise ValueError(f"Workflow persona dependency {alias!r} has no resolved definition")
        runtime_persona["portable_definition"] = dict(definition)
        runtime_personas.append(runtime_persona)
    return runtime_personas


def workflow_resource_nodes_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract Mimir resource nodes from a workflow snapshot."""
    if not snapshot:
        return []

    resource_nodes = snapshot.get("resource_nodes")
    if isinstance(resource_nodes, list):
        normalized = [
            resource_node for resource_node in resource_nodes if isinstance(resource_node, dict)
        ]
        if normalized:
            return normalized

    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []

    nodes = list(graph.get("nodes") or [])
    return [
        node
        for node in nodes
        if isinstance(node, dict)
        and str(node.get("kind") or "") == "resource"
        and str(node.get("resourceType") or "") == "mimir"
    ]


def workflow_resource_bindings_from_snapshot(
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract resource bindings from a workflow snapshot."""
    if not snapshot:
        return []

    resource_bindings = snapshot.get("resource_bindings")
    if isinstance(resource_bindings, list):
        normalized = [binding for binding in resource_bindings if isinstance(binding, dict)]
        if normalized:
            return normalized

    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []

    raw = graph.get("resourceBindings") or graph.get("resource_bindings") or []
    return [binding for binding in raw if isinstance(binding, dict)]


def workflow_mimir_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Build the richer Mimir workload payload from workflow resource data."""
    if not snapshot:
        return {}

    cached = snapshot.get("mimir")
    if isinstance(cached, dict) and cached:
        return dict(cached)

    resource_nodes = workflow_resource_nodes_from_snapshot(snapshot)
    resource_bindings = workflow_resource_bindings_from_snapshot(snapshot)
    if not resource_nodes:
        return {}

    resource_mount_names: dict[str, str] = {}
    registry_refs: list[dict[str, Any]] = []
    ephemeral_locals: list[dict[str, Any]] = []

    for node in resource_nodes:
        resource_node_id = str(node.get("id") or "").strip()
        if not resource_node_id:
            continue

        mount_name = _resource_mount_name(node)
        resource_mount_names[resource_node_id] = mount_name
        categories = _string_list(node.get("categories"))

        if str(node.get("bindingMode") or "registry") == "ephemeral_local":
            ephemeral_locals.append(
                {
                    "resource_node_id": resource_node_id,
                    "mount_name": mount_name,
                    "label": str(node.get("label") or mount_name),
                    "seed_from_registry_id": _optional_string(node.get("seedFromRegistryId")),
                    "categories": categories,
                }
            )
            continue

        ref = {
            "resource_node_id": resource_node_id,
            "registry_entry_id": _optional_string(node.get("registryEntryId")),
            "mount_name": mount_name,
            "label": str(node.get("label") or mount_name),
            "categories": categories,
        }
        if adapter := _optional_string(node.get("adapter")):
            ref["adapter"] = adapter
            ref["kwargs"] = dict(node.get("kwargs") or {})
            ref["secret_kwargs_env"] = dict(node.get("secretKwargsEnv") or {})
        if path := _optional_string(node.get("path")):
            ref["path"] = path
        if url := _optional_string(node.get("url")):
            ref["url"] = url
        if role := _optional_string(node.get("role")):
            ref["role"] = role
        if auth_ref := _optional_string(
            node.get("authRef") if "authRef" in node else node.get("auth_ref")
        ):
            ref["auth_ref"] = auth_ref
        default_read_priority = (
            node.get("defaultReadPriority")
            if "defaultReadPriority" in node
            else node.get("default_read_priority")
        )
        if isinstance(default_read_priority, int):
            ref["default_read_priority"] = default_read_priority
        registry_refs.append(ref)

    bindings: list[dict[str, Any]] = []
    for binding in resource_bindings:
        resource_node_id = str(binding.get("resourceNodeId") or "").strip()
        mount_name = resource_mount_names.get(resource_node_id)
        if not mount_name:
            continue
        bindings.append(
            {
                "resource_node_id": resource_node_id,
                "mount_name": mount_name,
                "target_type": str(binding.get("targetType") or "workflow"),
                "target_id": str(binding.get("targetId") or ""),
                "access": str(binding.get("access") or "read"),
                "write_prefixes": _string_list(binding.get("writePrefixes")),
                "read_priority": _int_value(binding.get("readPriority"), default=10),
            }
        )

    mimir: dict[str, Any] = {
        "registry_refs": registry_refs,
        "ephemeral_locals": ephemeral_locals,
        "bindings": bindings,
    }
    if registry_refs or ephemeral_locals:
        default_mount = (
            ephemeral_locals[0]["mount_name"]
            if ephemeral_locals
            else registry_refs[0]["mount_name"]
        )
        mimir["default_mounts"] = [default_mount]
    return mimir


def _persona_from_member(member: Any) -> dict[str, Any] | None:
    if not isinstance(member, dict):
        return None

    persona_id = member.get("personaId")
    if not isinstance(persona_id, str) or not persona_id:
        return None

    persona = {"name": persona_id}
    budget = member.get("budget")
    if isinstance(budget, int) and budget > 0:
        persona["iteration_budget"] = budget
    consumes_event_types = member.get("consumesEventTypes")
    if not isinstance(consumes_event_types, list):
        consumes_event_types = member.get("consumes_event_types")
    if isinstance(consumes_event_types, list):
        normalized = [
            str(event_type).strip()
            for event_type in consumes_event_types
            if str(event_type).strip()
        ]
        if normalized:
            persona["consumes_event_types"] = normalized
    model = str(member.get("model") or "").strip()
    if model:
        persona["model"] = model
    system_prompt_extra = str(
        member.get("systemPromptExtra") or member.get("system_prompt_extra") or ""
    ).strip()
    if system_prompt_extra:
        persona["system_prompt_extra"] = system_prompt_extra
    return persona


def _resource_mount_name(node: dict[str, Any]) -> str:
    registry_entry_id = _optional_string(node.get("registryEntryId"))
    if registry_entry_id:
        return registry_entry_id

    label = _optional_string(node.get("label"))
    if label:
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        if slug:
            return slug

    node_id = _optional_string(node.get("id"))
    if node_id:
        return node_id
    return "mimir-resource"


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _int_value(value: Any, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default
