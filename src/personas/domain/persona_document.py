"""Portable, revision-pinned persona documents.

The portable document contains only source-authored persona behaviour. Runtime
prompt injection and environment bindings (credentials, paths, and provider
connections) are deliberately outside this contract.
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

from personas.domain.permission_mode import parse_permission_mode

PERSONA_DOCUMENT_SCHEMA_VERSION = 1
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_PORTABLE_DEFINITION_FIELDS = frozenset(
    {
        "name",
        "description",
        "system_prompt_template",
        "permission_mode",
        "llm",
        "iteration_budget",
        "produces",
        "consumes",
        "fan_in",
        "stop_on_outcome",
        "delivery_workspace_actions",
        "voice",
    }
)


#: Persona fields that restricted a role's tools. Restricting tools per harness is not available
#: yet, so a persona that still carries one is refused rather than read with it ignored.
REMOVED_TOOL_RESTRICTION_FIELDS: tuple[str, ...] = ("allowed_tools", "forbidden_tools")


class PersonaDocumentError(ValueError):
    """A portable persona document or dependency pin is invalid."""


def refuse_tool_restrictions(persona: str, document: Mapping[str, Any]) -> None:
    """Raise :class:`PersonaDocumentError` when the persona still carries a tool restriction.

    Any presence of ``allowed_tools`` or ``forbidden_tools`` is refused, an empty list included:
    the persona must be corrected, not read with the restriction dropped.
    """
    present = [name for name in REMOVED_TOOL_RESTRICTION_FIELDS if name in document]
    if not present:
        return
    fields = " and ".join(present)
    verb = "is" if len(present) == 1 else "are"
    raise PersonaDocumentError(
        f"persona {persona}: {fields} {verb} not supported: per-harness tool restrictions are "
        "not available yet; remove allowed_tools (and forbidden_tools) from the persona"
    )


class PersonaDependencyResolutionError(PersonaDocumentError):
    """An exact persona dependency cannot be resolved."""


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
    """Exact workflow dependency on a persona source definition."""

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

    ``PersonaConfig.to_dict`` is the canonical lossless source serializer. Callers
    must pass a raw source config, before outcome prompt instructions or other
    runtime prompt additions are injected.
    """
    raw = config.to_dict()
    if not isinstance(raw, dict):
        raise PersonaDocumentError("PersonaConfig.to_dict() must return a mapping")
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


def persona_definition_from_mapping(definition: Mapping[str, Any]) -> PortablePersonaDefinition:
    """Validate a persona definition as its author wrote it and give it its content revision.

    The definition is the persona itself (``name``, ``description``, ``produces``, …), not the
    portable wrapper around it. It is checked as strictly as a pinned persona: an unknown field,
    a verdict rule for a verdict the persona does not map, or a mode no enforcer knows is refused,
    naming the field, rather than saved with it dropped.
    """
    if not isinstance(definition, Mapping):
        raise PersonaDocumentError("A persona is a mapping of its fields, starting with its name")
    name = str(definition.get("name") or "").strip()
    if not name:
        raise PersonaDocumentError("A persona needs a name")
    _validate_definition(name, definition)
    copied = _json_copy(dict(definition))
    return PortablePersonaDefinition(
        id=name,
        revision=persona_revision_for_definition(copied),
        definition=copied,
    )


def parse_persona_definition_yaml(text: str) -> PortablePersonaDefinition:
    """Parse persona YAML as its author wrote it; see :func:`persona_definition_from_mapping`."""
    try:
        loaded = yaml.load(text, Loader=_StrictSafeLoader)
    except yaml.YAMLError as exc:
        raise PersonaDocumentError(f"Invalid persona YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PersonaDocumentError("A persona is a YAML mapping of its fields, starting with name")
    return persona_definition_from_mapping(loaded)


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
    """Describe event incompatibilities for an intentional mapping."""
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
    refuse_tool_restrictions(persona_id, definition)
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
    _validate_optional_string(definition, "description")
    _validate_optional_string(definition, "system_prompt_template")
    _validate_optional_string(definition, "permission_mode")
    _validate_permission_mode(persona_id, definition)
    _validate_non_negative_int(definition, "iteration_budget")
    _validate_optional_bool(definition, "stop_on_outcome")
    _validate_string_list(definition, "delivery_workspace_actions")
    _validate_llm(definition.get("llm"))
    _validate_event_contract(definition.get("produces"), produces=True)
    _validate_event_contract(definition.get("consumes"), produces=False)
    _validate_fan_in(definition.get("fan_in"))
    _validate_voice(definition.get("voice"))
    try:
        json.dumps(definition, allow_nan=False, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PersonaDocumentError("Portable persona definition must be JSON-serializable") from exc


def _validate_permission_mode(persona_id: str, definition: Mapping[str, Any]) -> None:
    """Refuse a permission mode no enforcer knows; either spelling of a known one is valid.

    The digest is over the spelling the document was authored with, so existing pins stay
    valid; enforcement parses the mode (``parse_permission_mode``) and never compares text.
    """
    mode = definition.get("permission_mode")
    if not mode:
        return
    try:
        parse_permission_mode(mode)
    except ValueError as exc:
        raise PersonaDocumentError(f"Portable persona '{persona_id}': {exc}") from exc


def _validate_voice(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise PersonaDocumentError("Persona voice must be a mapping")
    unknown = set(raw) - {"voices", "style", "tools", "grok"}
    if unknown:
        raise PersonaDocumentError(
            f"Persona voice has unsupported field(s): {', '.join(sorted(unknown))}"
        )
    voices = raw.get("voices")
    if not isinstance(voices, Mapping) or not voices:
        raise PersonaDocumentError("Persona voice.voices must map a provider to a voice name")
    if not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in voices.items()):
        raise PersonaDocumentError("Persona voice.voices must map non-empty strings")
    _validate_optional_string(raw, "style")
    _validate_string_list(raw, "tools")
    grok = raw.get("grok")
    if grok is not None and not isinstance(grok, Mapping):
        raise PersonaDocumentError("Persona voice.grok must be a mapping")


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


def produced_event_types(definition: Mapping[str, Any]) -> set[str]:
    """Every event type the persona declares it produces: its default and its mapped ones."""
    raw = definition.get("produces")
    if not isinstance(raw, dict):
        return set()
    events = {str(raw.get("event_type") or "").strip()}
    event_map = raw.get("event_type_map")
    if isinstance(event_map, dict):
        events.update(str(value).strip() for value in event_map.values())
    return {event for event in events if event}


def consumed_event_types(definition: Mapping[str, Any]) -> set[str]:
    """Every event type the persona declares it consumes."""
    raw = definition.get("consumes")
    if not isinstance(raw, dict):
        return set()
    return _string_set(raw.get("event_types"))


_produced_events = produced_event_types


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


def _validate_verdict_guidance(when: Any, event_type_map: Mapping[str, Any]) -> None:
    """``produces.when``: for each verdict the persona maps, when to report it, in words."""
    if when is None:
        return
    if not isinstance(when, dict):
        raise PersonaDocumentError(
            "Portable persona definition.produces.when must map each verdict to when to report it"
        )
    for verdict, text in when.items():
        if not isinstance(verdict, str) or (event_type_map and verdict not in event_type_map):
            raise PersonaDocumentError(
                f"Portable persona definition.produces.when names verdict {verdict!r}, which "
                f"produces.event_type_map does not map; the verdicts are {sorted(event_type_map)}"
            )
        if not isinstance(text, str) or not text.strip():
            raise PersonaDocumentError(
                f"Portable persona definition.produces.when.{verdict} must say when to report it"
            )


def _validate_event_contract(value: Any, *, produces: bool) -> None:
    field = "produces" if produces else "consumes"
    allowed = {"schema"}
    if produces:
        allowed.update({"event_type", "event_type_map", "when"})
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
        _validate_verdict_guidance(contract.get("when"), event_type_map or {})
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
        if schema_field is None:
            raise PersonaDocumentError(
                f"Portable persona definition.{field}.schema.{schema_name} must be a mapping"
            )
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
