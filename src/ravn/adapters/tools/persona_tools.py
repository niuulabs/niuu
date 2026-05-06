"""Persona tools — validate and save persona YAML configs.

Two tools are provided:

* ``persona_validate`` — validate a persona YAML string, returning errors or a
  summary.  No side effects; safe to call repeatedly during creation.
* ``persona_save``     — validate then write a persona YAML file to disk.
  Verifies round-trip loading before confirming success.

Permission model
----------------
``persona_validate`` uses ``ravn:read`` — no side effects.
``persona_save`` uses ``ravn:write`` — creates/overwrites a file on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml as _yaml

from ravn.adapters.personas.loader import _VALID_FAN_IN_STRATEGIES
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_VALID_PERMISSION_MODES = {"read-only", "workspace-write", "full-access"}
_VALID_LLM_ALIASES = {"balanced", "powerful", "fast"}
_VALID_OUTCOME_FIELD_TYPES = {"string", "number", "boolean", "enum"}

_DEFAULT_PERSONAS_DIR = Path.home() / ".ravn" / "personas"


def _validate_yaml(yaml_content: str) -> tuple[list[str], list[str], dict | None]:
    """Return (errors, warnings, parsed_dict) for *yaml_content*.

    ``parsed_dict`` is the already-parsed YAML mapping when validation
    succeeds, so callers can use it directly without re-parsing.
    It is ``None`` whenever ``errors`` is non-empty.

    Errors are fatal (save will be refused).
    Warnings are non-fatal advisory messages.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not yaml_content or not yaml_content.strip():
        errors.append("YAML content is empty.")
        return errors, warnings, None

    try:
        raw = _yaml.safe_load(yaml_content)
    except _yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            errors.append(
                f"YAML syntax error at line {mark.line + 1}, "
                f"column {mark.column + 1}: {exc.problem}"
            )
        else:
            errors.append(f"YAML syntax error: {exc}")
        return errors, warnings, None

    if not isinstance(raw, dict):
        errors.append("YAML must be a mapping (dict), got a different type.")
        return errors, warnings, None

    name = str(raw.get("name", "")).strip()
    if not name:
        errors.append("Missing required field: 'name' must be a non-empty string.")

    system_prompt = str(raw.get("system_prompt_template", "")).strip()
    if not system_prompt:
        warnings.append("'system_prompt_template' is empty — the agent will have no identity.")

    permission_mode = str(raw.get("permission_mode", "")).strip()
    if permission_mode and permission_mode not in _VALID_PERMISSION_MODES:
        warnings.append(
            f"Unknown 'permission_mode': {permission_mode!r}. "
            f"Known modes: {sorted(_VALID_PERMISSION_MODES)}."
        )

    llm_raw = raw.get("llm")
    if isinstance(llm_raw, dict):
        alias = str(llm_raw.get("primary_alias", "")).strip()
        if alias and alias not in _VALID_LLM_ALIASES:
            warnings.append(
                f"Unknown 'llm.primary_alias': {alias!r}. "
                f"Known aliases: {sorted(_VALID_LLM_ALIASES)}."
            )

    fan_in_raw = raw.get("fan_in")
    if isinstance(fan_in_raw, dict):
        strategy = str(fan_in_raw.get("strategy", "")).strip()
        if strategy and strategy not in _VALID_FAN_IN_STRATEGIES:
            errors.append(
                f"Invalid 'fan_in.strategy': {strategy!r}. "
                f"Must be one of: {sorted(_VALID_FAN_IN_STRATEGIES)}."
            )

    produces_raw = raw.get("produces")
    if isinstance(produces_raw, dict):
        schema_raw = produces_raw.get("schema")
        if isinstance(schema_raw, dict):
            for field_name, field_def in schema_raw.items():
                if not isinstance(field_def, dict):
                    errors.append(
                        f"'produces.schema.{field_name}' must be a mapping, "
                        f"got {type(field_def).__name__}."
                    )
                    continue
                field_type = str(field_def.get("type", "")).strip()
                if field_type and field_type not in _VALID_OUTCOME_FIELD_TYPES:
                    errors.append(
                        f"'produces.schema.{field_name}.type': {field_type!r} is not a valid "
                        f"OutcomeField type. Must be one of: {sorted(_VALID_OUTCOME_FIELD_TYPES)}."
                    )
                if field_type == "enum":
                    values = field_def.get("values") or field_def.get("enum_values")
                    if not isinstance(values, list) or not values:
                        errors.append(
                            f"'produces.schema.{field_name}' has type 'enum' but no 'values' list."
                        )

    if errors:
        return errors, warnings, None

    from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

    parsed = FilesystemPersonaAdapter.parse(yaml_content)
    if parsed is None:
        errors.append(
            "FilesystemPersonaAdapter.parse() returned None — the YAML is structurally invalid. "
            "Ensure 'name' is present and the document is a valid YAML mapping."
        )
        return errors, warnings, None

    return errors, warnings, raw


def _format_validation_result(
    errors: list[str],
    warnings: list[str],
    name: str = "",
) -> tuple[str, bool]:
    """Return (message, is_error) for the validation result."""
    if errors:
        lines = ["Validation failed:"]
        for err in errors:
            lines.append(f"  ✗ {err}")
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for warn in warnings:
                lines.append(f"  ⚠ {warn}")
        return "\n".join(lines), True

    lines = [f"Validation passed: persona '{name}' is valid."]
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warn in warnings:
            lines.append(f"  ⚠ {warn}")
    return "\n".join(lines), False


# ---------------------------------------------------------------------------
# PersonaValidateTool
# ---------------------------------------------------------------------------


class PersonaValidateTool(ToolPort):
    """Validate a persona YAML string without writing anything.

    Checks syntax, required fields, known enum values, and verifies that
    ``FilesystemPersonaAdapter.parse()`` can parse the result.  Returns a success
    summary or detailed diagnostic messages with line numbers.
    """

    @property
    def name(self) -> str:
        return "persona_validate"

    @property
    def description(self) -> str:
        return (
            "Validate a persona YAML string without saving it. "
            "Checks syntax, required fields (name), known permission modes, "
            "LLM aliases, fan_in strategies, and OutcomeField types. "
            "Returns a success summary or detailed error messages. "
            "Call this before persona_save to catch problems early."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "yaml_content": {
                    "type": "string",
                    "description": "Full persona YAML content to validate.",
                },
            },
            "required": ["yaml_content"],
        }

    @property
    def required_permission(self) -> str:
        return "ravn:read"

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        yaml_content: str = input.get("yaml_content") or ""

        errors, warnings, raw = _validate_yaml(yaml_content)

        name = str(raw.get("name", "")).strip() if raw is not None else ""
        message, is_error = _format_validation_result(errors, warnings, name)
        return ToolResult(tool_call_id="", content=message, is_error=is_error)


# ---------------------------------------------------------------------------
# PersonaSaveTool
# ---------------------------------------------------------------------------


class PersonaSaveTool(ToolPort):
    """Validate and save a persona YAML file to disk.

    Validates the YAML first (same checks as ``persona_validate``).
    On success, writes ``<name>.yaml`` to the target directory, then
    verifies the round-trip by loading it back with ``FilesystemPersonaAdapter``.

    The optional ``directory`` parameter lets you save to a project-local
    ``.ravn/personas/`` instead of the default ``~/.ravn/personas/``.
    """

    @property
    def name(self) -> str:
        return "persona_save"

    @property
    def description(self) -> str:
        return (
            "Validate and save a persona YAML file to disk. "
            "Validates first; fails fast with diagnostics on any error. "
            "Writes <name>.yaml to ~/.ravn/personas/ by default. "
            "Pass 'directory' to save to a project-local .ravn/personas/ instead. "
            "Verifies round-trip loading after writing."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "yaml_content": {
                    "type": "string",
                    "description": "Full persona YAML content to save.",
                },
                "directory": {
                    "type": "string",
                    "description": (
                        "Optional target directory path. "
                        "Defaults to ~/.ravn/personas/. "
                        "Use a project-local path like .ravn/personas/ for project-scoped personas."
                    ),
                },
            },
            "required": ["yaml_content"],
        }

    @property
    def required_permission(self) -> str:
        return "ravn:write"

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        yaml_content: str = input.get("yaml_content") or ""
        directory_str: str = (input.get("directory") or "").strip()

        errors, warnings, raw = _validate_yaml(yaml_content)
        if errors:
            message, _ = _format_validation_result(errors, warnings)
            return ToolResult(tool_call_id="", content=message, is_error=True)

        assert raw is not None  # guaranteed when errors is empty
        name = str(raw.get("name", "")).strip()

        target_dir = Path(directory_str).expanduser() if directory_str else _DEFAULT_PERSONAS_DIR
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult(
                tool_call_id="",
                content=f"Failed to create directory {target_dir}: {exc}",
                is_error=True,
            )

        dest = target_dir / f"{name}.yaml"
        try:
            dest.write_text(yaml_content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                tool_call_id="",
                content=f"Failed to write {dest}: {exc}",
                is_error=True,
            )

        from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

        loader = FilesystemPersonaAdapter(
            persona_dirs=[str(target_dir)],
            include_builtin=False,
        )
        loaded = loader.load(name)
        if loaded is None:
            dest.unlink(missing_ok=True)
            return ToolResult(
                tool_call_id="",
                content=(
                    f"Round-trip verification failed: adapter could not load '{name}' "
                    f"from {dest}. The file was not saved."
                ),
                is_error=True,
            )

        lines = [f"Persona '{name}' saved to {dest}."]
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for warn in warnings:
                lines.append(f"  ⚠ {warn}")
        return ToolResult(tool_call_id="", content="\n".join(lines))
