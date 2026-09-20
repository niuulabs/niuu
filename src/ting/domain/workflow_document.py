"""Canonical portable Ting workflow YAML contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

import yaml

from niuu.domain.workflow_evidence import validate_evidence_gate_nodes
from ravn.domain.persona_document import PersonaDocumentError, validate_persona_identifier
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import (
    PersonaDependency,
    WorkflowDefinition,
    WorkflowDependency,
    WorkflowScope,
)

WORKFLOW_SCHEMA_VERSION = 2
SUPPORTED_WORKFLOW_SCHEMA_VERSIONS = frozenset({1, 2})
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_V1_DOCUMENT_KEYS = {
    "schema_version",
    "id",
    "name",
    "description",
    "version",
    "persona_dependencies",
    "graph",
}
_V2_DOCUMENT_KEYS = _V1_DOCUMENT_KEYS | {"workflow_dependencies"}
_DEPENDENCY_KEYS = {"id", "revision", "digest", "path"}
_REVIEW_ATTESTATION_KEYS = {"version", "scope", "eventType", "roles"}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise WorkflowDocumentError("YAML mapping keys must be scalar values") from exc
        if duplicate:
            raise WorkflowDocumentError(f"Duplicate YAML mapping key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class ReviewAttestationBinding:
    """Pinned reviewer roles and personas authenticated by the workflow runtime."""

    version: int
    scope: str
    event_type: str
    roles: dict[str, str]

    @property
    def personas(self) -> dict[str, str]:
        return {persona_id: role for role, persona_id in self.roles.items()}


@dataclass(frozen=True)
class WorkflowDocument:
    """Portable workflow definition without local ownership or access metadata."""

    schema_version: int
    id: UUID
    name: str
    description: str
    version: str
    persona_dependencies: dict[str, PersonaDependency]
    graph: dict[str, Any]
    workflow_dependencies: dict[str, WorkflowDependency] = field(default_factory=dict)

    def to_workflow(
        self,
        *,
        scope: WorkflowScope,
        owner_id: str | None,
        tenant_id: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        revision: str | None = None,
        read_only: bool = False,
        source: str | None = None,
        persona_definitions: dict[str, dict[str, Any]] | None = None,
        workflow_definitions: dict[str, dict[str, Any]] | None = None,
        requirements: list[dict[str, Any]] | None = None,
    ) -> WorkflowDefinition:
        now = datetime.now(UTC)
        return WorkflowDefinition(
            id=self.id,
            name=self.name,
            description=self.description,
            version=self.version,
            scope=scope,
            owner_id=owner_id,
            graph=self.graph,
            created_at=created_at or now,
            updated_at=updated_at or now,
            tenant_id=tenant_id,
            persona_dependencies=self.persona_dependencies,
            schema_version=self.schema_version,
            workflow_dependencies=self.workflow_dependencies,
            revision=revision,
            read_only=read_only,
            source=source,
            persona_definitions=persona_definitions or {},
            workflow_definitions=workflow_definitions or {},
            requirements=requirements or [],
        )


def document_from_workflow(workflow: WorkflowDefinition) -> WorkflowDocument:
    """Project a stored workflow into its portable representation."""
    if workflow.schema_version < 2 and workflow.workflow_dependencies:
        raise WorkflowDocumentError("Workflow dependencies require workflow schema_version 2")
    return WorkflowDocument(
        schema_version=workflow.schema_version,
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        version=workflow.version,
        persona_dependencies=dict(workflow.persona_dependencies),
        graph=dict(workflow.graph),
        workflow_dependencies=dict(workflow.workflow_dependencies),
    )


def load_workflow_document(text: str) -> WorkflowDocument:
    """Parse and strictly validate a portable workflow YAML document."""
    try:
        raw = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except (yaml.YAMLError, WorkflowDocumentError) as exc:
        raise WorkflowDocumentError(f"Invalid workflow YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowDocumentError("Workflow document must be a YAML mapping")

    if any(not isinstance(key, str) for key in raw):
        raise WorkflowDocumentError("Workflow document field names must be strings")
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version not in SUPPORTED_WORKFLOW_SCHEMA_VERSIONS:
        supported = ", ".join(str(item) for item in sorted(SUPPORTED_WORKFLOW_SCHEMA_VERSIONS))
        raise WorkflowDocumentError(
            f"Unsupported workflow schema_version {schema_version!r}; "
            f"supported versions are {supported}"
        )
    document_keys = _V2_DOCUMENT_KEYS if schema_version == 2 else _V1_DOCUMENT_KEYS
    unknown = set(raw) - document_keys
    if unknown:
        raise WorkflowDocumentError(f"Unknown workflow field(s): {', '.join(sorted(unknown))}")
    missing = document_keys - set(raw)
    if missing:
        raise WorkflowDocumentError(f"Missing workflow field(s): {', '.join(sorted(missing))}")

    try:
        workflow_id = UUID(str(raw["id"]))
    except (TypeError, ValueError) as exc:
        raise WorkflowDocumentError("Workflow id must be a valid UUID") from exc

    name = _required_string(raw["name"], "name")
    description = raw["description"]
    if not isinstance(description, str):
        raise WorkflowDocumentError("Workflow description must be a string")
    version = _required_string(raw["version"], "version")
    graph = raw["graph"]
    if not isinstance(graph, dict):
        raise WorkflowDocumentError("Workflow graph must be a mapping")
    if any(not isinstance(key, str) for key in graph):
        raise WorkflowDocumentError("Workflow graph field names must be strings")
    try:
        json.dumps(graph, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise WorkflowDocumentError(
            "Workflow graph must contain only finite JSON-compatible values"
        ) from exc
    _validate_graph_structure(graph)
    try:
        validate_evidence_gate_nodes(graph)
    except ValueError as exc:
        raise WorkflowDocumentError(f"Invalid workflow evidence gate: {exc}") from exc
    _validate_review_verdict_policies(graph)
    _validate_wait_nodes(graph, schema_version=schema_version)
    _validate_tool_actions(graph)

    dependencies_raw = raw["persona_dependencies"]
    if not isinstance(dependencies_raw, dict):
        raise WorkflowDocumentError("persona_dependencies must be a mapping keyed by alias")
    if any(not isinstance(alias, str) for alias in dependencies_raw):
        raise WorkflowDocumentError("Persona dependency aliases must be strings")
    dependencies: dict[str, PersonaDependency] = {}
    for alias, value in dependencies_raw.items():
        parsed_alias = _required_string(alias, "persona dependency alias")
        try:
            validate_persona_identifier(parsed_alias, field="Workflow persona alias")
        except PersonaDocumentError as exc:
            raise WorkflowDocumentError(str(exc)) from exc
        if parsed_alias in dependencies:
            raise WorkflowDocumentError(f"Duplicate persona dependency alias {parsed_alias!r}")
        dependencies[parsed_alias] = _load_dependency(parsed_alias, value)

    referenced_aliases = referenced_persona_aliases(graph)
    missing_aliases = referenced_aliases - set(dependencies)
    if missing_aliases:
        raise WorkflowDocumentError(
            "Workflow graph references undeclared persona alias(es): "
            + ", ".join(sorted(missing_aliases))
        )
    workflow_review_attestation(graph, persona_dependencies=dependencies)

    workflow_dependencies = _load_workflow_dependencies(
        raw.get("workflow_dependencies", {}),
        schema_version=schema_version,
    )
    _validate_subworkflow_nodes(
        graph,
        schema_version=schema_version,
        dependencies=workflow_dependencies,
        persona_dependencies=dependencies,
    )

    return WorkflowDocument(
        schema_version=schema_version,
        id=workflow_id,
        name=name,
        description=description,
        version=version,
        persona_dependencies=dependencies,
        graph=graph,
        workflow_dependencies=workflow_dependencies,
    )


def dump_workflow_document(document: WorkflowDocument | WorkflowDefinition) -> str:
    """Serialize a workflow deterministically as safe portable YAML."""
    if isinstance(document, WorkflowDefinition):
        document = document_from_workflow(document)
    payload = workflow_document_payload(document)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)


def workflow_document_payload(
    document: WorkflowDocument | WorkflowDefinition,
) -> dict[str, Any]:
    """Return the canonical portable mapping for a workflow document."""
    if isinstance(document, WorkflowDefinition):
        document = document_from_workflow(document)
    payload: dict[str, Any] = {
        "schema_version": document.schema_version,
        "id": str(document.id),
        "name": document.name,
        "description": document.description,
        "version": document.version,
        "persona_dependencies": {
            alias: _dependency_to_dict(dependency)
            for alias, dependency in sorted(document.persona_dependencies.items())
        },
    }
    if document.schema_version >= 2:
        payload["workflow_dependencies"] = {
            alias: _workflow_dependency_to_dict(dependency)
            for alias, dependency in sorted(document.workflow_dependencies.items())
        }
    payload["graph"] = document.graph
    return payload


def workflow_document_revision(document: WorkflowDocument | WorkflowDefinition) -> str:
    """Return an opaque content token for compare-and-swap updates."""
    if isinstance(document, WorkflowDefinition):
        document = document_from_workflow(document)
    canonical = workflow_document_payload(document)
    for dependencies_key in ("persona_dependencies", "workflow_dependencies"):
        dependencies = canonical.get(dependencies_key, {})
        if isinstance(dependencies, dict):
            for dependency in dependencies.values():
                if isinstance(dependency, dict):
                    dependency.pop("path", None)
    encoded = json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDocumentError(f"Workflow {field_name} must be a non-empty string")
    return value.strip()


def _load_dependency(alias: str, value: object) -> PersonaDependency:
    if not isinstance(value, dict):
        raise WorkflowDocumentError(f"Persona dependency {alias!r} must be a mapping")
    unknown = set(value) - _DEPENDENCY_KEYS
    if unknown:
        raise WorkflowDocumentError(
            f"Persona dependency {alias!r} has unknown field(s): " + ", ".join(sorted(unknown))
        )
    missing = {"id", "revision", "digest"} - set(value)
    if missing:
        raise WorkflowDocumentError(
            f"Persona dependency {alias!r} is missing field(s): " + ", ".join(sorted(missing))
        )
    persona_id = _required_string(value["id"], f"persona dependency {alias!r} id")
    revision = _required_string(value["revision"], f"persona dependency {alias!r} revision")
    digest = _required_string(value["digest"], f"persona dependency {alias!r} digest")
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise WorkflowDocumentError(
            f"Persona dependency {alias!r} digest must be sha256:<64 lowercase hex characters>"
        )
    path = value.get("path")
    if path is not None:
        path = _validate_bundle_path(alias, path)
    return PersonaDependency(id=persona_id, revision=revision, digest=digest, path=path)


def _load_workflow_dependencies(
    value: object,
    *,
    schema_version: int,
) -> dict[str, WorkflowDependency]:
    if not isinstance(value, dict):
        raise WorkflowDocumentError("workflow_dependencies must be a mapping keyed by alias")
    if any(not isinstance(alias, str) for alias in value):
        raise WorkflowDocumentError("Workflow dependency aliases must be strings")
    dependencies: dict[str, WorkflowDependency] = {}
    for alias, raw_dependency in value.items():
        parsed_alias = _required_string(alias, "workflow dependency alias")
        try:
            validate_persona_identifier(parsed_alias, field="Workflow dependency alias")
        except PersonaDocumentError as exc:
            raise WorkflowDocumentError(str(exc)) from exc
        if not isinstance(raw_dependency, dict):
            raise WorkflowDocumentError(f"Workflow dependency {parsed_alias!r} must be a mapping")
        unknown = set(raw_dependency) - _DEPENDENCY_KEYS
        missing = {"id", "revision", "digest"} - set(raw_dependency)
        if unknown or missing:
            details = []
            if unknown:
                details.append("unknown: " + ", ".join(sorted(unknown)))
            if missing:
                details.append("missing: " + ", ".join(sorted(missing)))
            raise WorkflowDocumentError(
                f"Invalid workflow dependency {parsed_alias!r} ({'; '.join(details)})"
            )
        try:
            workflow_id = UUID(str(raw_dependency["id"]))
        except (TypeError, ValueError) as exc:
            raise WorkflowDocumentError(
                f"Workflow dependency {parsed_alias!r} id must be a UUID"
            ) from exc
        revision = _required_string(
            raw_dependency["revision"], f"workflow dependency {parsed_alias!r} revision"
        )
        digest = _required_string(
            raw_dependency["digest"], f"workflow dependency {parsed_alias!r} digest"
        )
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise WorkflowDocumentError(
                f"Workflow dependency {parsed_alias!r} digest must be "
                "sha256:<64 lowercase hex characters>"
            )
        path = raw_dependency.get("path")
        if path is not None:
            path = _validate_workflow_bundle_path(parsed_alias, path)
        dependencies[parsed_alias] = WorkflowDependency(
            id=workflow_id,
            revision=revision,
            digest=digest,
            path=path,
        )
    if schema_version == 1 and dependencies:
        raise WorkflowDocumentError("schema_version 1 cannot declare workflow dependencies")
    return dependencies


def _validate_bundle_path(alias: str, value: object) -> str:
    path = _required_string(value, f"persona dependency {alias!r} path")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or ".." in parsed.parts
        or path != parsed.as_posix()
        or "\\" in path
        or ":" in path
        or not path.startswith("personas/")
        or parsed.suffix not in {".yaml", ".yml"}
    ):
        raise WorkflowDocumentError(
            f"Persona dependency {alias!r} path must be a normalized personas/*.yaml "
            "relative POSIX path"
        )
    return path


def _validate_workflow_bundle_path(alias: str, value: object) -> str:
    path = _required_string(value, f"workflow dependency {alias!r} path")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or ".." in parsed.parts
        or path != parsed.as_posix()
        or "\\" in path
        or ":" in path
        or not path.startswith("workflows/")
        or parsed.suffix not in {".yaml", ".yml"}
    ):
        raise WorkflowDocumentError(
            f"Workflow dependency {alias!r} path must be a normalized workflows/*.yaml "
            "relative POSIX path"
        )
    return path


def referenced_persona_aliases(graph: dict[str, Any]) -> set[str]:
    """Return stage persona aliases using the runtime's legacy precedence."""
    aliases: set[str] = set()
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list):
        raise WorkflowDocumentError("Workflow graph nodes must be a list")
    for node in nodes:
        if not isinstance(node, dict):
            raise WorkflowDocumentError("Each workflow graph node must be a mapping")
        members = node.get("stageMembers", [])
        if not isinstance(members, list):
            raise WorkflowDocumentError("Workflow stageMembers must be a list")
        for member in members:
            if not isinstance(member, dict):
                raise WorkflowDocumentError("Each workflow stage member must be a mapping")
            alias = member.get("personaId")
            if alias is None:
                continue
            parsed_alias = _required_string(alias, "stage member personaId")
            try:
                validate_persona_identifier(parsed_alias, field="Workflow persona alias")
            except PersonaDocumentError as exc:
                raise WorkflowDocumentError(str(exc)) from exc
            aliases.add(parsed_alias)
        if members:
            continue
        legacy_aliases = node.get("personaIds", [])
        if not isinstance(legacy_aliases, list):
            raise WorkflowDocumentError("Workflow personaIds must be a list")
        for alias in legacy_aliases:
            parsed_alias = _required_string(alias, "stage personaIds alias")
            try:
                validate_persona_identifier(parsed_alias, field="Workflow persona alias")
            except PersonaDocumentError as exc:
                raise WorkflowDocumentError(str(exc)) from exc
            aliases.add(parsed_alias)
    return aliases


def workflow_review_attestation(
    graph: dict[str, Any],
    *,
    persona_dependencies: dict[str, PersonaDependency] | None = None,
) -> ReviewAttestationBinding | None:
    """Parse a workflow-pinned reviewer binding declared directly on the graph."""
    raw = graph.get("reviewAttestation")
    if raw is None:
        if _requires_review_attestation(graph):
            raise WorkflowDocumentError("Workflow graph requires graph.reviewAttestation")
        return None
    if not isinstance(raw, dict):
        raise WorkflowDocumentError("Workflow graph reviewAttestation must be a mapping")
    unknown = set(raw) - _REVIEW_ATTESTATION_KEYS
    missing = _REVIEW_ATTESTATION_KEYS - set(raw)
    if unknown or missing:
        details = []
        if unknown:
            details.append("unknown: " + ", ".join(sorted(unknown)))
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        raise WorkflowDocumentError(
            "Invalid workflow graph reviewAttestation (" + "; ".join(details) + ")"
        )
    if raw["version"] != 1 or type(raw["version"]) is not int:
        raise WorkflowDocumentError("Workflow graph reviewAttestation version must be 1")
    scope = _required_string(raw["scope"], "reviewAttestation scope")
    event_type = _required_string(raw["eventType"], "reviewAttestation eventType")
    raw_roles = raw["roles"]
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise WorkflowDocumentError(
            "Workflow graph reviewAttestation roles must be a non-empty mapping"
        )
    if any(not isinstance(role, str) for role in raw_roles):
        raise WorkflowDocumentError("Workflow graph reviewAttestation role names must be strings")

    roles: dict[str, str] = {}
    for role, persona_id in raw_roles.items():
        normalized_role = _required_string(role, "reviewAttestation role")
        normalized_persona = _required_string(
            persona_id, f"reviewAttestation role {normalized_role!r} personaId"
        )
        try:
            validate_persona_identifier(normalized_role, field="Workflow review role")
            validate_persona_identifier(normalized_persona, field="Workflow review persona alias")
        except PersonaDocumentError as exc:
            raise WorkflowDocumentError(str(exc)) from exc
        roles[normalized_role] = normalized_persona
    if len(set(roles.values())) != len(roles):
        raise WorkflowDocumentError(
            "Workflow graph reviewAttestation personas must be unique across roles"
        )
    if persona_dependencies is not None:
        undeclared = set(roles.values()) - set(persona_dependencies)
        if undeclared:
            raise WorkflowDocumentError(
                "Workflow graph reviewAttestation references undeclared persona alias(es): "
                + ", ".join(sorted(undeclared))
            )
        joined_personas = {
            alias
            for node in graph.get("nodes", [])
            if isinstance(node, dict)
            and node.get("kind") == "stage"
            and node.get("joinMode") == "all"
            for alias in referenced_persona_aliases({"nodes": [node], "edges": []})
        }
        unjoined = set(roles.values()) - joined_personas
        if unjoined:
            raise WorkflowDocumentError(
                "Workflow graph reviewAttestation persona alias(es) must belong to a "
                "joinMode all stage: " + ", ".join(sorted(unjoined))
            )
    return ReviewAttestationBinding(
        version=1,
        scope=scope,
        event_type=event_type,
        roles=roles,
    )


def _requires_review_attestation(graph: dict[str, Any]) -> bool:
    """Return whether the graph uses a construct that consumes an attested binding.

    A `reviewVerdictPolicy` stage gates its pass/fail outcome on an event type
    supplied by authenticated reviewer identities, and a subworkflow whose
    `resultSchema` requires `reviewReceipts` hands a signed reviewer receipt
    across the child/parent boundary. Either one needs to know which persona
    may speak for which role, which is exactly what `reviewAttestation`
    supplies — so declaring one of these constructs without it is fatal rather
    than silently ungoverned.
    """
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("reviewVerdictPolicy"), dict):
            return True
        if node.get("kind") != "subworkflow":
            continue
        result_schema = node.get("resultSchema")
        required = result_schema.get("required") if isinstance(result_schema, dict) else None
        if isinstance(required, list) and "reviewReceipts" in required:
            return True
    return False


def _validate_tool_actions(graph: dict[str, Any]) -> None:
    """Validate the optional graph-level narrowing of tool actions by name.

    `toolActions` maps a tool name to the exact action names a persona running
    this graph may use from it; the tool builder can only intersect a
    persona's own declared actions against this list, never widen them. The
    key is opaque here — no tool or action name is privileged by the engine.
    """
    if "toolActions" not in graph:
        return
    tool_actions = graph["toolActions"]
    if not isinstance(tool_actions, dict) or not tool_actions:
        raise WorkflowDocumentError("Workflow graph toolActions must be a non-empty mapping")
    for tool_name, actions in tool_actions.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise WorkflowDocumentError("Workflow graph toolActions tool names must be strings")
        if (
            not isinstance(actions, list)
            or not actions
            or any(not isinstance(action, str) or not action.strip() for action in actions)
        ):
            raise WorkflowDocumentError(
                f"Workflow graph toolActions[{tool_name!r}] must be a non-empty list of "
                "action names"
            )
        if len(set(actions)) != len(actions):
            raise WorkflowDocumentError(
                f"Workflow graph toolActions[{tool_name!r}] must not repeat action names"
            )


def _validate_graph_structure(graph: dict[str, Any]) -> None:
    for field_name in ("nodes", "edges"):
        value = graph.get(field_name, [])
        if not isinstance(value, list):
            raise WorkflowDocumentError(f"Workflow graph {field_name} must be a list")
        if any(not isinstance(item, dict) for item in value):
            raise WorkflowDocumentError(f"Each workflow graph {field_name[:-1]} must be a mapping")
    for field_name in ("resourceBindings", "resource_bindings"):
        if field_name not in graph:
            continue
        value = graph[field_name]
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise WorkflowDocumentError(f"Workflow graph {field_name} must be a list of mappings")
    for node in graph.get("nodes", []):
        passing_verdicts = node.get("passingVerdicts")
        if passing_verdicts is None:
            continue
        node_id = str(node.get("id") or "").strip()
        if (
            node.get("kind") != "end"
            or not isinstance(passing_verdicts, list)
            or not (passing_verdicts)
        ):
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} passingVerdicts must be a non-empty list on an end node"
            )
        if any(not isinstance(value, str) or not value.strip() for value in passing_verdicts):
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} passingVerdicts must contain event verdict strings"
            )


def _validate_review_verdict_policies(graph: dict[str, Any]) -> None:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    by_id = {str(node.get("id") or ""): node for node in nodes if isinstance(node, dict)}
    for node in nodes:
        policy = node.get("reviewVerdictPolicy")
        if policy is None:
            continue
        node_id = _required_string(node.get("id"), "review verdict policy node id")
        if node.get("kind") != "stage" or not isinstance(policy, dict):
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} reviewVerdictPolicy must be a mapping on a stage"
            )
        unknown = set(policy) - {
            "eventType",
            "passOutcomes",
            "failOutcomes",
            "bindingFields",
        }
        if unknown:
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} reviewVerdictPolicy has unknown field(s): "
                + ", ".join(sorted(unknown))
            )
        event_type = _required_string(
            policy.get("eventType"), f"Workflow node {node_id!r} review eventType"
        )
        outcome_sets: list[set[str]] = []
        for outcome_field in ("passOutcomes", "failOutcomes"):
            values = policy.get(outcome_field)
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)
            ):
                raise WorkflowDocumentError(
                    f"Workflow node {node_id!r} reviewVerdictPolicy {outcome_field} "
                    "must be a non-empty list of event types"
                )
            outcome_sets.append({value.strip() for value in values})
        if outcome_sets[0] & outcome_sets[1]:
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} review pass and fail outcomes must be disjoint"
            )
        binding_fields = policy.get("bindingFields")
        if (
            not isinstance(binding_fields, list)
            or not binding_fields
            or any(not isinstance(value, str) or not value.strip() for value in binding_fields)
        ):
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} reviewVerdictPolicy bindingFields "
                "must be a non-empty list"
            )
        incoming_sources = [
            by_id.get(str(edge.get("source") or ""))
            for edge in edges
            if str(edge.get("target") or "") == node_id
            and _split_edge_label_for_validation(edge.get("label"))[1] == event_type
        ]
        reviewers = set().union(
            *(
                referenced_persona_aliases({"nodes": [source], "edges": []})
                for source in incoming_sources
                if isinstance(source, dict)
                and source.get("kind") == "stage"
                and source.get("joinMode") == "all"
            ),
            set(),
        )
        if len(reviewers) < 2:
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} reviewVerdictPolicy requires an incoming "
                "joinMode all stage with at least two declared reviewers"
            )
        outgoing = {
            _split_edge_label_for_validation(edge.get("label"))[0]
            for edge in edges
            if str(edge.get("source") or "") == node_id
        }
        undeclared = (outcome_sets[0] | outcome_sets[1]) - outgoing
        if undeclared:
            raise WorkflowDocumentError(
                f"Workflow node {node_id!r} review outcome(s) are not outgoing topics: "
                + ", ".join(sorted(undeclared))
            )


def _validate_wait_nodes(graph: dict[str, Any], *, schema_version: int) -> None:
    """Validate passive external waits without turning them into agent or human gates."""
    edges = graph.get("edges", [])
    for node in graph.get("nodes", []):
        if node.get("kind") != "wait":
            continue
        node_id = _required_string(node.get("id"), "wait node id")
        _required_string(node.get("label"), f"wait node {node_id!r} label")
        if schema_version < 2:
            raise WorkflowDocumentError(f"Wait node {node_id!r} requires workflow schema_version 2")
        if node.get("stageMembers") or node.get("personaIds"):
            raise WorkflowDocumentError(
                f"Wait node {node_id!r} must not declare personas or stage members"
            )
        incoming = [edge for edge in edges if str(edge.get("target") or "") == node_id]
        outgoing = [edge for edge in edges if str(edge.get("source") or "") == node_id]
        if not incoming or not outgoing:
            raise WorkflowDocumentError(
                f"Wait node {node_id!r} requires incoming suspension and "
                "outgoing continuation edges"
            )
        for edge in (*incoming, *outgoing):
            source_event, target_event = _split_edge_label_for_validation(edge.get("label"))
            if not source_event or not target_event:
                raise WorkflowDocumentError(
                    f"Wait node {node_id!r} edges require source and target event types"
                )


def _split_edge_label_for_validation(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or "->" not in value:
        return "", ""
    source, target = value.split("->", 1)
    return source.strip(), target.strip()


def _validate_subworkflow_nodes(
    graph: dict[str, Any],
    *,
    schema_version: int,
    dependencies: dict[str, WorkflowDependency],
    persona_dependencies: dict[str, PersonaDependency],
) -> None:
    for node in graph.get("nodes", []):
        if node.get("kind") != "subworkflow":
            continue
        node_id = _required_string(node.get("id"), "subworkflow node id")
        if schema_version < 2:
            raise WorkflowDocumentError(
                f"Subworkflow node {node_id!r} requires workflow schema_version 2"
            )
        dependency_alias = _required_string(
            node.get("workflowDependency"),
            f"subworkflow node {node_id!r} workflowDependency",
        )
        if dependency_alias not in dependencies:
            raise WorkflowDocumentError(
                f"Subworkflow node {node_id!r} references undeclared workflow dependency "
                f"{dependency_alias!r}"
            )
        coordinator = _required_string(
            node.get("allowedCoordinator"),
            f"subworkflow node {node_id!r} allowedCoordinator",
        )
        if coordinator not in persona_dependencies:
            raise WorkflowDocumentError(
                f"Subworkflow node {node_id!r} allowedCoordinator {coordinator!r} "
                "is not a declared persona dependency"
            )
        _validate_contract_schema(node.get("inputSchema"), node_id=node_id, field="inputSchema")
        _validate_contract_schema(node.get("resultSchema"), node_id=node_id, field="resultSchema")
        _validate_bounded_integer(
            node.get("maxChildren"),
            node_id=node_id,
            field="maxChildren",
            minimum=1,
            maximum=100,
        )
        _validate_bounded_integer(
            node.get("maxAttempts"),
            node_id=node_id,
            field="maxAttempts",
            minimum=1,
            maximum=10,
        )
        join_mode = node.get("joinMode")
        if join_mode not in {"all", "any"}:
            raise WorkflowDocumentError(
                f"Subworkflow node {node_id!r} joinMode must be 'all' or 'any'"
            )


def _validate_contract_schema(value: object, *, node_id: str, field: str) -> None:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise WorkflowDocumentError(
            f"Subworkflow node {node_id!r} {field} must be an object JSON schema"
        )
    properties = value.get("properties")
    if not isinstance(properties, dict) or any(
        not isinstance(name, str) or not isinstance(schema, dict)
        for name, schema in properties.items()
    ):
        raise WorkflowDocumentError(
            f"Subworkflow node {node_id!r} {field}.properties must map names to schemas"
        )
    required = value.get("required", [])
    if not isinstance(required, list) or any(
        not isinstance(name, str) or name not in properties for name in required
    ):
        raise WorkflowDocumentError(
            f"Subworkflow node {node_id!r} {field}.required must reference declared properties"
        )


def _validate_bounded_integer(
    value: object,
    *,
    node_id: str,
    field: str,
    minimum: int,
    maximum: int,
) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise WorkflowDocumentError(
            f"Subworkflow node {node_id!r} {field} must be an integer from {minimum} to {maximum}"
        )


def _dependency_to_dict(dependency: PersonaDependency) -> dict[str, str]:
    value = {
        "id": dependency.id,
        "revision": dependency.revision,
        "digest": dependency.digest,
    }
    if dependency.path is not None:
        value["path"] = dependency.path
    return value


def _workflow_dependency_to_dict(dependency: WorkflowDependency) -> dict[str, str]:
    return dependency.to_dict()
