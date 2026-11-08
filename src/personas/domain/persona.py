"""The persona model: a role a worker plays in a workflow or team.

A persona says what a role is: its prompt, the tools it may and may not use, its
permission mode, the events it consumes and produces, and how its output combines
with parallel peers. It does not say which harness runs the role; the workflow step
or spec decides that.

Persona YAML format::

    name: coding-agent
    system_prompt_template: |
      You are a focused coding agent. ...
    allowed_tools: [file_read, file_write, shell, web_fetch, plan, "mimir:search"]
    forbidden_tools: [web_search]
    permission_mode: workspace_write
    llm:
      thinking_enabled: true
    iteration_budget: 40
    produces:
      event_type: review.completed
      schema:
        verdict:
          type: enum
          values: [pass, fail, needs_changes]
        summary:
          type: string
    consumes:
      event_types: [code.changed, review.requested]
      injects: [repo, branch, diff_url]
    fan_in:
      strategy: all_must_pass
      contributes_to: review.verdict
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

from niuu.domain.outcome import OutcomeField, OutcomeSchema, generate_outcome_instruction
from personas.domain.permission_mode import PermissionMode, parse_optional_permission_mode
from personas.domain.persona_document import PersonaDocumentError
from personas.domain.tool_vocabulary import validate_tool_names

EXECUTOR_REMOVED_MESSAGE = (
    "Persona {name!r} declares an 'executor' block. A persona no longer names the "
    "harness that runs it; the workflow step or spec chooses that. Remove the "
    "'executor' key from the persona document."
)


@dataclass
class PersonaLLMConfig:
    """Non-model LLM behavior settings embedded in a persona."""

    primary_alias: str = ""
    thinking_enabled: bool = False
    max_tokens: int = 0  # 0 = use settings default


@dataclass
class PersonaVoiceConfig:
    """How the persona sounds and what it may do on a realtime voice call.

    ``voices`` maps a realtime provider (``grok-voice``, ``openai-realtime``) to the
    provider's voice name; a persona with no voices does not take voice calls.
    ``style`` is how the persona speaks. ``tools`` names the tools a voice call may
    use, inside ``allowed_tools``; empty means none. ``grok`` holds Grok Voice
    session fields (reasoning, turn detection, transcription key terms) sent as is.
    """

    voices: dict[str, str] = field(default_factory=dict)
    style: str = ""
    tools: list[str] = field(default_factory=list)
    grok: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.voices)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"voices": dict(self.voices)}
        if self.style:
            d["style"] = self.style
        if self.tools:
            d["tools"] = list(self.tools)
        if self.grok:
            d["grok"] = dict(self.grok)
        return d


@dataclass
class PersonaProduces:
    """What this persona outputs when it completes.

    event_type: Default event type to publish (used if event_type_map doesn't match)
    event_type_map: Maps outcome field values to event types, e.g.:
        {"pass": "review.passed", "needs_changes": "review.changes_requested"}
        The map is checked against the 'verdict' field in the outcome.
    schema: Expected fields in the outcome block
    """

    event_type: str = ""
    event_type_map: dict[str, str] = field(default_factory=dict)
    schema: dict[str, OutcomeField] = field(default_factory=dict)


@dataclass
class PersonaConsumes:
    """What input this persona expects from previous stages."""

    event_types: list[str] = field(default_factory=list)
    injects: list[str] = field(default_factory=list)
    schema: dict[str, OutcomeField] = field(default_factory=dict)


@dataclass
class PersonaFanIn:
    """How this persona's output combines with parallel peers."""

    strategy: Literal["all_must_pass", "any_pass", "majority", "merge"] = "merge"
    contributes_to: str = ""


@dataclass
class PersonaConfig:
    """A fully-resolved persona configuration.

    Fields left at their zero-value (empty string, empty list, 0) are
    considered "unset" and will not override Settings defaults when the persona
    is applied.

    ``permission_mode`` keeps the spelling the persona was authored with
    (``read-only`` or ``read_only``) because persona content digests — and the
    workflow pins built on them — are computed from it.  Enforcement must read
    ``parsed_permission_mode``, which is parsed once at construction; an
    unrecognised mode raises here, so no persona can exist with one.
    """

    name: str
    system_prompt_template: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    permission_mode: str = ""
    parsed_permission_mode: PermissionMode | None = field(init=False, default=None, compare=False)
    llm: PersonaLLMConfig = field(default_factory=PersonaLLMConfig)
    iteration_budget: int = 0
    produces: PersonaProduces = field(default_factory=PersonaProduces)
    consumes: PersonaConsumes = field(default_factory=PersonaConsumes)
    fan_in: PersonaFanIn = field(default_factory=PersonaFanIn)
    # Legacy compatibility field. Outcomes never suppress requested tool calls.
    stop_on_outcome: bool = False
    # Explicit allowlist of delivery_workspace operations ("allocate", "verify",
    # "integrate", "inspect") this persona's own document grants. Read directly
    # off the loaded persona document rather than off `name` or a workflow's
    # executionContract, so a workflow that maps a dependency to this persona
    # under a different alias (InlinePersonaAdapter.load only overwrites
    # `name`) cannot silently widen it. Empty means the persona was never
    # granted any delivery_workspace action; a persona that has the
    # delivery_workspace tool but declares no actions here gets none.
    delivery_workspace_actions: list[str] = field(default_factory=list)
    # Realtime voice calls (``gateway.channels.voice``); unset means no voice.
    voice: PersonaVoiceConfig = field(default_factory=PersonaVoiceConfig)

    def __post_init__(self) -> None:
        try:
            self.parsed_permission_mode = parse_optional_permission_mode(self.permission_mode)
        except ValueError as exc:
            raise ValueError(f"Persona {self.name!r}: {exc}") from exc

    def to_dict(self) -> dict:
        """Serialize to a dict compatible with :meth:`FilesystemPersonaAdapter.parse`.

        Zero-value fields (empty string, empty list, ``0``, ``False``) are
        omitted to keep the resulting YAML clean.  Nested dataclasses are
        serialized recursively.
        """
        d: dict = {"name": self.name}

        if self.system_prompt_template:
            d["system_prompt_template"] = self.system_prompt_template
        if self.allowed_tools:
            d["allowed_tools"] = list(self.allowed_tools)
        if self.forbidden_tools:
            d["forbidden_tools"] = list(self.forbidden_tools)
        if self.permission_mode:
            d["permission_mode"] = str(self.permission_mode)

        llm_dict: dict = {}
        if self.llm.primary_alias:
            llm_dict["primary_alias"] = self.llm.primary_alias
        if self.llm.thinking_enabled:
            llm_dict["thinking_enabled"] = self.llm.thinking_enabled
        if self.llm.max_tokens:
            llm_dict["max_tokens"] = self.llm.max_tokens
        if llm_dict:
            d["llm"] = llm_dict

        if self.iteration_budget:
            d["iteration_budget"] = self.iteration_budget

        if self.produces.event_type or self.produces.event_type_map or self.produces.schema:
            produces_dict: dict = {}
            if self.produces.event_type:
                produces_dict["event_type"] = self.produces.event_type
            if self.produces.event_type_map:
                produces_dict["event_type_map"] = dict(self.produces.event_type_map)
            if self.produces.schema:
                schema_dict: dict = {}
                for fname, f in self.produces.schema.items():
                    field_dict: dict = {"type": f.type, "description": f.description}
                    if f.type == "enum" and f.enum_values:
                        field_dict["values"] = list(f.enum_values)
                    if not f.required:
                        field_dict["required"] = False
                    schema_dict[fname] = field_dict
                produces_dict["schema"] = schema_dict
            d["produces"] = produces_dict

        if self.consumes.event_types or self.consumes.injects or self.consumes.schema:
            consumes_dict: dict = {}
            if self.consumes.event_types:
                consumes_dict["event_types"] = list(self.consumes.event_types)
            if self.consumes.injects:
                consumes_dict["injects"] = list(self.consumes.injects)
            if self.consumes.schema:
                schema_dict: dict = {}
                for fname, f in self.consumes.schema.items():
                    field_dict: dict = {"type": f.type, "description": f.description}
                    if f.type == "enum" and f.enum_values:
                        field_dict["values"] = list(f.enum_values)
                    if not f.required:
                        field_dict["required"] = False
                    schema_dict[fname] = field_dict
                consumes_dict["schema"] = schema_dict
            d["consumes"] = consumes_dict

        if self.fan_in.strategy != "merge" or self.fan_in.contributes_to:
            fan_in_dict: dict = {"strategy": self.fan_in.strategy}
            if self.fan_in.contributes_to:
                fan_in_dict["contributes_to"] = self.fan_in.contributes_to
            d["fan_in"] = fan_in_dict

        if self.stop_on_outcome:
            d["stop_on_outcome"] = True

        if self.delivery_workspace_actions:
            d["delivery_workspace_actions"] = list(self.delivery_workspace_actions)

        if self.voice.enabled:
            d["voice"] = self.voice.to_dict()

        return d


def safe_int(val: object, default: int = 0) -> int:
    """Convert *val* to int, returning *default* on ValueError/TypeError."""
    try:
        return int(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def safe_bool(val: Any, default: bool = False) -> bool:
    """Convert *val* to bool, returning *default* on unexpected types."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in {"true", "yes", "1"}
    return default


VALID_FAN_IN_STRATEGIES = {"all_must_pass", "any_pass", "majority", "merge"}


def parse_outcome_field(name: str, raw: Any) -> OutcomeField | None:
    """Parse a single outcome field dict from YAML into an OutcomeField."""
    if not isinstance(raw, dict):
        return None
    field_type = str(raw.get("type", "string"))
    description = str(raw.get("description", name))
    required = safe_bool(raw.get("required", True), default=True)
    enum_values: list[str] | None = None
    if field_type == "enum":
        vals = raw.get("values") or raw.get("enum_values")
        if isinstance(vals, list):
            enum_values = [str(v) for v in vals]
    return OutcomeField(
        type=field_type,  # type: ignore[arg-type]
        description=description,
        enum_values=enum_values,
        required=required,
    )


def parse_produces(raw: Any) -> PersonaProduces:
    """Parse the ``produces:`` section of a persona YAML dict."""
    if not isinstance(raw, dict):
        return PersonaProduces()
    event_type = str(raw.get("event_type", ""))
    event_type_map: dict[str, str] = {}
    event_type_map_raw = raw.get("event_type_map")
    if isinstance(event_type_map_raw, dict):
        for k, v in event_type_map_raw.items():
            event_type_map[str(k)] = str(v)
    schema: dict[str, OutcomeField] = {}
    schema_raw = raw.get("schema")
    if isinstance(schema_raw, dict):
        for fname, fval in schema_raw.items():
            parsed = parse_outcome_field(fname, fval)
            if parsed is not None:
                schema[fname] = parsed
    return PersonaProduces(event_type=event_type, event_type_map=event_type_map, schema=schema)


def parse_consumes(raw: Any) -> PersonaConsumes:
    """Parse the ``consumes:`` section of a persona YAML dict."""
    if not isinstance(raw, dict):
        return PersonaConsumes()
    event_types_raw = raw.get("event_types", [])
    injects_raw = raw.get("injects", [])
    event_types = list(event_types_raw) if isinstance(event_types_raw, list) else []
    injects = list(injects_raw) if isinstance(injects_raw, list) else []
    schema: dict[str, OutcomeField] = {}
    schema_raw = raw.get("schema")
    if isinstance(schema_raw, dict):
        for fname, fval in schema_raw.items():
            parsed = parse_outcome_field(fname, fval)
            if parsed is not None:
                schema[fname] = parsed
    return PersonaConsumes(event_types=event_types, injects=injects, schema=schema)


def parse_fan_in(raw: Any) -> PersonaFanIn:
    """Parse the ``fan_in:`` section of a persona YAML dict.

    An absent section is the default (``merge``). A section that is not a mapping,
    or names a strategy that does not exist, raises: a persona that asks for a
    strategy and cannot get it must not silently merge instead.
    """
    if raw is None:
        return PersonaFanIn()
    if not isinstance(raw, dict):
        raise ValueError("persona fan_in must be a mapping")
    strategy = str(raw.get("strategy", "merge"))
    if strategy not in VALID_FAN_IN_STRATEGIES:
        raise ValueError(
            f"persona fan_in.strategy {strategy!r} is not a fan-in strategy; "
            f"use one of: {', '.join(sorted(VALID_FAN_IN_STRATEGIES))}"
        )
    contributes_to = str(raw.get("contributes_to", ""))
    return PersonaFanIn(
        strategy=strategy,  # type: ignore[arg-type]
        contributes_to=contributes_to,
    )


def parse_voice(raw: Any) -> PersonaVoiceConfig:
    """Parse the ``voice:`` section of a persona YAML dict.

    A malformed section raises: a persona that asks for voice and cannot be read
    must not silently become a persona without it.
    """
    if raw is None:
        return PersonaVoiceConfig()
    if not isinstance(raw, dict):
        raise ValueError("persona voice must be a mapping")
    voices_raw = raw.get("voices") or {}
    if not isinstance(voices_raw, dict) or not voices_raw:
        raise ValueError("persona voice.voices must map a provider to a voice name")
    voices = {str(k).strip(): str(v).strip() for k, v in voices_raw.items()}
    if any(not k or not v for k, v in voices.items()):
        raise ValueError("persona voice.voices must not contain empty providers or voices")
    tools_raw = raw.get("tools", [])
    if not isinstance(tools_raw, list) or not all(isinstance(t, str) and t for t in tools_raw):
        raise ValueError("persona voice.tools must be a list of tool names")
    grok_raw = raw.get("grok", {})
    if not isinstance(grok_raw, dict):
        raise ValueError("persona voice.grok must be a mapping")
    return PersonaVoiceConfig(
        voices=voices,
        style=str(raw.get("style", "")),
        tools=list(tools_raw),
        grok=dict(grok_raw),
    )


def apply_outcome_instruction(persona: PersonaConfig) -> PersonaConfig:
    """Append outcome block instruction to system prompt when schema is declared."""
    if not persona.produces.schema:
        return persona
    schema = OutcomeSchema(fields=persona.produces.schema)
    instruction = generate_outcome_instruction(schema)
    return dataclasses.replace(
        persona,
        system_prompt_template=persona.system_prompt_template + "\n\n" + instruction,
    )


def parse_persona(text: str) -> PersonaConfig | None:
    """Parse a persona YAML *text* string.

    Returns ``None`` on empty input or YAML that is not a persona mapping with a
    name. Handles the ``produces``, ``consumes`` and ``fan_in`` sections.

    Raises :class:`ValueError` when ``permission_mode`` is not a known mode or a tool list
    names a tool outside the neutral vocabulary (``personas.domain.tool_vocabulary``; the
    message lists the valid set), and
    :class:`~personas.domain.persona_document.PersonaDocumentError` when the document
    still carries an ``executor`` block. A persona no longer names the
    harness that runs it, and a document that does must be corrected rather than read
    with the block dropped.
    """
    if not text.strip():
        return None

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        return None

    if not isinstance(raw, dict):
        return None

    name = str(raw.get("name", "")).strip()
    if not name:
        return None

    if "executor" in raw:
        raise PersonaDocumentError(EXECUTOR_REMOVED_MESSAGE.format(name=name))

    llm_raw: dict[str, Any] = {}
    if isinstance(raw.get("llm"), dict):
        llm_raw = raw["llm"]

    llm = PersonaLLMConfig(
        primary_alias=str(llm_raw.get("primary_alias", "")),
        thinking_enabled=safe_bool(llm_raw.get("thinking_enabled", False)),
        max_tokens=safe_int(llm_raw.get("max_tokens", 0)),
    )

    allowed = raw.get("allowed_tools", [])
    forbidden = raw.get("forbidden_tools", [])
    for field_name, tools in (("allowed_tools", allowed), ("forbidden_tools", forbidden)):
        if not isinstance(tools, list):
            continue
        try:
            validate_tool_names(field_name, tools)
        except ValueError as exc:
            raise ValueError(f"Persona {name!r}: {exc}") from exc
    delivery_workspace_actions = raw.get("delivery_workspace_actions", [])
    raw_permission_mode = raw.get("permission_mode")

    return PersonaConfig(
        name=name,
        system_prompt_template=str(raw.get("system_prompt_template", "")),
        allowed_tools=list(allowed) if isinstance(allowed, list) else [],
        forbidden_tools=list(forbidden) if isinstance(forbidden, list) else [],
        permission_mode="" if raw_permission_mode is None else str(raw_permission_mode),
        llm=llm,
        iteration_budget=safe_int(raw.get("iteration_budget", 0)),
        produces=parse_produces(raw.get("produces")),
        consumes=parse_consumes(raw.get("consumes")),
        fan_in=parse_fan_in(raw.get("fan_in")),
        stop_on_outcome=safe_bool(raw.get("stop_on_outcome", False)),
        delivery_workspace_actions=(
            list(delivery_workspace_actions) if isinstance(delivery_workspace_actions, list) else []
        ),
        voice=parse_voice(raw.get("voice")),
    )


class _LiteralStr(str):
    """A string rendered in YAML block-scalar style."""


class _PersonaDumper(yaml.Dumper):
    """YAML dumper that keeps multi-line prompts human-readable."""


def _literal_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


_PersonaDumper.add_representer(_LiteralStr, _literal_representer)


def persona_to_yaml(config: PersonaConfig) -> str:
    """Serialise *config* to a YAML string that :func:`parse_persona` can round-trip.

    ``system_prompt_template`` is rendered in block scalar style (``|``) so that
    multi-line prompts remain human-readable.
    """
    d = config.to_dict()
    if "system_prompt_template" in d and "\n" in d["system_prompt_template"]:
        d["system_prompt_template"] = _LiteralStr(d["system_prompt_template"])

    return yaml.dump(d, default_flow_style=False, sort_keys=False, Dumper=_PersonaDumper)
