"""PostgreSQL-backed persona registry with built-in fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
import yaml

from niuu.domain.outcome import OutcomeField
from personas.adapters.loader import FilesystemPersonaAdapter
from personas.domain.permission_mode import PERSONA_API_PERMISSION_MODES
from personas.domain.persona import (
    EXECUTOR_REMOVED_MESSAGE,
    PersonaConfig,
    PersonaConsumes,
    PersonaFanIn,
    PersonaLLMConfig,
    PersonaProduces,
    parse_persona,
    persona_to_yaml,
)
from personas.domain.persona_document import (
    PersonaDocumentError,
    PortablePersonaDefinition,
    persona_definition_from_mapping,
    portable_persona_from_config,
    refuse_tool_restrictions,
)

_ROLE_DEFAULT = "build"
#: A payload handed to ``save_persona`` may carry the persona's whole document under this key
#: (a fork does), as a mapping.
_DOCUMENT_KEY = "document"
#: Stored payload key holding a persona's whole document when it was saved as one (from YAML,
#: or a fork). Present, the document is the persona and the payload's other fields are its
#: summary. It is JSON text, not a nested object, because ``jsonb`` reorders object keys and
#: the order of a persona's outcomes is part of what its author wrote.
_STORED_DOCUMENT_KEY = "document_json"
_COLOR_BY_ROLE = {
    "arbiter": "var(--color-accent-indigo)",
    "audit": "var(--color-accent-red)",
    "autonomy": "var(--color-accent-purple)",
    "build": "var(--color-accent-indigo)",
    "coord": "var(--color-accent-amber)",
    "gate": "var(--color-accent-indigo)",
    "index": "var(--color-accent-purple)",
    "investigate": "var(--color-accent-amber)",
    "knowledge": "var(--color-accent-purple)",
    "observe": "var(--color-accent-cyan)",
    "plan": "var(--color-accent-cyan)",
    "qa": "var(--color-accent-amber)",
    "report": "var(--color-accent-emerald)",
    "review": "var(--color-accent-indigo)",
    "ship": "var(--color-accent-emerald)",
    "verify": "var(--color-accent-amber)",
    "write": "var(--color-accent-emerald)",
}
_RUNTIME_TO_UI_PERMISSION = {
    "workspace-write": "default",
    "workspace_write": "default",
    "read-only": "safe",
    "read_only": "safe",
    "full-access": "loose",
    "full_access": "loose",
}
_FAN_IN_TO_RUNTIME = {
    "all_must_pass": "all_must_pass",
    "any_passes": "any_pass",
    "merge": "merge",
    "quorum": "majority",
    "weighted_score": "majority",
    "first_wins": "any_pass",
}
_RUNTIME_TO_FAN_IN = {
    "all_must_pass": "all_must_pass",
    "any_pass": "any_passes",
    "majority": "quorum",
    "merge": "merge",
}
_BUILTIN_METADATA: dict[str, dict[str, str]] = {
    "coder": {
        "role": "build",
        "color": "var(--color-accent-indigo)",
        "summary": "Writes and edits source code.",
        "description": "Writes and edits source code.",
    },
    "reviewer": {
        "role": "review",
        "color": "var(--color-accent-indigo)",
        "summary": "Reviews code changes and provides feedback.",
        "description": "Reviews code changes and provides feedback.",
    },
    "security-auditor": {
        "role": "review",
        "color": "var(--color-accent-red)",
        "summary": "Periodic deep security audits.",
        "description": "Periodic deep security audits.",
    },
}


@dataclass(frozen=True)
class PersonaView:
    config: PersonaConfig
    payload: dict[str, Any]
    is_builtin: bool
    has_override: bool
    yaml_source: str
    override_source: str | None = None


class PostgresPersonaRegistry:
    """Read/write persona registry with per-user overrides and built-in fallback."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        builtin_loader: FilesystemPersonaAdapter | None = None,
    ) -> None:
        self._pool = pool
        self._builtin_loader = builtin_loader or FilesystemPersonaAdapter(
            persona_dirs=[],
            include_builtin=True,
        )

    async def list_personas(self, owner_id: str, *, source: str = "all") -> list[PersonaView]:
        overrides = await self._load_overrides(owner_id)
        active_builtin_names = set(self._builtin_loader.list_builtin_names())
        custom_override_names = {
            name for name in overrides.keys() if not self._builtin_loader.is_builtin(name)
        }
        names = active_builtin_names | custom_override_names
        views: list[PersonaView] = []

        for name in sorted(names):
            view = self._build_view(owner_id, name, overrides.get(name))
            if view is None:
                continue
            if source == "builtin" and not view.is_builtin:
                continue
            if source == "custom" and view.is_builtin:
                continue
            views.append(view)

        return views

    async def get_persona(self, owner_id: str, name: str) -> PersonaView | None:
        overrides = await self._load_overrides(owner_id, name=name)
        return self._build_view(owner_id, name, overrides.get(name))

    async def save_persona(self, owner_id: str, payload: dict[str, Any]) -> None:
        """Save a persona from its registry payload (the field-by-field form).

        A payload that carries the persona's whole document (``document``, as a view of a
        persona saved from YAML does, and so a fork of one) is saved as that document under
        the payload's name, so nothing the form cannot express is dropped.
        """
        document = payload.get(_DOCUMENT_KEY)
        if document is not None:
            if not isinstance(document, dict):
                raise PersonaDocumentError("A persona payload's document must be a mapping")
            name = str(payload.get("name", "")).strip()
            renamed = persona_definition_from_mapping({**document, "name": name})
            await self._save_document(owner_id, renamed, presentation=payload)
            return
        normalized = _normalize_payload(payload)
        await self._write(owner_id, normalized, _payload_to_config(normalized))

    async def save_persona_document(
        self,
        owner_id: str,
        document: PortablePersonaDefinition,
    ) -> None:
        """Save a persona from its whole document, exactly as written.

        The document is the persona: every field it declares is kept and is what agents and
        workflow pins read. The library's presentation (role, letter, colour) of an existing
        override is kept; its description becomes the summary.
        """
        overrides = await self._load_overrides(owner_id, name=document.id)
        await self._save_document(owner_id, document, presentation=overrides.get(document.id))

    async def _save_document(
        self,
        owner_id: str,
        document: PortablePersonaDefinition,
        *,
        presentation: dict[str, Any] | None,
    ) -> None:
        config = _document_config(document.definition)
        payload = _config_to_payload(config)
        for key in ("role", "letter", "color"):
            value = str((presentation or {}).get(key) or "").strip()
            if value:
                payload[key] = value
        payload["letter"] = _normalize_letter(payload["letter"], config.name)
        if config.description:
            payload["summary"] = config.description
            payload["description"] = config.description
        payload[_STORED_DOCUMENT_KEY] = json.dumps(document.definition, ensure_ascii=False)
        await self._write(owner_id, payload, config)

    async def _write(self, owner_id: str, payload: dict[str, Any], config: PersonaConfig) -> None:
        now = datetime.now(UTC)
        await self._pool.execute(
            """
            INSERT INTO ravn_personas
                (owner_id, name, config_json, runtime_config_json, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)
            ON CONFLICT (owner_id, name) DO UPDATE SET
                config_json = EXCLUDED.config_json,
                runtime_config_json = EXCLUDED.runtime_config_json,
                updated_at = EXCLUDED.updated_at
            """,
            owner_id,
            payload["name"],
            json.dumps(payload),
            json.dumps(config.to_dict()),
            now,
            now,
        )

    async def delete_persona(self, owner_id: str, name: str) -> bool:
        result = await self._pool.execute(
            """
            DELETE FROM ravn_personas
            WHERE owner_id = $1
              AND name = $2
            """,
            owner_id,
            name,
        )
        return result == "DELETE 1"

    async def get_persona_yaml(self, owner_id: str, name: str) -> str | None:
        view = await self.get_persona(owner_id, name)
        if view is None:
            return None
        return persona_to_yaml(view.config)

    async def get_current_portable_persona(
        self,
        owner_id: str,
        persona_id: str,
    ) -> PortablePersonaDefinition | None:
        """Return owner-scoped raw source content with a content revision."""
        overrides = await self._load_overrides(owner_id, name=persona_id)
        override_payload = overrides.get(persona_id)
        builtin_document = self._builtin_loader.load_current_portable(persona_id)
        if override_payload is None:
            return builtin_document
        document = _stored_document(override_payload)
        if document is not None:
            return persona_definition_from_mapping(document)

        builtin_config: PersonaConfig | None = None
        if builtin_document is not None:
            builtin_config = parse_persona(
                yaml.safe_dump(builtin_document.definition, allow_unicode=True, sort_keys=False)
            )
        payload = _normalize_payload(override_payload, fallback=builtin_config)
        return portable_persona_from_config(
            _payload_to_config(payload),
            persona_id=persona_id,
        )

    async def get_portable_persona_revision(
        self,
        owner_id: str,
        persona_id: str,
        revision: str,
    ) -> PortablePersonaDefinition | None:
        """Return the current owner-scoped source only when its revision matches."""
        document = await self.get_current_portable_persona(owner_id, persona_id)
        if document is None or document.revision != revision:
            return None
        return document

    def is_builtin(self, name: str) -> bool:
        return self._builtin_loader.is_builtin(name)

    async def _load_overrides(
        self,
        owner_id: str,
        *,
        name: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        params: list[object] = [owner_id]
        where = "WHERE owner_id = $1"
        if name is not None:
            params.append(name)
            where += " AND name = $2"

        rows = await self._pool.fetch(
            f"""
            SELECT name, config_json
            FROM ravn_personas
            {where}
            ORDER BY updated_at DESC, created_at DESC
            """,
            *params,
        )

        overrides: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = _parse_payload(row["config_json"])
            if payload is not None:
                overrides[row["name"]] = payload
        return overrides

    def _build_view(
        self,
        owner_id: str,
        name: str,
        override_payload: dict[str, Any] | None,
    ) -> PersonaView | None:
        builtin_config = self._builtin_loader.load(name)
        is_builtin = builtin_config is not None
        if override_payload is not None:
            payload = _normalize_payload(override_payload, fallback=builtin_config)
            document = _stored_document(override_payload)
            config = _payload_to_config(payload) if document is None else _document_config(document)
            yaml_source = "[built-in]" if is_builtin else f"[user:{owner_id}]"
            override_source = f"[user:{owner_id}]" if is_builtin else None
            return PersonaView(
                config=config,
                payload=payload,
                is_builtin=is_builtin,
                has_override=True,
                yaml_source=yaml_source,
                override_source=override_source,
            )

        if builtin_config is None:
            return None

        payload = _config_to_payload(builtin_config)
        return PersonaView(
            config=builtin_config,
            payload=payload,
            is_builtin=True,
            has_override=False,
            yaml_source="[built-in]",
        )


def _parse_payload(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        payload = json.loads(raw)
    else:
        payload = dict(raw)
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name", "")).strip()
    if not name:
        return None
    return payload


def _normalize_payload(
    payload: dict[str, Any],
    *,
    fallback: PersonaConfig | None = None,
) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("Persona name is required")
    if "executor" in payload:
        raise PersonaDocumentError(EXECUTOR_REMOVED_MESSAGE.format(name=name))
    refuse_tool_restrictions(name, payload)

    base = _config_to_payload(fallback) if fallback is not None else _default_payload(name)
    normalized = dict(base)

    normalized["name"] = name
    normalized["role"] = str(payload.get("role") or base["role"])
    normalized["letter"] = _normalize_letter(payload.get("letter"), name)
    normalized["color"] = str(payload.get("color") or base["color"])
    normalized["summary"] = str(payload.get("summary") or base["summary"])
    normalized["description"] = str(payload.get("description") or normalized["summary"])
    normalized["system_prompt_template"] = str(
        payload.get("system_prompt_template") or base["system_prompt_template"]
    )
    normalized["permission_mode"] = _normalize_permission_mode(
        payload.get("permission_mode") or base["permission_mode"]
    )
    normalized["iteration_budget"] = _normalize_non_negative_int(
        payload.get("iteration_budget"),
        default=int(base["iteration_budget"]),
    )
    normalized["llm_primary_alias"] = ""
    normalized["llm_thinking_enabled"] = bool(
        payload.get("llm_thinking_enabled", base["llm_thinking_enabled"])
    )
    normalized["llm_max_tokens"] = _normalize_non_negative_int(
        payload.get("llm_max_tokens"),
        default=int(base["llm_max_tokens"]),
    )
    normalized["llm_temperature"] = _normalize_optional_number(payload.get("llm_temperature"))
    normalized["produces_event_type"] = str(
        payload.get("produces_event_type") or base["produces_event_type"]
    )
    normalized["produces_event_map"] = _normalize_str_map(
        payload.get("produces_event_map", base.get("produces_event_map"))
    )
    normalized["produces_when"] = _normalize_str_map(
        payload.get("produces_when", base.get("produces_when"))
    )
    normalized["produces_schema"] = _normalize_schema(payload.get("produces_schema"))
    normalized["consumes_events"] = _normalize_consumes_events(payload.get("consumes_events"))
    normalized["consumes_schema"] = _normalize_schema(payload.get("consumes_schema"))
    normalized["fan_in_strategy"] = _normalize_optional_string(payload.get("fan_in_strategy"))
    normalized["fan_in_params"] = _normalize_params(payload.get("fan_in_params"))
    normalized["mimir_write_routing"] = _normalize_optional_string(
        payload.get("mimir_write_routing")
    )
    return normalized


def _config_to_payload(config: PersonaConfig | None) -> dict[str, Any]:
    if config is None:
        raise ValueError("Config is required")

    meta = _BUILTIN_METADATA.get(config.name, {})
    role = meta.get("role", _ROLE_DEFAULT)
    summary = meta.get("summary", _humanize_name(config.name))
    description = meta.get("description", summary)
    return {
        "name": config.name,
        "role": role,
        "letter": _normalize_letter(None, config.name),
        "color": meta.get("color", _COLOR_BY_ROLE.get(role, _COLOR_BY_ROLE[_ROLE_DEFAULT])),
        "summary": summary,
        "description": description,
        "system_prompt_template": config.system_prompt_template,
        "permission_mode": _normalize_permission_mode(config.permission_mode),
        "iteration_budget": config.iteration_budget,
        "llm_primary_alias": "",
        "llm_thinking_enabled": config.llm.thinking_enabled,
        "llm_max_tokens": config.llm.max_tokens,
        "llm_temperature": None,
        "produces_event_type": config.produces.event_type,
        "produces_event_map": dict(config.produces.event_type_map),
        "produces_when": dict(config.produces.when),
        "produces_schema": {key: field.type for key, field in config.produces.schema.items()},
        "consumes_events": [{"name": name} for name in config.consumes.event_types],
        "consumes_schema": {key: field.type for key, field in config.consumes.schema.items()},
        "fan_in_strategy": _RUNTIME_TO_FAN_IN.get(config.fan_in.strategy),
        "fan_in_params": (
            {"contributes_to": config.fan_in.contributes_to} if config.fan_in.contributes_to else {}
        ),
        "mimir_write_routing": None,
    }


def _payload_to_config(payload: dict[str, Any]) -> PersonaConfig:
    produces_schema = {
        field_name: OutcomeField(
            type=str(field_type),  # type: ignore[arg-type]
            description=field_name,
        )
        for field_name, field_type in payload["produces_schema"].items()
    }
    consumes_schema = {
        field_name: OutcomeField(
            type=str(field_type),  # type: ignore[arg-type]
            description=field_name,
        )
        for field_name, field_type in payload.get("consumes_schema", {}).items()
    }
    consumes_events = payload["consumes_events"]
    injects: list[str] = []
    for event in consumes_events:
        injects.extend(event.get("injects", []))

    fan_in_strategy = payload.get("fan_in_strategy")
    runtime_fan_in = "merge"
    if fan_in_strategy is not None:
        runtime_fan_in = _FAN_IN_TO_RUNTIME.get(str(fan_in_strategy))
        if runtime_fan_in is None:
            raise ValueError(
                f"persona fan_in_strategy {fan_in_strategy!r} is not a fan-in strategy; "
                f"use one of: {', '.join(sorted(_FAN_IN_TO_RUNTIME))}"
            )
    params = payload.get("fan_in_params") or {}
    contributes_to = ""
    if isinstance(params, dict):
        contributes_to = str(params.get("contributes_to", ""))

    return PersonaConfig(
        name=str(payload["name"]),
        system_prompt_template=str(payload["system_prompt_template"]),
        permission_mode=PERSONA_API_PERMISSION_MODES.get(
            str(payload["permission_mode"]),
            str(payload["permission_mode"]),
        ),
        llm=PersonaLLMConfig(
            primary_alias="",
            thinking_enabled=bool(payload["llm_thinking_enabled"]),
            max_tokens=int(payload["llm_max_tokens"]),
        ),
        iteration_budget=int(payload["iteration_budget"]),
        produces=PersonaProduces(
            event_type=str(payload["produces_event_type"]),
            event_type_map=dict(payload.get("produces_event_map") or {}),
            when=dict(payload.get("produces_when") or {}),
            schema=produces_schema,
        ),
        consumes=PersonaConsumes(
            event_types=[
                str(event["name"]) for event in consumes_events if str(event.get("name", ""))
            ],
            injects=sorted(set(injects)),
            schema=consumes_schema,
        ),
        fan_in=PersonaFanIn(
            strategy=runtime_fan_in,  # type: ignore[arg-type]
            contributes_to=contributes_to,
        ),
    )


def _stored_document(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The whole document a stored payload carries, in the order its author wrote it."""
    raw = payload.get(_STORED_DOCUMENT_KEY)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PersonaDocumentError(
            f"Stored persona {payload.get('name')!r} has a document that is not JSON text"
        )
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise PersonaDocumentError(
            f"Stored persona {payload.get('name')!r} document is not a mapping"
        )
    return document


def _document_config(document: object) -> PersonaConfig:
    """Read a stored persona document back as the persona it declares."""
    definition = persona_definition_from_mapping(document)  # type: ignore[arg-type]
    config = parse_persona(
        yaml.safe_dump(definition.definition, allow_unicode=True, sort_keys=False)
    )
    assert config is not None  # a validated definition always has a name
    return config


def _default_payload(name: str) -> dict[str, Any]:
    role = _BUILTIN_METADATA.get(name, {}).get("role", _ROLE_DEFAULT)
    summary = _BUILTIN_METADATA.get(name, {}).get("summary", _humanize_name(name))
    return {
        "name": name,
        "role": role,
        "letter": _normalize_letter(None, name),
        "color": _BUILTIN_METADATA.get(name, {}).get(
            "color",
            _COLOR_BY_ROLE.get(role, _COLOR_BY_ROLE[_ROLE_DEFAULT]),
        ),
        "summary": summary,
        "description": summary,
        "system_prompt_template": "",
        "permission_mode": "default",
        "iteration_budget": 0,
        "llm_primary_alias": "",
        "llm_thinking_enabled": False,
        "llm_max_tokens": 0,
        "llm_temperature": None,
        "produces_event_type": "",
        "produces_event_map": {},
        "produces_when": {},
        "produces_schema": {},
        "consumes_events": [],
        "consumes_schema": {},
        "fan_in_strategy": None,
        "fan_in_params": {},
        "mimir_write_routing": None,
    }


def _normalize_letter(raw: object, name: str) -> str:
    letter = str(raw or "").strip()
    if letter:
        return letter[:1].upper()
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "P"


def _normalize_str_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item)]


def _normalize_str_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }


def _normalize_permission_mode(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return "default"
    return _RUNTIME_TO_UI_PERMISSION.get(value, value)


def _normalize_non_negative_int(raw: object, *, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _normalize_optional_number(raw: object) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _normalize_schema(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        key_str = str(key).strip()
        value_str = str(value).strip()
        if key_str and value_str:
            normalized[key_str] = value_str
    return normalized


def _normalize_consumes_events(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "injects": _normalize_str_list(item.get("injects")),
                "trust": _normalize_optional_number(item.get("trust")),
            }
        )
    return normalized


def _normalize_params(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _normalize_optional_string(raw: object) -> str | None:
    value = str(raw or "").strip()
    return value or None


def _humanize_name(name: str) -> str:
    text = name.replace("-", " ").replace("_", " ").strip()
    if not text:
        return "Custom persona"
    return text[:1].upper() + text[1:]
