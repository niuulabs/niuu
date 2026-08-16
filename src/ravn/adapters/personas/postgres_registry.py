"""PostgreSQL-backed persona registry with built-in fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

from niuu.domain.outcome import OutcomeField
from ravn.adapters.personas.loader import (
    FilesystemPersonaAdapter,
    PersonaConfig,
    PersonaConsumes,
    PersonaExecutorConfig,
    PersonaFanIn,
    PersonaLLMConfig,
    PersonaProduces,
    _sanitize_executor_kwargs,
)

_ROLE_DEFAULT = "build"
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
_UI_TO_RUNTIME_PERMISSION = {
    "default": "workspace-write",
    "safe": "read-only",
    "loose": "full-access",
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
    "architect": {
        "role": "plan",
        "color": "var(--color-accent-cyan)",
        "summary": "High-level design and planning persona.",
        "description": "High-level design and planning persona.",
    },
    "autonomous-agent": {
        "role": "autonomy",
        "color": "var(--color-accent-purple)",
        "summary": "Fully autonomous general-purpose agent.",
        "description": "Fully autonomous general-purpose agent.",
    },
    "coder": {
        "role": "build",
        "color": "var(--color-accent-indigo)",
        "summary": "Writes and edits source code.",
        "description": "Writes and edits source code.",
    },
    "coding-agent": {
        "role": "build",
        "color": "var(--color-accent-indigo)",
        "summary": "Focused coding agent for implementation work.",
        "description": "Focused coding agent for implementation work.",
    },
    "coordinator": {
        "role": "coord",
        "color": "var(--color-accent-amber)",
        "summary": "Orchestrates multi-step workflows.",
        "description": "Orchestrates multi-step workflows.",
    },
    "decomposer": {
        "role": "plan",
        "color": "var(--color-accent-cyan)",
        "summary": "Breaks goals into structured plans and runs.",
        "description": "Breaks goals into structured plans and runs.",
    },
    "draft-a-note": {
        "role": "write",
        "color": "var(--color-accent-emerald)",
        "summary": "Drafts concise notes and summaries.",
        "description": "Drafts concise notes and summaries.",
    },
    "health-auditor": {
        "role": "observe",
        "color": "var(--color-accent-cyan)",
        "summary": "Periodically audits system health metrics.",
        "description": "Periodically audits system health metrics.",
    },
    "investigator": {
        "role": "investigate",
        "color": "var(--color-accent-amber)",
        "summary": "Root-cause analysis for incidents and bugs.",
        "description": "Root-cause analysis for incidents and bugs.",
    },
    "mimir-curator": {
        "role": "knowledge",
        "color": "var(--color-accent-purple)",
        "summary": "Curates and indexes knowledge into Mimir.",
        "description": "Curates and indexes knowledge into Mimir.",
    },
    "mimir-memory-curator": {
        "role": "knowledge",
        "color": "var(--color-accent-purple)",
        "summary": "Transforms ingested memory sources into Mimir wiki knowledge.",
        "description": "Transforms ingested memory sources into Mimir wiki knowledge.",
    },
    "mimir-warden": {
        "role": "knowledge",
        "color": "var(--color-accent-purple)",
        "summary": "Long-lived Mimir warden for curation, refresh, and dream-cycle maintenance.",
        "description": (
            "Long-lived Mimir warden for curation, refresh, and dream-cycle maintenance."
        ),
    },
    "office-hours": {
        "role": "report",
        "color": "var(--color-accent-emerald)",
        "summary": "Handles interactive support and office-hours style help.",
        "description": "Handles interactive support and office-hours style help.",
    },
    "planning-agent": {
        "role": "plan",
        "color": "var(--color-accent-cyan)",
        "summary": "Decomposes goals into actionable plans.",
        "description": "Decomposes goals into actionable plans.",
    },
    "produce-recap": {
        "role": "report",
        "color": "var(--color-accent-emerald)",
        "summary": "Produces concise recaps of completed work.",
        "description": "Produces concise recaps of completed work.",
    },
    "qa-agent": {
        "role": "qa",
        "color": "var(--color-accent-amber)",
        "summary": "Runs test suites and validates code quality.",
        "description": "Runs test suites and validates code quality.",
    },
    "run-executor": {
        "role": "build",
        "color": "var(--color-accent-indigo)",
        "summary": "Executes run tasks against prepared work items.",
        "description": "Executes run tasks against prepared work items.",
    },
    "reporter": {
        "role": "report",
        "color": "var(--color-accent-emerald)",
        "summary": "Produces status reports and summaries.",
        "description": "Produces status reports and summaries.",
    },
    "research-agent": {
        "role": "investigate",
        "color": "var(--color-accent-cyan)",
        "summary": "Researches topics and synthesizes findings.",
        "description": "Researches topics and synthesizes findings.",
    },
    "research-and-distill": {
        "role": "investigate",
        "color": "var(--color-accent-cyan)",
        "summary": "Researches and distills findings into concise outputs.",
        "description": "Researches and distills findings into concise outputs.",
    },
    "retro-analyst": {
        "role": "observe",
        "color": "var(--color-accent-cyan)",
        "summary": "Runs retrospective analysis on completed work.",
        "description": "Runs retrospective analysis on completed work.",
    },
    "reviewer": {
        "role": "review",
        "color": "var(--color-accent-indigo)",
        "summary": "Reviews code changes and provides feedback.",
        "description": "Reviews code changes and provides feedback.",
    },
    "security": {
        "role": "review",
        "color": "var(--color-accent-red)",
        "summary": "Performs security-focused reviews.",
        "description": "Performs security-focused reviews.",
    },
    "security-auditor": {
        "role": "review",
        "color": "var(--color-accent-red)",
        "summary": "Periodic deep security audits.",
        "description": "Periodic deep security audits.",
    },
    "ship-agent": {
        "role": "ship",
        "color": "var(--color-accent-emerald)",
        "summary": "Handles release and shipping workflows.",
        "description": "Handles release and shipping workflows.",
    },
    "verifier": {
        "role": "verify",
        "color": "var(--color-accent-amber)",
        "summary": "Holistic verification across code, tests, and docs.",
        "description": "Holistic verification across code, tests, and docs.",
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
        normalized = _normalize_payload(payload)
        config = _payload_to_config(normalized)
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
            normalized["name"],
            json.dumps(normalized),
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
        return FilesystemPersonaAdapter.to_yaml(view.config)

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
            config = _payload_to_config(payload)
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
    normalized["allowed_tools"] = _normalize_str_list(payload.get("allowed_tools"))
    normalized["forbidden_tools"] = _normalize_str_list(payload.get("forbidden_tools"))
    normalized["permission_mode"] = _normalize_permission_mode(
        payload.get("permission_mode") or base["permission_mode"]
    )
    normalized["executor"] = _normalize_executor(payload.get("executor"), base["executor"])
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
        "allowed_tools": list(config.allowed_tools),
        "forbidden_tools": list(config.forbidden_tools),
        "permission_mode": _normalize_permission_mode(config.permission_mode),
        "executor": {
            "adapter": config.executor.adapter,
            "kwargs": _sanitize_executor_kwargs(config.executor.kwargs),
        },
        "iteration_budget": config.iteration_budget,
        "llm_primary_alias": "",
        "llm_thinking_enabled": config.llm.thinking_enabled,
        "llm_max_tokens": config.llm.max_tokens,
        "llm_temperature": None,
        "produces_event_type": config.produces.event_type,
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
    runtime_fan_in = _FAN_IN_TO_RUNTIME.get(str(fan_in_strategy), "merge")
    params = payload.get("fan_in_params") or {}
    contributes_to = ""
    if isinstance(params, dict):
        contributes_to = str(params.get("contributes_to", ""))

    return PersonaConfig(
        name=str(payload["name"]),
        system_prompt_template=str(payload["system_prompt_template"]),
        allowed_tools=list(payload["allowed_tools"]),
        forbidden_tools=list(payload["forbidden_tools"]),
        permission_mode=_UI_TO_RUNTIME_PERMISSION.get(
            str(payload["permission_mode"]),
            str(payload["permission_mode"]),
        ),
        executor=PersonaExecutorConfig(
            adapter=str((payload.get("executor") or {}).get("adapter", "")),
            kwargs=dict((payload.get("executor") or {}).get("kwargs") or {}),
        ),
        llm=PersonaLLMConfig(
            primary_alias="",
            thinking_enabled=bool(payload["llm_thinking_enabled"]),
            max_tokens=int(payload["llm_max_tokens"]),
        ),
        iteration_budget=int(payload["iteration_budget"]),
        produces=PersonaProduces(
            event_type=str(payload["produces_event_type"]),
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
        "allowed_tools": [],
        "forbidden_tools": [],
        "permission_mode": "default",
        "executor": {"adapter": "", "kwargs": {}},
        "iteration_budget": 0,
        "llm_primary_alias": "",
        "llm_thinking_enabled": False,
        "llm_max_tokens": 0,
        "llm_temperature": None,
        "produces_event_type": "",
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


def _normalize_permission_mode(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return "default"
    return _RUNTIME_TO_UI_PERMISSION.get(value, value)


def _normalize_executor(raw: object, fallback: object) -> dict[str, Any]:
    base = {"adapter": "", "kwargs": {}}
    if isinstance(fallback, dict):
        base["adapter"] = str(fallback.get("adapter", "")).strip()
        kwargs = fallback.get("kwargs")
        if isinstance(kwargs, dict):
            base["kwargs"] = {str(key): value for key, value in kwargs.items()}

    if not isinstance(raw, dict):
        return base

    adapter = str(raw.get("adapter", "")).strip()
    kwargs = raw.get("kwargs")
    normalized_kwargs: dict[str, Any] = {}
    if isinstance(kwargs, dict):
        normalized_kwargs = _sanitize_executor_kwargs(
            {str(key): value for key, value in kwargs.items()}
        )

    if not adapter and not normalized_kwargs:
        return {"adapter": "", "kwargs": {}}

    return {
        "adapter": adapter,
        "kwargs": normalized_kwargs,
    }


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
