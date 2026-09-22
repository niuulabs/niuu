"""Compile local source references into the existing portable workflow format.

Only dependency declarations have authoring shorthand. Graphs and personas use
the runtime's existing schemas; there is no second workflow language or engine.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml

from ravn.domain.persona_document import (
    PortablePersonaDefinition,
    parse_portable_persona,
    persona_revision_for_definition,
    portable_persona_yaml,
)
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.services.workflow_sharing import (
    plan_workflow_import,
    reject_inline_workflow_secrets,
)
from ting.domain.workflow_document import (
    WorkflowDocument,
    _UniqueKeySafeLoader,
    dump_workflow_document,
    load_workflow_document,
    workflow_document_revision,
)
from ting.ports.workflow_authoring import WorkflowSourceReader


@dataclass(frozen=True)
class WorkflowBuild:
    document: WorkflowDocument
    files: dict[str, str]

    @property
    def yaml(self) -> str:
        return dump_workflow_document(self.document)


def compile_workflow(
    source: str,
    reader: WorkflowSourceReader,
    *,
    max_depth: int = 32,
) -> WorkflowBuild:
    """Pin referenced source content and validate the entire importable closure."""
    files: dict[str, str] = {}
    cache: dict[str, WorkflowDocument] = {}

    def read_mapping(path: str) -> dict:
        try:
            value = yaml.load(reader.read(path), Loader=_UniqueKeySafeLoader)
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowDocumentError(f"{path}: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkflowDocumentError(f"{path}: source must be a YAML mapping")
        return value

    def visit(path: str, ancestors: tuple[str, ...]) -> WorkflowDocument:
        if path in ancestors:
            raise WorkflowDocumentError(
                f"Cyclic workflow sources: {' -> '.join((*ancestors, path))}"
            )
        if len(ancestors) >= max_depth:
            raise WorkflowDocumentError(f"Workflow source nesting exceeds {max_depth}: {path}")
        if path in cache:
            return cache[path]
        raw = read_mapping(path)
        reject_inline_workflow_secrets(raw.get("graph"))
        for key in ("persona_dependencies", "workflow_dependencies"):
            dependencies = raw.get(key, {})
            if not isinstance(dependencies, dict):
                raise WorkflowDocumentError(f"{path}: {key} must be a mapping")
            for alias, dependency in dependencies.items():
                if not isinstance(dependency, dict) or set(dependency) != {"source"}:
                    raise WorkflowDocumentError(
                        f"{path}: {key}.{alias} must contain only source; "
                        "build source definitions, or import already-pinned bundles directly"
                    )
                referenced = _source_path(path, dependency["source"])
                if key == "persona_dependencies":
                    definition = read_mapping(referenced)
                    if "schema_version" in definition:
                        persona = parse_portable_persona(definition)
                    else:
                        persona = PortablePersonaDefinition(
                            id=str(definition.get("name") or ""),
                            revision=persona_revision_for_definition(definition),
                            definition=definition,
                        )
                    bundle_path = (
                        f"personas/{persona.id}-{persona.digest.removeprefix('sha256:')}.yaml"
                    )
                    files[bundle_path] = portable_persona_yaml(persona)
                    dependencies[alias] = {
                        **persona.dependency.to_dict(),
                        "path": bundle_path,
                    }
                    continue
                child = visit(referenced, (*ancestors, path))
                digest = workflow_document_revision(child)
                bundle_path = f"workflows/{child.id}-{digest.removeprefix('sha256:')}.yaml"
                files[bundle_path] = dump_workflow_document(child)
                dependencies[alias] = {
                    "id": str(child.id),
                    "revision": digest,
                    "digest": digest,
                    "path": bundle_path,
                }
        try:
            document = load_workflow_document(json.dumps(raw))
        except (ValueError, TypeError) as exc:
            raise WorkflowDocumentError(f"{path}: {exc}") from exc
        cache[path] = document
        return document

    document = visit(_source_path("", source), ())
    plan = plan_workflow_import(document, files, source=None, mappings={}, bindings={})
    if plan.errors:
        raise WorkflowDocumentError("; ".join(plan.errors))
    return WorkflowBuild(document, files)


def _source_path(parent: str, source: object) -> str:
    if not isinstance(source, str) or not source or "\\" in source or ":" in source:
        raise WorkflowDocumentError("Source must be a non-empty relative YAML path")
    if PurePosixPath(source).is_absolute():
        raise WorkflowDocumentError("Source paths must stay inside the workflow project")
    result = posixpath.normpath(posixpath.join(posixpath.dirname(parent), source))
    if (
        result == ".."
        or result.startswith("../")
        or PurePosixPath(result).suffix not in {".yaml", ".yml"}
    ):
        raise WorkflowDocumentError(
            f"Source must be a YAML file inside the workflow project: {source}"
        )
    return result
