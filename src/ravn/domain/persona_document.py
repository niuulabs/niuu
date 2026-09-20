"""Portable, revision-pinned Ravn persona documents.

The portable document contains only source-authored persona behaviour. Runtime
prompt injection and environment bindings (executor adapters, credentials,
paths, and provider connections) are deliberately outside this contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

import yaml
from yaml.constructor import ConstructorError

PERSONA_DOCUMENT_SCHEMA_VERSION = 1
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_PORTABLE_DEFINITION_FIELDS = frozenset(
    {
        "name",
        "system_prompt_template",
        "allowed_tools",
        "forbidden_tools",
        "permission_mode",
        "llm",
        "iteration_budget",
        "produces",
        "consumes",
        "fan_in",
        "stop_on_outcome",
    }
)


class PersonaDocumentError(ValueError):
    """A portable persona document or dependency pin is invalid."""


class PersonaDependencyResolutionError(PersonaDocumentError):
    """An exact persona dependency cannot be resolved."""


class PersonaNonPortableError(PersonaDocumentError):
    """A persona contains a local binding that cannot be exported safely."""


class PortablePersonaSource(Protocol):
    """Source capable of resolving an exact portable persona revision."""

    def load_portable(
        self,
        persona_id: str,
        revision: str,
    ) -> PortablePersonaDefinition | None: ...

    def load_current_portable(
        self,
        persona_id: str,
    ) -> PortablePersonaDefinition | None: ...


class PortablePersonaCollection:
    """In-memory exact source assembled from an authorized remote registry."""

    def __init__(self, documents: list[PortablePersonaDefinition]) -> None:
        self._by_pin: dict[tuple[str, str], PortablePersonaDefinition] = {}
        self._current: dict[str, PortablePersonaDefinition] = {}
        for document in documents:
            pin = (document.id, document.revision)
            previous = self._by_pin.get(pin)
            if previous is not None and previous.digest != document.digest:
                raise PersonaDocumentError(
                    f"Conflicting persona content for {document.id}@{document.revision}"
                )
            self._by_pin[pin] = document
            current = self._current.get(document.id)
            if current is not None and current.revision != document.revision:
                raise PersonaDocumentError(
                    f"Multiple current revisions supplied for persona {document.id!r}"
                )
            self._current[document.id] = document

    def load_portable(
        self,
        persona_id: str,
        revision: str,
    ) -> PortablePersonaDefinition | None:
        return self._by_pin.get((persona_id, revision))

    def load_current_portable(
        self,
        persona_id: str,
    ) -> PortablePersonaDefinition | None:
        return self._current.get(persona_id)


@dataclass(frozen=True)
class PersonaDependency:
    """Exact workflow dependency on a Ravn persona source definition."""

    id: str
    revision: str
    digest: str
    path: str | None = None

    def __post_init__(self) -> None:
        validate_persona_identifier(self.id, field="Persona dependency id")
        validate_persona_identifier(self.revision, field="Persona dependency revision")
        if not _DIGEST_PATTERN.fullmatch(self.digest):
            raise PersonaDocumentError(
                "Persona dependency digest must be 'sha256:' followed by 64 lowercase hex digits"
            )
        if self.path is not None:
            _validate_bundle_path(self.path)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PersonaDependency:
        allowed = {"id", "revision", "digest", "path"}
        unknown = set(data) - allowed
        if unknown:
            raise PersonaDocumentError(
                f"Unsupported persona dependency field(s): {', '.join(sorted(unknown))}"
            )
        return cls(
            id=str(data.get("id") or "").strip(),
            revision=str(data.get("revision") or "").strip(),
            digest=str(data.get("digest") or "").strip(),
            path=(str(data["path"]).strip() if data.get("path") is not None else None),
        )

    def to_dict(self) -> dict[str, str]:
        result = {"id": self.id, "revision": self.revision, "digest": self.digest}
        if self.path is not None:
            result["path"] = self.path
        return result


@dataclass(frozen=True)
class PortablePersonaDefinition:
    """Lossless portable source definition for one immutable persona revision."""

    id: str
    revision: str
    definition: dict[str, Any]
    schema_version: int = PERSONA_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PERSONA_DOCUMENT_SCHEMA_VERSION:
            raise PersonaDocumentError(
                f"Unsupported persona schema_version {self.schema_version}; "
                f"expected {PERSONA_DOCUMENT_SCHEMA_VERSION}"
            )
        validate_persona_identifier(self.id, field="Portable persona id")
        validate_persona_identifier(self.revision, field="Portable persona revision")
        _validate_definition(self.id, self.definition)

    @property
    def digest(self) -> str:
        return persona_definition_digest(self.definition)

    @property
    def dependency(self) -> PersonaDependency:
        return PersonaDependency(id=self.id, revision=self.revision, digest=self.digest)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PortablePersonaDefinition:
        allowed = {"schema_version", "id", "revision", "definition"}
        unknown = set(data) - allowed
        if unknown:
            raise PersonaDocumentError(
                f"Unsupported portable persona field(s): {', '.join(sorted(unknown))}"
            )
        definition = data.get("definition")
        if not isinstance(definition, dict):
            raise PersonaDocumentError("Portable persona definition must be a mapping")
        raw_schema_version = data.get("schema_version")
        if isinstance(raw_schema_version, bool) or not isinstance(raw_schema_version, int):
            raise PersonaDocumentError("Portable persona schema_version must be an integer")
        return cls(
            schema_version=raw_schema_version,
            id=str(data.get("id") or "").strip(),
            revision=str(data.get("revision") or "").strip(),
            definition=_json_copy(definition),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "revision": self.revision,
            "definition": _json_copy(self.definition),
        }


def portable_persona_from_config(
    config: Any,
    *,
    persona_id: str | None = None,
    revision: str | None = None,
) -> PortablePersonaDefinition:
    """Create a portable document from a raw ``PersonaConfig`` source.

    ``PersonaConfig.to_dict`` is the canonical lossless source serializer.
    Executor configuration is an environment binding and is intentionally
    excluded. Callers must pass a raw source config, before outcome prompt
    instructions or other runtime prompt additions are injected.
    """
    raw = config.to_dict()
    if not isinstance(raw, dict):
        raise PersonaDocumentError("PersonaConfig.to_dict() must return a mapping")
    executor = raw.get("executor")
    if isinstance(executor, dict) and executor:
        raise PersonaNonPortableError(
            f"Persona {getattr(config, 'name', '<unknown>')!r} has a local executor binding. "
            "Remove the executor binding from the source persona and select a local runtime "
            "binding at workflow launch before exporting it as portable content."
        )
    definition = {key: value for key, value in raw.items() if key in _PORTABLE_DEFINITION_FIELDS}
    identity = str(persona_id or definition.get("name") or "").strip()
    return PortablePersonaDefinition(
        id=identity,
        revision=revision or persona_revision_for_definition(definition),
        definition=_json_copy(definition),
    )


def parse_portable_persona(value: str | bytes | Mapping[str, Any]) -> PortablePersonaDefinition:
    """Parse and strictly validate a portable persona YAML/dict document."""
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            loaded = yaml.load(value, Loader=_StrictSafeLoader)
        except yaml.YAMLError as exc:
            raise PersonaDocumentError(f"Invalid persona YAML: {exc}") from exc
        if not isinstance(loaded, dict):
            raise PersonaDocumentError("Portable persona document must be a mapping")
        value = loaded
    return PortablePersonaDefinition.from_dict(value)


def portable_persona_yaml(document: PortablePersonaDefinition) -> str:
    """Serialize a portable persona document as stable, human-readable YAML."""
    return yaml.safe_dump(
        document.to_dict(),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def persona_definition_digest(definition: Mapping[str, Any]) -> str:
    """Return the canonical SHA-256 digest for portable persona content."""
    canonical = json.dumps(
        definition,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def persona_revision_for_definition(definition: Mapping[str, Any]) -> str:
    """Return the immutable human-visible revision label for source content."""
    digest = persona_definition_digest(definition).removeprefix("sha256:")
    return f"content-{digest[:16]}"


def resolve_persona_dependencies(
    dependencies: Mapping[str, Mapping[str, Any] | PersonaDependency],
    scoped_definitions: Mapping[str, Mapping[str, Any] | PortablePersonaDefinition],
    source: PortablePersonaSource | None,
) -> dict[str, PortablePersonaDefinition]:
    """Resolve every workflow alias to the exact pinned portable document.

    Workflow-scoped definitions take precedence and are never installed into
    or replaced in a global registry. A present scoped definition that fails
    its pin is a conflict; it does not fall through to a mutable source.
    """
    resolved: dict[str, PortablePersonaDefinition] = {}
    for alias, raw_dependency in dependencies.items():
        normalized_alias = str(alias).strip()
        if not normalized_alias:
            raise PersonaDependencyResolutionError("Persona dependency alias must not be empty")
        dependency = (
            raw_dependency
            if isinstance(raw_dependency, PersonaDependency)
            else _coerce_dependency(raw_dependency)
        )
        raw_scoped = scoped_definitions.get(normalized_alias)
        if raw_scoped is not None:
            document = (
                raw_scoped
                if isinstance(raw_scoped, PortablePersonaDefinition)
                else PortablePersonaDefinition.from_dict(raw_scoped)
            )
            _assert_pin(normalized_alias, dependency, document, location="workflow scope")
            resolved[normalized_alias] = document
            continue
        if source is None:
            raise PersonaDependencyResolutionError(
                f"Persona dependency '{normalized_alias}' has no bundled definition and "
                "no authoritative persona source is configured"
            )
        document = source.load_portable(dependency.id, dependency.revision)
        if document is None:
            raise PersonaDependencyResolutionError(
                f"Persona dependency '{normalized_alias}' requires "
                f"{dependency.id}@{dependency.revision} ({dependency.digest}), but that exact "
                "revision is unavailable"
            )
        _assert_pin(normalized_alias, dependency, document, location="persona source")
        resolved[normalized_alias] = document
    return resolved


def persona_mapping_compatibility_issues(
    required: PortablePersonaDefinition,
    candidate: PortablePersonaDefinition,
) -> list[str]:
    """Describe event/capability incompatibilities for an intentional mapping."""
    issues: list[str] = []
    required_consumes = _string_set((required.definition.get("consumes") or {}).get("event_types"))
    candidate_consumes = _string_set(
        (candidate.definition.get("consumes") or {}).get("event_types")
    )
    missing_consumes = required_consumes - candidate_consumes
    if missing_consumes:
        issues.append(f"does not consume events: {', '.join(sorted(missing_consumes))}")

    required_produces = _produced_events(required.definition)
    candidate_produces = _produced_events(candidate.definition)
    missing_produces = required_produces - candidate_produces
    if missing_produces:
        issues.append(f"does not produce events: {', '.join(sorted(missing_produces))}")

    required_tools = _string_set(required.definition.get("allowed_tools"))
    candidate_tools = _string_set(candidate.definition.get("allowed_tools"))
    missing_tools = required_tools - candidate_tools if candidate_tools else set()
    if missing_tools:
        issues.append(f"does not request capabilities: {', '.join(sorted(missing_tools))}")
    return issues


def _assert_pin(
    alias: str,
    dependency: PersonaDependency,
    document: PortablePersonaDefinition,
    *,
    location: str,
) -> None:
    if document.id != dependency.id or document.revision != dependency.revision:
        raise PersonaDependencyResolutionError(
            f"Persona dependency '{alias}' expected {dependency.id}@{dependency.revision}, "
            f"but {location} contains {document.id}@{document.revision}"
        )
    if document.digest != dependency.digest:
        raise PersonaDependencyResolutionError(
            f"Persona dependency '{alias}' digest conflict: expected {dependency.digest}, "
            f"but {location} contains {document.digest}"
        )


def _coerce_dependency(raw: Any) -> PersonaDependency:
    if isinstance(raw, Mapping):
        return PersonaDependency.from_dict(raw)
    try:
        return PersonaDependency(
            id=str(raw.id),
            revision=str(raw.revision),
            digest=str(raw.digest),
            path=(str(raw.path) if raw.path is not None else None),
        )
    except AttributeError as exc:
        raise PersonaDocumentError("Persona dependency must be a mapping") from exc


def _validate_definition(persona_id: str, definition: Mapping[str, Any]) -> None:
    unknown = set(definition) - _PORTABLE_DEFINITION_FIELDS
    if unknown:
        raise PersonaDocumentError(
            "Portable persona definition contains environment-specific or unsupported field(s): "
            f"{', '.join(sorted(unknown))}"
        )
    name = str(definition.get("name") or "").strip()
    if not name:
        raise PersonaDocumentError("Portable persona definition.name must not be empty")
    if name != persona_id:
        raise PersonaDocumentError(
            f"Portable persona id '{persona_id}' does not match definition.name '{name}'"
        )
    _validate_optional_string(definition, "system_prompt_template")
    _validate_string_list(definition, "allowed_tools")
    _validate_string_list(definition, "forbidden_tools")
    _validate_optional_string(definition, "permission_mode")
    _validate_non_negative_int(definition, "iteration_budget")
    _validate_optional_bool(definition, "stop_on_outcome")
    _validate_llm(definition.get("llm"))
    _validate_event_contract(definition.get("produces"), produces=True)
    _validate_event_contract(definition.get("consumes"), produces=False)
    _validate_fan_in(definition.get("fan_in"))
    try:
        json.dumps(definition, allow_nan=False, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PersonaDocumentError("Portable persona definition must be JSON-serializable") from exc


def _validate_bundle_path(path: str) -> None:
    if not path or "\\" in path:
        raise PersonaDocumentError("Persona bundle path must be a non-empty POSIX relative path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise PersonaDocumentError(f"Unsafe persona bundle path: {path!r}")


def validate_persona_identifier(value: str, *, field: str = "Persona identifier") -> None:
    """Reject values unsafe for paths, URLs, runtime names, and registry keys."""
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise PersonaDocumentError(
            f"{field} must start with an ASCII letter or digit and contain only "
            "letters, digits, '.', '_', or '-'"
        )


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise PersonaDocumentError("Portable persona content must be JSON-serializable") from exc


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _produced_events(definition: Mapping[str, Any]) -> set[str]:
    raw = definition.get("produces")
    if not isinstance(raw, dict):
        return set()
    events = {str(raw.get("event_type") or "").strip()}
    event_map = raw.get("event_type_map")
    if isinstance(event_map, dict):
        events.update(str(value).strip() for value in event_map.values())
    return {event for event in events if event}


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _validate_optional_string(definition: Mapping[str, Any], key: str) -> None:
    if key in definition and not isinstance(definition[key], str):
        raise PersonaDocumentError(f"Portable persona definition.{key} must be a string")


def _validate_string_list(definition: Mapping[str, Any], key: str) -> None:
    if key not in definition:
        return
    value = definition[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PersonaDocumentError(f"Portable persona definition.{key} must be a list of strings")


def _validate_non_negative_int(definition: Mapping[str, Any], key: str) -> None:
    if key not in definition:
        return
    value = definition[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PersonaDocumentError(
            f"Portable persona definition.{key} must be a non-negative integer"
        )


def _validate_optional_bool(definition: Mapping[str, Any], key: str) -> None:
    if key in definition and not isinstance(definition[key], bool):
        raise PersonaDocumentError(f"Portable persona definition.{key} must be a boolean")


def _validate_mapping(
    value: Any,
    *,
    field: str,
    allowed: set[str],
) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PersonaDocumentError(f"Portable persona definition.{field} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise PersonaDocumentError(
            f"Portable persona definition.{field} has unsupported field(s): "
            f"{', '.join(sorted(str(key) for key in unknown))}"
        )
    return value


def _validate_llm(value: Any) -> None:
    llm = _validate_mapping(
        value,
        field="llm",
        allowed={"primary_alias", "thinking_enabled", "max_tokens"},
    )
    if llm is None:
        return
    _validate_optional_string(llm, "primary_alias")
    _validate_optional_bool(llm, "thinking_enabled")
    _validate_non_negative_int(llm, "max_tokens")


def _validate_event_contract(value: Any, *, produces: bool) -> None:
    field = "produces" if produces else "consumes"
    allowed = {"schema"}
    if produces:
        allowed.update({"event_type", "event_type_map"})
    else:
        allowed.update({"event_types", "injects"})
    contract = _validate_mapping(value, field=field, allowed=allowed)
    if contract is None:
        return
    if produces:
        _validate_optional_string(contract, "event_type")
        event_type_map = contract.get("event_type_map")
        if event_type_map is not None:
            if not isinstance(event_type_map, dict) or any(
                not isinstance(key, str) or not isinstance(item, str)
                for key, item in event_type_map.items()
            ):
                raise PersonaDocumentError(
                    "Portable persona definition.produces.event_type_map must map "
                    "strings to strings"
                )
    else:
        _validate_string_list(contract, "event_types")
        _validate_string_list(contract, "injects")
    schema = contract.get("schema")
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise PersonaDocumentError(f"Portable persona definition.{field}.schema must be a mapping")
    for schema_name, raw_schema_field in schema.items():
        if not isinstance(schema_name, str) or not schema_name:
            raise PersonaDocumentError(
                f"Portable persona definition.{field}.schema keys must be non-empty strings"
            )
        schema_field = _validate_mapping(
            raw_schema_field,
            field=f"{field}.schema.{schema_name}",
            allowed={"type", "description", "values", "required"},
        )
        assert schema_field is not None
        _validate_optional_string(schema_field, "type")
        _validate_optional_string(schema_field, "description")
        _validate_string_list(schema_field, "values")
        _validate_optional_bool(schema_field, "required")


def _validate_fan_in(value: Any) -> None:
    fan_in = _validate_mapping(
        value,
        field="fan_in",
        allowed={"strategy", "contributes_to"},
    )
    if fan_in is None:
        return
    _validate_optional_string(fan_in, "strategy")
    _validate_optional_string(fan_in, "contributes_to")
    strategy = fan_in.get("strategy")
    if strategy is not None and strategy not in {"all_must_pass", "any_pass", "majority", "merge"}:
        raise PersonaDocumentError("Portable persona definition.fan_in.strategy is unsupported")
