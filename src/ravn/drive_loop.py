"""DriveLoop — perpetually-running initiative engine.

Runs as three concurrent asyncio tasks:
- ``_trigger_watcher``  — polls all registered TriggerPorts
- ``_task_executor``    — drains PriorityQueue, runs agent turns
- ``_heartbeat``        — publishes periodic heartbeat events via EventPublisherPort

The queue is persisted to ``queue_journal_path`` on every enqueue/dequeue so
that pending tasks survive daemon restarts.  A file lock prevents concurrent
daemon instances.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from niuu.domain.mimir import ThreadState
from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
from niuu.observability import get_observability
from niuu.ports.mimir import MimirPort
from ravn.adapters.channels.capture import CaptureChannel, TaskResult, TaskResultStore
from ravn.adapters.channels.composite import CompositeChannel
from ravn.adapters.channels.mesh_channel import MeshActivityChannel
from ravn.adapters.channels.silent import SilentChannel
from ravn.adapters.channels.skuld_channel import SkuldChannel
from ravn.adapters.channels.task_context import TaskContextChannel
from ravn.adapters.events.noop_publisher import NoOpEventPublisher
from ravn.config import BudgetConfig, InitiativeConfig, Settings
from ravn.domain.budget import DailyBudgetTracker, compute_cost
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.exceptions import LLMError
from ravn.domain.help_needed import build_help_needed_event
from ravn.domain.models import AgentTask, OutputMode
from ravn.domain.resident_continuation import validate_resident_working_state
from ravn.domain.valkyrie_contracts import (
    VALKYRIE_JUDGMENT_REJECTED,
    VALKYRIE_RUNTIME_OWNED_FIELDS,
    is_valkyrie_outcome_event,
    normalize_valkyrie_outcome,
    validate_valkyrie_outcome,
)
from ravn.ports.channel import ChannelPort
from ravn.ports.event_publisher import EventPublisherPort
from ravn.ports.trigger import TriggerPort
from ravn.prompt_builder import build_initiative_prompt
from ravn.reflex import ReflexInjector, build_reflex_injector
from sleipnir.domain.events import SleipnirEvent

if TYPE_CHECKING:
    from ravn.adapters.personas.loader import PersonaConfig
    from ravn.ports.mesh import MeshPort

try:
    from sleipnir.domain.catalog import (
        mimir_dream_completed as _sleipnir_mimir_dream_completed,
    )
    from sleipnir.domain.catalog import (
        ravn_task_completed as _sleipnir_task_completed,
    )
except ImportError:
    _sleipnir_task_completed = None  # type: ignore[assignment]
    _sleipnir_mimir_dream_completed = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SUCCESS_VERDICT_PREFERENCE = (
    "pass",
    "complete",
    "completed",
    "approve",
    "approved",
    "ok",
    "success",
    "succeeded",
    "clean",
)
_MIMIR_PAGE_WRITTEN_OUTCOME = "mimir.page.written"
_DREAM_CYCLE_TRIGGER = "dream_cycle:cron"
_DREAM_COUNT_FIELDS = ("pages_updated", "entities_created", "lint_fixes")
_RAVN_TASK_STARTED = "ravn.task.started"
_TRACEPARENT_PATTERN = re.compile(
    r"^[\da-fA-F]{2}-([\da-fA-F]{32})-[\da-fA-F]{16}-[\da-fA-F]{2}$"
)


def _bounded_hud_text(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _markdown_section(value: str, heading_prefix: str) -> str:
    """Return one Markdown section without coupling the HUD to task semantics."""
    collecting = False
    collected: list[str] = []
    wanted = heading_prefix.casefold()
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            if collecting:
                break
            collecting = heading.startswith(wanted)
            continue
        if collecting:
            collected.append(line)
    return "\n".join(collected).strip()


def _resident_hud_task_input(task: AgentTask, limit: int) -> dict[str, str]:
    context = task.initiative_context.strip()
    first_line = next((line.strip() for line in context.splitlines() if line.strip()), task.title)
    summary = first_line.lstrip("#").strip() or task.title
    observations = _markdown_section(context, "observed signals")
    if not observations:
        observations = _markdown_section(context, "signal")
    if not observations:
        observations = context
    objective = _markdown_section(context, "your task")
    return {
        "summary": _bounded_hud_text(summary, limit),
        "observations": _bounded_hud_text(observations, limit),
        "objective": _bounded_hud_text(objective, limit),
    }


def _trace_id_from_carrier(carrier: Mapping[str, str]) -> str:
    direct = str(carrier.get("trace_id") or "").strip()
    if re.fullmatch(r"[\da-fA-F]{32}", direct):
        return direct.lower()
    traceparent = str(carrier.get("traceparent") or "").strip()
    match = _TRACEPARENT_PATTERN.fullmatch(traceparent)
    return match.group(1).lower() if match else ""
_RAVN_TASK_DROPPED = "ravn.task.dropped"
_RESIDENT_VALKYRIE_SCHEMA_REPAIR_TRIGGER = "schema_repair:resident_valkyrie"


def _build_workflow_outcome_repair_prompt(
    *,
    task: AgentTask,
    original_response: str,
    allowed_topics: set[str],
) -> str:
    """Ask the same workflow agent to resolve an outcome the graph cannot route."""
    return (
        "Your previous outcome cannot advance the active workflow node because none of its "
        "outgoing routes accept that outcome. Reassess the result using the evidence and tool "
        "results already available. You may use tools again when that could resolve a transient "
        "failure or materially change the answer. Do not claim success without evidence and do "
        "not invent missing facts. If a material decision genuinely requires external input, "
        "return `verdict: help_needed` with one concrete `question`; the workflow can route that "
        "to the commissioning party.\n\n"
        f"Active workflow node: {task.workflow_node_id}\n"
        f"Routable event types: {json.dumps(sorted(allowed_topics))}\n\n"
        "Original response:\n"
        "<original_response>\n"
        f"{original_response}\n"
        "</original_response>\n\n"
        "Return the complete outcome contract required by your persona."
    )


def _default_success_verdict(produces: object) -> str:
    """Return the best success verdict to synthesize for a persona outcome."""
    event_type_map = getattr(produces, "event_type_map", {}) or {}
    if isinstance(event_type_map, dict):
        for candidate in _SUCCESS_VERDICT_PREFERENCE:
            if candidate in event_type_map:
                return candidate

    schema = getattr(produces, "schema", {}) or {}
    if isinstance(schema, dict):
        verdict_schema = schema.get("verdict")
        values: list[str] = []
        if isinstance(verdict_schema, dict):
            raw_values = verdict_schema.get("values", [])
            if isinstance(raw_values, list):
                values = [str(value).strip() for value in raw_values if str(value).strip()]
        else:
            raw_values = getattr(verdict_schema, "enum_values", None)
            if isinstance(raw_values, list):
                values = [str(value).strip() for value in raw_values if str(value).strip()]

        if values:
            normalized = {value.lower(): value for value in values}
            for candidate in _SUCCESS_VERDICT_PREFERENCE:
                chosen = normalized.get(candidate)
                if chosen:
                    return chosen

    return ""


def _known_verdict_tokens(produces: object) -> set[str]:
    """Collect known verdict tokens for a persona outcome contract."""
    tokens: set[str] = set()

    event_type_map = getattr(produces, "event_type_map", {}) or {}
    if isinstance(event_type_map, dict):
        tokens.update(str(key).strip() for key in event_type_map if str(key).strip())

    schema = getattr(produces, "schema", {}) or {}
    if isinstance(schema, dict):
        verdict_schema = schema.get("verdict")
        if isinstance(verdict_schema, dict):
            raw_values = verdict_schema.get("values", [])
            if isinstance(raw_values, list):
                tokens.update(str(value).strip() for value in raw_values if str(value).strip())
        else:
            raw_values = getattr(verdict_schema, "enum_values", None)
            if isinstance(raw_values, list):
                tokens.update(str(value).strip() for value in raw_values if str(value).strip())

    success_verdict = _default_success_verdict(produces)
    if success_verdict:
        tokens.add(success_verdict)

    return tokens


def _normalize_outcome_verdict(raw_verdict: object, produces: object) -> str:
    """Normalize wrapped verdict tokens back onto the declared contract."""
    verdict = str(raw_verdict or "").strip()
    if not verdict:
        return ""

    known = _known_verdict_tokens(produces)
    if not known:
        return verdict

    folded = " ".join(verdict.split())
    candidates: list[str] = []
    for candidate in (
        verdict,
        folded,
        re.sub(r"\s+", "_", folded),
        re.sub(r"\s+", "", folded),
    ):
        candidate = candidate.strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    known_by_lower = {token.lower(): token for token in known}
    for candidate in candidates:
        resolved = known_by_lower.get(candidate.lower())
        if resolved:
            return resolved

    return verdict


def _infer_tool_written_verdict(
    *,
    allowed_topics: set[str] | None,
    event_type_map: dict[str, str],
) -> str:
    """Infer a workflow verdict when one alias outcome is unambiguously allowed."""
    if not allowed_topics or len(allowed_topics) != 1 or not event_type_map:
        return ""

    matches = [
        verdict
        for verdict, topic in event_type_map.items()
        if str(topic).strip() and topic in allowed_topics
    ]
    if len(matches) != 1:
        return ""
    return matches[0]


def _parse_outcome_for_persona(
    response_text: str,
    persona_config: PersonaConfig | None,
):
    """Parse outcome text with persona schema when one is declared."""
    if not response_text:
        return None
    produces = getattr(persona_config, "produces", None)
    schema_fields = getattr(produces, "schema", None)
    if schema_fields:
        try:
            event_type = str(getattr(produces, "event_type", "") or "")
            if is_valkyrie_outcome_event(event_type):
                schema_fields = {
                    name: field
                    for name, field in schema_fields.items()
                    if name not in VALKYRIE_RUNTIME_OWNED_FIELDS
                }
            return parse_outcome_block(response_text, OutcomeSchema(fields=schema_fields))
        except Exception:
            logger.warning(
                "drive_loop: schema-aware outcome parsing failed; falling back to generic parse",
                exc_info=True,
            )
    return parse_outcome_block(response_text)


def _outcome_parse_errors(errors: list[str]) -> list[str]:
    """Keep only errors that mean the outcome block itself was not parseable."""
    return [
        error
        for error in errors
        if error.startswith("YAML parse error:") or "did not parse as a YAML mapping" in error
    ]


def _dedupe_errors(errors: list[str]) -> list[str]:
    """Return validation errors without duplicates, preserving order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for error in errors:
        if error in seen:
            continue
        seen.add(error)
        deduped.append(error)
    return deduped


def _validate_normalized_persona_schema(
    fields: dict[str, object],
    persona_config: PersonaConfig | None,
) -> list[str]:
    """Validate normalized fields against the persona outcome schema."""
    produces = getattr(persona_config, "produces", None)
    schema_fields = getattr(produces, "schema", None)
    if not isinstance(schema_fields, dict):
        return []

    errors: list[str] = []
    for name, field_def in schema_fields.items():
        required = bool(getattr(field_def, "required", True))
        if name not in fields:
            if required:
                errors.append(f"required field '{name}' is missing")
            continue

        value = fields[name]
        field_type = str(getattr(field_def, "type", "") or "")
        if field_type == "enum":
            enum_values = getattr(field_def, "enum_values", None) or []
            if str(value) not in enum_values:
                errors.append(
                    f"field '{name}': value {value!r} not in allowed values {enum_values}"
                )
        elif field_type == "number":
            if not isinstance(value, int | float) or isinstance(value, bool):
                errors.append(f"field '{name}': expected number, got {type(value).__name__}")
        elif field_type == "boolean":
            if not isinstance(value, bool):
                errors.append(f"field '{name}': expected boolean, got {type(value).__name__}")
        elif field_type == "array":
            if not isinstance(value, list):
                errors.append(f"field '{name}': expected array, got {type(value).__name__}")
        elif field_type == "object":
            if not isinstance(value, dict):
                errors.append(f"field '{name}': expected object, got {type(value).__name__}")
        elif field_type == "string" and not isinstance(value, str):
            errors.append(f"field '{name}': expected string, got {type(value).__name__}")
    return errors


def _omit_optional_null_persona_fields(
    fields: dict[str, object],
    persona_config: PersonaConfig | None,
) -> dict[str, object]:
    """Treat YAML null for optional fields as omission at the contract boundary."""
    produces = getattr(persona_config, "produces", None)
    schema_fields = getattr(produces, "schema", None)
    if not isinstance(schema_fields, dict):
        return fields

    normalized = dict(fields)
    for name, field_def in schema_fields.items():
        value = normalized.get(name)
        is_null = value is None or (
            isinstance(value, str) and value.strip().lower() in {"null", "none"}
        )
        if not bool(getattr(field_def, "required", True)) and is_null:
            normalized.pop(name, None)
    return normalized


def _apply_authoritative_valkyrie_fields(
    fields: dict[str, object],
    authoritative_fields: Mapping[str, object] | None,
) -> dict[str, object]:
    """Attach runtime-owned identity and routing fields to a model judgment."""
    if not authoritative_fields:
        return fields
    merged = dict(fields)
    for key in ("environment_id", "environment_type", "valkyrie_id"):
        value = authoritative_fields.get(key)
        if str(value or "").strip():
            merged[key] = value
    correlations = merged.get("correlation_ids")
    correlation_ids = dict(correlations) if isinstance(correlations, Mapping) else {}
    authoritative_correlations = authoritative_fields.get("correlation_ids")
    if isinstance(authoritative_correlations, Mapping):
        correlation_ids.update(authoritative_correlations)
    if correlation_ids:
        merged["correlation_ids"] = correlation_ids
    return merged


def _resident_valkyrie_validation_result(
    response_text: str,
    persona_config: PersonaConfig | None,
    *,
    authoritative_fields: Mapping[str, object] | None = None,
) -> tuple[str, dict[str, object], list[str]]:
    """Parse and validate a resident Valkyrie response against its canonical contract."""
    produces = getattr(persona_config, "produces", None)
    canonical_event_type = str(getattr(produces, "event_type", "") or "")
    if not canonical_event_type or not is_valkyrie_outcome_event(canonical_event_type):
        return "", {}, []

    parsed = _parse_outcome_for_persona(response_text, persona_config)
    outcome_fields = dict(parsed.fields) if parsed is not None else {}
    outcome_fields = normalize_valkyrie_outcome(canonical_event_type, outcome_fields)
    outcome_fields = _omit_optional_null_persona_fields(outcome_fields, persona_config)
    outcome_fields = _apply_authoritative_valkyrie_fields(
        outcome_fields,
        authoritative_fields,
    )

    validation_errors: list[str] = []
    if parsed is None:
        validation_errors.append("resident Valkyrie outcome block is missing")
    elif not parsed.valid:
        validation_errors.extend(_outcome_parse_errors(parsed.errors))
    validation_errors.extend(_validate_normalized_persona_schema(outcome_fields, persona_config))
    validation_errors.extend(validate_valkyrie_outcome(canonical_event_type, outcome_fields))
    validation_errors.extend(_validate_resident_continuation_contract(outcome_fields))
    if "working_state" in outcome_fields:
        validation_errors.extend(validate_resident_working_state(outcome_fields["working_state"]))
    return canonical_event_type, outcome_fields, _dedupe_errors(validation_errors)


def _validate_resident_continuation_contract(fields: Mapping[str, object]) -> list[str]:
    """Validate control-plane coherence without deciding the resident's next action."""
    continuation = str(fields.get("continuation") or "").strip().casefold()
    timing = str(fields.get("next_action_timing") or "").strip().casefold()
    selected = fields.get("selected_next_action")
    if isinstance(selected, Mapping):
        action = str(
            selected.get("action") or selected.get("next_step") or selected.get("description") or ""
        ).strip()
    else:
        action = str(selected or "").strip()
    if not continuation and action and action.casefold().rstrip(".!") != "none":
        return ["selected_next_action requires explicit continuation control"]
    if continuation == "continue":
        return [
            "continuation 'continue' is unsupported; call available tools before the final "
            "outcome, or use sleep/ask_operator for a real wake source"
        ]
    expected_timing = {
        "ask_operator": {"operator_input"},
        "sleep": {"external_event", "scheduled_time"},
        "stop": {"none"},
    }
    if continuation in expected_timing:
        allowed = expected_timing[continuation]
        if timing not in allowed:
            return [
                f"continuation {continuation!r} requires next_action_timing in "
                f"{sorted(allowed)!r}, got {timing!r}"
            ]
    return []


def _build_resident_valkyrie_schema_repair_prompt(
    *,
    task: AgentTask,
    original_response: str,
    validation_errors: list[str],
    outcome_fields: dict[str, object],
) -> str:
    """Build a narrow repair instruction for malformed resident Valkyrie outcomes."""
    working_state_shape = ""
    if "working_state" in outcome_fields or any(
        "working_state" in error for error in validation_errors
    ):
        working_state_shape = (
            "working_state:\n"
            "  observations: []\n"
            "  hypotheses: []\n"
            "  unknowns: []\n"
            "  capability_gaps: []\n"
            "  attempts: []\n"
            "# preserve valid prior entries and revise them with current evidence\n"
        )
    return (
        "[SCHEMA REPAIR - resident Valkyrie outcome]\n"
        "Your previous answer did not satisfy the resident Valkyrie outcome contract.\n"
        "Do not call tools. Do not explain. Return exactly one valid YAML outcome block.\n\n"
        "Use YAML block mappings/sequences. Represent an empty list as `field: []`, never "
        "as a list containing an empty list. Double-quote scalar text containing `:`, `#`, "
        "brackets, or braces. `working_state` must be a mapping, never a quoted string. "
        "It is a compact current model, not a history: use at most five entries per list and "
        "500 characters per entry.\n\n"
        "Validation errors:\n"
        f"{json.dumps(validation_errors, indent=2)}\n\n"
        "Fields parsed from the invalid attempt, if any:\n"
        f"{json.dumps(_json_safe(outcome_fields), indent=2)}\n\n"
        "Original autonomous task context:\n"
        "<initiative_context>\n"
        f"{task.initiative_context}\n"
        "</initiative_context>\n\n"
        "Previous response:\n"
        "<previous_response>\n"
        f"{original_response[:4000]}\n"
        "</previous_response>\n\n"
        "Required block shape:\n"
        "---outcome---\n"
        "decision: ignore | watch | investigate | propose_action | escalate | learn | blocked\n"
        "signal_refs: [<signal event id or stable ref>]\n"
        "tier: silent | ambient | present | urgent\n"
        "confidence: <number from 0.0 to 1.0>\n"
        "operational_state: nominal | watching | investigating | degraded | "
        "remediating | blocked | dreaming\n"
        "wakefulness: sleeping | watching | wakeful | dreaming\n"
        "rationale: <concise reason grounded in evidence>\n"
        "evidence: [{event_id: <id>, source_id: <source>, severity: <severity>}]\n"
        "recommended_action: <specific next step, or none>\n"
        "action_authority: autonomous | yolo_allowed | court_required | human_review_required\n"
        "action_capability: <capability name, or none>\n"
        "target_surfaces: []\n"
        'expires_at: ""\n'
        "dissent_refs: []\n"
        "selected_next_action: <one concrete future action, or none>\n"
        "continuation: ask_operator | sleep | stop\n"
        "next_action_timing: external_event | scheduled_time | operator_input | none\n"
        'question: ""\n'
        "# call tools before this outcome; only a real event/time/operator answer wakes a turn\n"
        f"{working_state_shape}"
        "---end---"
    )


def _coerce_nonnegative_int(value: object) -> int | None:
    """Return *value* as a non-negative integer, or None when it cannot be parsed."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        parsed = int(value)
        return parsed if parsed >= 0 else None
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _coerce_float(value: object, *, default: float) -> float:
    """Return *value* as a float, or *default* when it cannot be parsed."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value or "").strip())
    except ValueError:
        return default


def _clean_string(value: object, *, default: str = "") -> str:
    """Return a useful string while avoiding mocked config object reprs in tests."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    if value.__class__.__module__.startswith("unittest.mock"):
        return default
    raw = str(value).strip()
    return raw or default


def _json_safe(value: object) -> object:
    """Return a msgpack/JSON-safe copy of nested telemetry payload data."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    return value


def _extract_mimir_dream_counts(
    response_text: str,
    persona_config: PersonaConfig | None,
) -> dict[str, int]:
    """Extract dream-cycle summary counts from a final outcome response."""
    counts: dict[str, int] = {}
    parsed = _parse_outcome_for_persona(response_text, persona_config) if response_text else None
    fields = dict(getattr(parsed, "fields", {}) or {}) if parsed is not None else {}

    for field in _DREAM_COUNT_FIELDS:
        value = _coerce_nonnegative_int(fields.get(field))
        if value is not None:
            counts[field] = value

    # Backward-compatible fallback for older warden prompts that only mention
    # counts in the summary/log prose, e.g. ``pages_updated=0``.
    for field in _DREAM_COUNT_FIELDS:
        if field in counts:
            continue
        spaced = field.replace("_", r"[\s_-]")
        match = re.search(rf"\b(?:{field}|{spaced})\b\s*[:=]\s*(\d+)", response_text)
        counts[field] = int(match.group(1)) if match else 0

    return counts


# Type alias for the mesh RPC handler callable
MeshRpcHandler = Callable[[dict], Awaitable[dict]]

# ---------------------------------------------------------------------------
# Fan-in buffer — accumulates events before enqueuing a task
# ---------------------------------------------------------------------------

_FAN_IN_DEFAULT_TTL_SECONDS = 300.0


@dataclass
class FanInSlot:
    """One pending fan-in group waiting for events to accumulate."""

    group_key: str
    required_event_types: set[str]
    received: dict[str, dict]  # event_type → event payload snapshot
    strategy: str  # all_must_pass, any_pass, majority, merge
    persona_name: str
    root_correlation_id: str
    created_at: datetime
    deadline: datetime


@dataclass
class _FanInResult:
    """Returned by FanInBuffer when a slot completes."""

    consumer_key: str
    persona_name: str
    merged_context: str
    root_correlation_id: str
    triggered_by: str


class FanInBuffer:
    """Accumulates mesh events for personas that require multiple inputs.

    Supports two patterns:

    **Consumer accumulation** — a persona consumes multiple event types and
    should only fire when all (or enough) have arrived for the same root
    correlation.

    **Producer aggregation** — multiple personas ``contributes_to`` the same
    target.  A downstream consumer of that target waits until all contributors
    have produced.

    For ``merge`` strategy (the default) the buffer returns immediately —
    no accumulation, backward-compatible with the old behaviour.
    """

    def __init__(self, ttl_seconds: float = _FAN_IN_DEFAULT_TTL_SECONDS) -> None:
        self._slots: dict[str, FanInSlot] = {}
        self._ttl = ttl_seconds
        # contributor_names is populated at startup from the persona catalog
        # target → set of persona names that contribute
        self._contributor_names: dict[str, set[str]] = {}

    def set_contributors(self, target: str, persona_names: list[str]) -> None:
        """Register which personas contribute to *target* (e.g. ``review.verdict``)."""
        self._contributor_names[target] = set(persona_names)

    # ------------------------------------------------------------------
    # Consumer accumulation
    # ------------------------------------------------------------------

    def try_accept_consumer(
        self,
        event_type: str,
        event_payload: dict,
        root_correlation_id: str,
        persona_name: str,
        consumes_event_types: list[str],
        strategy: str,
        consumer_key: str | None = None,
        cycle_correlation_id: str | None = None,
    ) -> _FanInResult | None:
        """Accept an event for consumer-side fan-in.

        Returns a ``_FanInResult`` when all required events have arrived,
        otherwise ``None``. ``cycle_correlation_id`` keeps repeated workflow
        passes under one root from mixing approvals across artifact generations.
        For ``merge`` strategy returns immediately.
        """
        # Act immediately when:
        # - merge strategy (plain event subscription)
        # - any_pass strategy (alternate triggers / OR semantics)
        # - single event type
        # - no fan-in contributors registered (solo persona in the flock)
        if strategy in {"merge", "any_pass"} or len(consumes_event_types) <= 1:
            return _FanInResult(
                consumer_key=consumer_key or persona_name,
                persona_name=persona_name,
                merged_context=self._format_single_event(event_type, event_payload),
                root_correlation_id=root_correlation_id,
                triggered_by=f"mesh:outcome:{event_type}",
            )

        cycle_id = cycle_correlation_id or root_correlation_id
        group_key = f"consumer:{consumer_key or persona_name}:{cycle_id}"
        slot = self._slots.get(group_key)
        now = datetime.now(UTC)

        if slot is None:
            slot = FanInSlot(
                group_key=group_key,
                required_event_types=set(consumes_event_types),
                received={},
                strategy=strategy,
                persona_name=persona_name,
                root_correlation_id=root_correlation_id,
                created_at=now,
                deadline=now + timedelta(seconds=self._ttl),
            )
            self._slots[group_key] = slot

        slot.received[event_type] = event_payload

        if not slot.required_event_types.issubset(set(slot.received.keys())):
            logger.debug(
                "fan-in: consumer %s waiting — have %s, need %s",
                persona_name,
                list(slot.received.keys()),
                list(slot.required_event_types),
            )
            return None

        # All required events present — evaluate strategy
        del self._slots[group_key]
        return self._evaluate_and_merge(slot)

    # ------------------------------------------------------------------
    # Producer aggregation
    # ------------------------------------------------------------------

    def try_accept_producer(
        self,
        contributes_to: str,
        producer_persona: str,
        event_type: str,
        event_payload: dict,
        root_correlation_id: str,
        cycle_correlation_id: str | None = None,
    ) -> _FanInResult | None:
        """Accept a producer outcome for aggregation.

        Returns a ``_FanInResult`` when all contributors have produced,
        otherwise ``None``.  Returns ``None`` immediately if no contributor
        registry exists for *contributes_to*. ``cycle_correlation_id`` separates
        repeated producer rounds under one workflow root.
        """
        required = self._contributor_names.get(contributes_to)
        if not required or len(required) <= 1:
            return None

        cycle_id = cycle_correlation_id or root_correlation_id
        group_key = f"producer:{contributes_to}:{cycle_id}"
        slot = self._slots.get(group_key)
        now = datetime.now(UTC)

        if slot is None:
            slot = FanInSlot(
                group_key=group_key,
                required_event_types=set(required),
                received={},
                strategy="all_must_pass",  # default for producer aggregation
                persona_name=contributes_to,  # target name, not a persona
                root_correlation_id=root_correlation_id,
                created_at=now,
                deadline=now + timedelta(seconds=self._ttl),
            )
            self._slots[group_key] = slot

        # Key by producer persona name (not event_type) since multiple
        # producers may emit different event types for the same target.
        slot.received[producer_persona] = event_payload

        if not slot.required_event_types.issubset(set(slot.received.keys())):
            logger.debug(
                "fan-in: producer aggregation %s waiting — have %s, need %s",
                contributes_to,
                list(slot.received.keys()),
                list(slot.required_event_types),
            )
            return None

        # All contributors reported — evaluate aggregate
        del self._slots[group_key]
        return self._evaluate_and_merge(slot)

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    def sweep_expired(self) -> list[str]:
        """Remove expired slots.  Returns list of expired group keys."""
        now = datetime.now(UTC)
        expired = [k for k, s in self._slots.items() if now > s.deadline]
        for k in expired:
            slot = self._slots.pop(k)
            logger.info(
                "fan-in: expired slot %s (had %d/%d events)",
                k,
                len(slot.received),
                len(slot.required_event_types),
            )
        return expired

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise pending slots for journal persistence."""
        return {
            key: {
                "group_key": slot.group_key,
                "required_event_types": sorted(slot.required_event_types),
                "received": slot.received,
                "strategy": slot.strategy,
                "persona_name": slot.persona_name,
                "root_correlation_id": slot.root_correlation_id,
                "created_at": slot.created_at.isoformat(),
                "deadline": slot.deadline.isoformat(),
            }
            for key, slot in self._slots.items()
        }

    def load_dict(self, data: dict) -> None:
        """Restore pending slots from journal data."""
        for key, entry in data.items():
            self._slots[key] = FanInSlot(
                group_key=entry["group_key"],
                required_event_types=set(entry["required_event_types"]),
                received=entry.get("received", {}),
                strategy=entry["strategy"],
                persona_name=entry["persona_name"],
                root_correlation_id=entry.get("root_correlation_id", ""),
                created_at=datetime.fromisoformat(entry["created_at"]),
                deadline=datetime.fromisoformat(entry["deadline"]),
            )

    @property
    def pending_count(self) -> int:
        return len(self._slots)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _format_single_event(event_type: str, payload: dict) -> str:
        """Format a single event payload as initiative context."""
        source_persona = payload.get("persona", "")
        source_task = payload.get("task_id", "unknown")
        outcome = payload.get("outcome", {})
        parts = [
            f"Event type: {event_type}",
            f"From persona: {source_persona}",
            f"Source task: {source_task}",
        ]

        task_desc = payload.get("task_description")
        if task_desc:
            parts.append(f"Task description: {task_desc}")

        workspace = payload.get("workspace_path")
        if workspace:
            parts.append(f"Workspace path: {workspace}")

        files = payload.get("files_changed")
        if files:
            parts.append(f"Files changed ({len(files)}):")
            for f in files:
                parts.append(f"  - {f}")

        diff = payload.get("diff_summary")
        if diff:
            parts.append(f"Git diff:\n{diff}")

        if outcome:
            parts.append(f"Outcome: {json.dumps(outcome)}")
        return "\n".join(parts)

    @staticmethod
    def _evaluate_and_merge(slot: FanInSlot) -> _FanInResult:
        """Evaluate the fan-in strategy and merge received events into context."""
        # Collect verdicts from all received events
        verdicts: list[str] = []
        for payload in slot.received.values():
            outcome = payload.get("outcome", {}) if isinstance(payload, dict) else {}
            verdict = outcome.get("verdict", "pass") if isinstance(outcome, dict) else "pass"
            verdicts.append(str(verdict))

        # Evaluate strategy
        strategy_ok = True
        if slot.strategy == "all_must_pass":
            strategy_ok = all(v != "fail" for v in verdicts)
        elif slot.strategy == "any_pass":
            strategy_ok = any(v == "pass" for v in verdicts)
        elif slot.strategy == "majority":
            passing = sum(1 for v in verdicts if v == "pass")
            strategy_ok = passing > len(verdicts) / 2

        # Merge context from all received events
        context_parts = [
            f"Fan-in complete: {slot.group_key}",
            f"Strategy: {slot.strategy} → {'PASS' if strategy_ok else 'FAIL'}",
            f"Events received: {len(slot.received)}/{len(slot.required_event_types)}",
            "",
        ]
        for source, payload in slot.received.items():
            outcome = payload.get("outcome", {}) if isinstance(payload, dict) else {}
            persona = payload.get("persona", source) if isinstance(payload, dict) else source
            context_parts.append(f"--- {persona} ({source}) ---")
            if outcome:
                context_parts.append(json.dumps(outcome, indent=2))
            context_parts.append("")

        triggered_by_types = list(slot.required_event_types)
        return _FanInResult(
            consumer_key=slot.group_key.split(":", 2)[1],
            persona_name=slot.persona_name,
            merged_context="\n".join(context_parts),
            root_correlation_id=slot.root_correlation_id,
            triggered_by=f"mesh:fan_in:{'+'.join(sorted(triggered_by_types))}",
        )


class DriveLoop:
    """Perpetually-running initiative engine.

    ``agent_factory(channel, task_id, persona, triggered_by)`` is called per
    task to create an isolated :class:`ravn.agent.RavnAgent` instance.  The
    ``task_id`` parameter lets the agent (and its SleipnirChannel) tag all
    emitted events with the correct task correlation ID.  The ``persona``
    parameter allows per-task persona overrides (may be ``None`` to use the
    default).  The ``triggered_by`` parameter carries the trigger source
    (e.g. ``"thread:<slug>"``) so the factory can apply trust constraints.

    Human-initiated turns (from the gateway) are NOT subject to the
    ``max_concurrent_tasks`` cap — that cap only applies to initiative tasks
    started by the drive loop.
    """

    def __init__(
        self,
        agent_factory: Callable[[ChannelPort, str | None, str | None, str | None], object],
        config: InitiativeConfig,
        settings: Settings,
        event_publisher: EventPublisherPort | None = None,
        resume: bool = False,
        budget: DailyBudgetTracker | None = None,
        mimir: MimirPort | None = None,
        sleipnir_publisher: object | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._config = config
        self._settings = settings
        self._event_publisher: EventPublisherPort = event_publisher or NoOpEventPublisher()
        self._resume = resume
        self._mimir = mimir
        self._sleipnir_publisher = sleipnir_publisher
        self._triggers: list[TriggerPort] = []
        # (priority, counter, AgentTask)
        # Capacity is enforced in enqueue(). The queue itself stays unbounded so
        # in-flight records restored after a crash can temporarily coexist with
        # a previously full pending queue without dropping either class of work.
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._inflight_tasks: dict[str, AgentTask] = {}
        self._restored_inflight_task_ids: set[str] = set()
        self._active_task_contexts: dict[str, AgentTask] = {}
        self._active_agents: dict[str, object] = {}
        self._semaphore = asyncio.Semaphore(config.max_concurrent_tasks)
        self._journal_path = Path(config.queue_journal_path).expanduser()
        self._source_id = "drive_loop"
        self._counter = 0
        self._rpc_handler: MeshRpcHandler | None = None
        self._directed_message_interceptors: list[
            Callable[[str, dict[str, Any] | None], Awaitable[bool]]
        ] = []
        self._result_store: TaskResultStore = TaskResultStore()
        self._completion_events: dict[str, asyncio.Event] = {}
        self._mesh: MeshPort | None = None
        self._persona_config: PersonaConfig | None = None
        self._workflow_allowed_outcomes_resolver: (
            Callable[[AgentTask, PersonaConfig], set[str] | None] | None
        ) = None
        self._fan_in = FanInBuffer()
        self._reflex_injector: ReflexInjector | None = None
        self._reflex_initialised = False
        self._resident_runtime: object | None = None
        self._shutting_down = False
        self._current_task_var: contextvars.ContextVar[AgentTask | None] = contextvars.ContextVar(
            "ravn_current_task",
            default=None,
        )
        budget_cfg = getattr(settings, "budget", None)
        if isinstance(budget_cfg, BudgetConfig):
            _cap = budget_cfg.daily_cap_usd
            _warn = budget_cfg.warn_at_percent
        else:
            _cap = 1.0
            _warn = 80
        self._budget: DailyBudgetTracker = budget or DailyBudgetTracker(
            daily_cap_usd=_cap,
            warn_at_percent=_warn,
        )

        # Session-join manager: built once here (composition root) and injected
        # into both this loop's observer and the session_join tool's runtime
        # context, so the tool and the drive loop share one membership set
        # without a module global.
        from ravn.session_join import build_session_join_manager  # noqa: PLC0415

        self._session_join_manager = build_session_join_manager(settings)

        # Skuld channel for browser delivery (mesh cascade visualization)
        # Live activity and durable workflow outcomes intentionally share the mesh
        # today so ordering and correlation remain consistent for all consumers.
        self._skuld_channel: SkuldChannel | None = None
        if settings.skuld.enabled:
            # peer_id is appended to the broker_url
            peer_id = settings.mesh.own_peer_id if settings.mesh.enabled else "ravn-daemon"
            broker_url = f"{settings.skuld.broker_url.rstrip('/')}/{peer_id}"
            self._skuld_channel = SkuldChannel(
                broker_url=broker_url,
                session_id="mesh",
                peer_id=peer_id,
                persona=None,  # Set per-task
                display_name=settings.skuld.display_name or "",
                reconnect_delay=settings.skuld.reconnect_delay_seconds,
                max_reconnect_attempts=settings.skuld.max_reconnect_attempts,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _next_counter(self) -> int:
        self._counter += 1
        return self._counter

    def register_trigger(self, trigger: TriggerPort) -> None:
        """Register a trigger source before calling ``run()``."""
        self._triggers.append(trigger)

    def operator_contact_channel(self) -> ChannelPort | None:
        """Return the daemon's existing outbound operator channel, when enabled."""
        return self._skuld_channel

    def register_directed_message_interceptor(
        self,
        handler: Callable[[str, dict[str, Any] | None], Awaitable[bool]],
    ) -> None:
        """Register a handler that may consume directed user messages before enqueue."""
        self._directed_message_interceptors.append(handler)

    async def enqueue(self, task: AgentTask) -> bool:
        """Add a task to the priority queue, honouring deadline and capacity."""
        telemetry = get_observability()
        attributes = {
            "ravn.task.id": task.task_id,
            "ravn.task.trigger": task.triggered_by,
            "ravn.task.persona": task.persona or "ravn",
            "ravn.queue.max": self._config.task_queue_max,
        }
        with telemetry.span(
            "ravn.queue.enqueue",
            attributes=attributes,
            carrier=task.trace_context,
        ) as span:
            accepted, reason = await self._enqueue_observed(task)
            outcome_attributes = {
                **attributes,
                "ravn.queue.outcome": "accepted" if accepted else "rejected",
                "ravn.queue.reason": reason,
            }
            telemetry.set_attributes(outcome_attributes)
            telemetry.event("ravn.queue.admission", attributes=outcome_attributes)
            telemetry.count(
                "ravn.queue.admissions",
                attributes={
                    "ravn.task.trigger": task.triggered_by,
                    "ravn.task.persona": task.persona or "ravn",
                    "ravn.queue.outcome": outcome_attributes["ravn.queue.outcome"],
                    "ravn.queue.reason": reason,
                    "ravn.environment.id": self._settings.environment.id,
                },
            )
            if not accepted:
                telemetry.mark_error(span, reason)
            self._record_queue_state()
            return accepted

    async def _enqueue_observed(self, task: AgentTask) -> tuple[bool, str]:
        """Perform queue admission and return the decision with its reason."""
        if (
            task.task_id in self._active_tasks
            or task.task_id in self._inflight_tasks
            or task.task_id in self.queued_task_ids()
        ):
            logger.info(
                "drive_loop: duplicate task %s (%r) already queued or active — discarding",
                task.task_id,
                task.title,
            )
            await self._emit_sleipnir_task_dropped(task, reason="duplicate_task")
            return False, "duplicate_task"

        if task.deadline is not None and datetime.now(UTC) > task.deadline:
            logger.info(
                "drive_loop: task %s (%r) deadline exceeded before enqueue — discarding",
                task.task_id,
                task.title,
            )
            await self._emit_sleipnir_task_dropped(task, reason="deadline_exceeded")
            return False, "deadline_exceeded"

        if self._queue.qsize() >= self._config.task_queue_max:
            logger.info(
                "drive_loop: queue full (max=%d) — discarding task %s",
                self._config.task_queue_max,
                task.task_id,
            )
            await self._emit_sleipnir_task_dropped(task, reason="queue_full")
            return False, "queue_full"

        counter = self._next_counter()
        await self._queue.put((task.priority, counter, task))
        track = getattr(self._resident_runtime, "track_task", None)
        if track is not None:
            track(task)
        self._persist_queue()
        logger.info(
            "drive_loop: enqueued task %s (%r) queue_size=%d active=%d",
            task.task_id,
            task.title,
            self._queue.qsize(),
            len(self._active_tasks),
        )
        return True, "accepted"

    def _record_queue_state(self) -> None:
        telemetry = get_observability()
        attributes = {
            "ravn.environment.id": self._settings.environment.id,
            "gen_ai.agent.id": self._settings.mesh.own_peer_id or self._source_id,
        }
        telemetry.gauge(
            "ravn.queue.depth",
            self._queue.qsize(),
            attributes=attributes,
            description="Current pending resident task count.",
        )
        telemetry.gauge(
            "ravn.agent.active_tasks",
            len(self._active_tasks),
            attributes=attributes,
            description="Current executing resident task count.",
        )
        telemetry.gauge(
            "ravn.runtime.up",
            1,
            attributes=attributes,
            description="Resident runtime liveness heartbeat.",
        )

    async def cancel(self, task_id: str) -> None:
        """Request cancellation of a running task by ID."""
        running = self._active_tasks.get(task_id)
        if running is not None:
            running.cancel()
            logger.info("drive_loop: cancel requested for task %s", task_id)

    @property
    def fan_in(self) -> FanInBuffer:
        """The fan-in buffer for event accumulation."""
        return self._fan_in

    def active_task_ids(self) -> list[str]:
        """Return task IDs that are currently executing."""
        return list(self._active_tasks.keys())

    def resident_hud_status(self) -> dict[str, object]:
        """Return factual, bounded-by-store runtime state for the resident HUD."""
        context_limit = self._settings.gateway.channels.http.resident_hud_task_context_max_chars
        active: list[dict[str, object]] = []
        for task_id in self.active_task_ids():
            task = self._inflight_tasks.get(task_id) or self._active_task_contexts.get(task_id)
            result = self._result_store.get(task_id)
            events = list(result.events if result is not None else [])
            agent = self._active_agents.get(task_id)
            iteration_budget = getattr(agent, "iteration_budget", None)
            active.append(
                {
                    "task_id": task_id,
                    "title": task.title if task is not None else "",
                    "triggered_by": task.triggered_by if task is not None else "",
                    "persona": (
                        task.persona
                        if task is not None and task.persona
                        else getattr(self._persona_config, "name", "")
                    ),
                    "root_correlation_id": (
                        task.root_correlation_id or task.task_id if task is not None else task_id
                    ),
                    "case_id": task.resident_case_id if task is not None else "",
                    "turn_index": task.resident_turn_index if task is not None else 0,
                    "trace_id": (
                        _trace_id_from_carrier(task.trace_context) if task is not None else ""
                    ),
                    "started_at": (
                        result.started_at.isoformat()
                        if result is not None
                        else task.created_at.isoformat()
                        if task is not None
                        else ""
                    ),
                    "input": (
                        _resident_hud_task_input(task, context_limit) if task is not None else {}
                    ),
                    "progress": {
                        "iteration": int(getattr(iteration_budget, "consumed", 0) or 0),
                        "iteration_budget": int(getattr(iteration_budget, "total", 0) or 0),
                        "tool_calls": sum(event.type == "tool_start" for event in events),
                        "warnings": sum(
                            event.type == "error" or "[warning]" in event.summary.casefold()
                            for event in events
                        ),
                    },
                    "events": [
                        {
                            "type": event.type,
                            "summary": event.summary,
                            "timestamp": event.timestamp.isoformat(),
                        }
                        for event in events
                    ],
                }
            )
        queued = [
            {
                "task_id": task.task_id,
                "title": task.title,
                "triggered_by": task.triggered_by,
                "root_correlation_id": task.root_correlation_id or task.task_id,
                "created_at": task.created_at.isoformat(),
                "input_summary": _resident_hud_task_input(task, context_limit)["summary"],
            }
            for _priority, _counter, task in sorted(
                list(self._queue._queue)  # type: ignore[attr-defined]
            )
        ]
        return {
            "active_tasks": active,
            "active_count": len(active),
            "queued_count": self._queue.qsize(),
            "queue_capacity": self._config.task_queue_max,
            "queued_tasks": queued,
            "model": self._settings.effective_model(),
        }

    def queued_task_ids(self) -> list[str]:
        """Return task IDs waiting in the priority queue (not yet started)."""
        return [
            task.task_id
            for _prio, _counter, task in list(self._queue._queue)  # type: ignore[attr-defined]
        ]

    def task_status(self, task_id: str, include_progress: bool = False) -> str | dict:
        """Return the current status of a task by ID.

        When ``include_progress=False`` (default) returns one of:
        - ``"running"``  — task is actively executing
        - ``"queued"``   — task is in the priority queue, not yet started
        - ``"unknown"``  — task_id not found (may have completed or never existed)

        When ``include_progress=True`` returns a dict::

            {
                "status": "<running|queued|unknown>",
                "events": [{"type": ..., "summary": ..., "timestamp": ...}, ...],
            }

        The event list reflects all events accumulated so far by CaptureChannel.
        """
        if task_id in self._active_tasks:
            status = "running"
        elif task_id in self.queued_task_ids():
            status = "queued"
        else:
            status = "unknown"

        if not include_progress:
            return status

        result = self._result_store.get(task_id)
        events_data: list[dict] = []
        if result is not None:
            events_data = [
                {
                    "type": e.type,
                    "summary": e.summary,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in result.events
            ]
        return {"status": status, "events": events_data}

    def get_result(self, task_id: str) -> TaskResult | None:
        """Return the full TaskResult for a completed (or running) task, or None."""
        return self._result_store.get(task_id)

    async def wait_for_result(self, task_id: str) -> TaskResult | None:
        """Wait for a task to complete and return its result.

        Creates an asyncio.Event for the task if one doesn't exist, then waits
        for it to be signalled by _on_task_done.
        """
        # If task is already complete, return immediately
        result = self._result_store.get(task_id)
        if result is not None and result.status in ("complete", "failed", "cancelled"):
            return result

        # Create event if not exists
        if task_id not in self._completion_events:
            self._completion_events[task_id] = asyncio.Event()

        # Wait for completion
        await self._completion_events[task_id].wait()

        # Clean up and return result
        self._completion_events.pop(task_id, None)
        return self._result_store.get(task_id)

    def set_rpc_handler(self, handler: MeshRpcHandler) -> None:
        """Register a coroutine handler for incoming mesh RPC messages.

        The handler is called with the raw message dict and must return a
        dict reply.  Registered via MeshPort.set_rpc_handler() in _run_daemon().
        """
        self._rpc_handler = handler

    def set_mesh(self, mesh: MeshPort | None) -> None:
        """Set the mesh port for publishing outcome events."""
        self._mesh = mesh

    def set_persona_config(self, persona_config: PersonaConfig | None) -> None:
        """Set the persona config for determining produces.event_type.

        Must be called before ``run()`` so the Skuld channel connects with
        the full identity (persona, display_name, subscribes_to, emits, tools).
        """
        self._persona_config = persona_config

        # Enrich Skuld channel with persona metadata for the sidebar UI
        if self._skuld_channel is not None and persona_config is not None:
            self._skuld_channel._persona = persona_config.name
            self._skuld_channel._subscribes_to = persona_config.consumes.event_types
            emits: list[str] = []
            if persona_config.produces.event_type:
                emits.append(persona_config.produces.event_type)
            emits.extend(persona_config.produces.event_type_map.values())
            self._skuld_channel._emits = list(dict.fromkeys(emits))  # dedupe, preserve order
            self._skuld_channel._tools = persona_config.allowed_tools
        if self._session_join_manager is not None and persona_config is not None:
            self._session_join_manager.set_subscribes_to(persona_config.consumes.event_types)

    def set_resident_runtime(self, runtime: object | None) -> None:
        """Attach the application service that persists/resumes resident cases."""
        self._resident_runtime = runtime
        bind = getattr(runtime, "bind_enqueue", None)
        if bind is not None:
            bind(self.enqueue)

    def set_workflow_allowed_outcomes_resolver(
        self,
        resolver: Callable[[AgentTask, PersonaConfig], set[str] | None] | None,
    ) -> None:
        """Register a resolver for node-scoped workflow outcome topics."""
        self._workflow_allowed_outcomes_resolver = resolver

    def current_task(self) -> AgentTask | None:
        """Return the task currently executing in this context, if any."""
        return self._current_task_var.get()

    def resolve_task_context(self, task_id: str | None = None) -> AgentTask | None:
        """Resolve the task context for tool-originated workflow events."""
        if task_id:
            task = self._active_task_contexts.get(task_id)
            if task is not None:
                return task
        current = self.current_task()
        if current is not None:
            return current
        if len(self._active_task_contexts) == 1:
            return next(iter(self._active_task_contexts.values()))
        return None

    def record_tool_outcome_fields(
        self,
        *,
        task: AgentTask,
        event_type: str,
        fields: dict[str, object],
    ) -> None:
        """Remember deterministic tool metadata for the task's final outcome."""
        if not event_type or not fields:
            return
        existing = task.tool_outcomes.get(event_type) or {}
        merged = dict(existing)
        merged.update(fields)
        task.tool_outcomes[event_type] = merged

    def _authoritative_valkyrie_fields(self, task: AgentTask) -> dict[str, object]:
        """Return identity/correlation data owned by runtime configuration."""
        environment = getattr(self._settings, "environment", None)
        mesh = getattr(self._settings, "mesh", None)
        environment_id = _clean_string(getattr(environment, "id", None))
        environment_type = _clean_string(getattr(environment, "type", None))
        valkyrie_id = _clean_string(getattr(mesh, "own_peer_id", None), default=self._source_id)
        correlations: dict[str, str] = {
            "root": task.root_correlation_id or task.task_id,
            "task": task.task_id,
        }
        trace_id = get_observability().trace_id()
        if trace_id:
            correlations["trace"] = trace_id
        if environment_id:
            correlations["environment"] = environment_id
        if task.workflow_parent_event_id:
            correlations["signal"] = task.workflow_parent_event_id
        fields: dict[str, object] = {
            "valkyrie_id": valkyrie_id,
            "correlation_ids": correlations,
        }
        if environment_id:
            fields["environment_id"] = environment_id
        if environment_type:
            fields["environment_type"] = environment_type
        return fields

    def _decorate_turn_result_outcome(
        self,
        task: AgentTask,
        turn_result: object,
        response_text: str,
    ) -> tuple[dict[str, object], bool | None]:
        """Parse resident control fields independently of episodic memory."""
        canonical_event_type, outcome_fields, validation_errors = (
            _resident_valkyrie_validation_result(
                response_text,
                self._persona_config,
                authoritative_fields=self._authoritative_valkyrie_fields(task),
            )
        )
        if not canonical_event_type:
            return {}, None

        outcome_valid = not validation_errors
        episode = getattr(turn_result, "episode", None)
        if episode is not None and outcome_fields:
            episode.structured_outcome = outcome_fields
            episode.outcome_valid = outcome_valid
        return outcome_fields, outcome_valid

    def _default_mimir_mount_fields(self) -> dict[str, object]:
        """Infer the active Mimir mount names from runtime config when unambiguous."""
        mimir_cfg = getattr(self._settings, "mimir", None)
        if mimir_cfg is None:
            return {}

        mount_names = [
            str(name).strip()
            for name in getattr(getattr(mimir_cfg, "write_routing", None), "default", []) or []
            if str(name).strip()
        ]
        if not mount_names:
            mount_names = [
                str(instance.name).strip()
                for instance in getattr(mimir_cfg, "instances", []) or []
                if str(getattr(instance, "name", "")).strip()
            ]
        if not mount_names:
            return {}

        fields: dict[str, object] = {"mount_names": mount_names}
        if len(mount_names) == 1:
            fields["mount_name"] = mount_names[0]
        return fields

    @staticmethod
    def _artifact_publish_paths(outcome_fields: dict[str, object]) -> list[str]:
        """Collect markdown artifact paths declared in an outcome block."""
        paths: list[str] = []
        for key, value in outcome_fields.items():
            if not str(key).endswith("_path"):
                continue
            path = str(value or "").strip()
            if not path or not path.endswith(".md"):
                continue
            paths.append(path)
        return list(dict.fromkeys(paths))

    async def _maybe_publish_workflow_artifacts(
        self,
        task: AgentTask,
        outcome_fields: dict[str, object],
    ) -> str:
        """Mirror workflow-authored markdown artifacts into the active Mimir mount."""
        if self._mimir is None:
            return ""
        if not str(task.workflow_node_id or "").strip():
            return ""
        paths = self._artifact_publish_paths(outcome_fields)
        if not paths:
            return ""

        from ravn.adapters.tools.mimir_tools import MimirPublishFilesTool  # noqa: PLC0415

        workspace_root = Path(
            str(getattr(getattr(self._settings, "permission", None), "workspace_root", "")).strip()
            or Path.cwd()
        )
        tool = MimirPublishFilesTool(self._mimir, workspace_root)
        local_paths = [path for path in paths if tool.resolve_workspace_file(path) is not None]
        if not local_paths:
            return ""
        result = await tool.execute({"paths": local_paths})
        if result.is_error:
            error = str(result.content or "artifact publication failed")
            logger.warning(
                "drive_loop: failed to publish workflow artifacts to Mimir for task %s: %s",
                task.task_id,
                error,
            )
            get_observability().event(
                "ravn.workflow.artifact.publish_failed",
                attributes={
                    "ravn.task.id": task.task_id,
                    "ravn.workflow.node.id": task.workflow_node_id,
                    "error.type": "artifact_publish_failed",
                    "error.message": error,
                },
            )
            return error
        outcome_fields.setdefault("published_paths", local_paths)
        logger.info(
            "drive_loop: published workflow artifacts to Mimir for task %s: %s",
            task.task_id,
            local_paths,
        )
        return ""

    async def _maybe_materialize_workflow_artifacts(
        self,
        task: AgentTask,
        outcome_fields: dict[str, object],
    ) -> None:
        """Mirror workflow-authored Mimir artifacts into the live workspace."""
        if self._mimir is None:
            return
        if not str(task.workflow_node_id or "").strip():
            return
        paths = self._artifact_publish_paths(outcome_fields)
        if not paths:
            return

        from ravn.adapters.tools.file_security import (  # noqa: PLC0415
            PathSecurityError,
            resolve_safe,
        )

        workspace_root = Path(
            str(getattr(getattr(self._settings, "permission", None), "workspace_root", "")).strip()
            or Path.cwd()
        )
        materialized: list[str] = []
        for path in paths:
            if Path(path).is_absolute():
                logger.warning(
                    "drive_loop: refusing to materialize absolute workflow artifact path %s",
                    path,
                )
                continue
            try:
                target = resolve_safe(workspace_root / path, workspace_root)
            except PathSecurityError:
                logger.warning(
                    "drive_loop: refusing to materialize unsafe workflow artifact path %s",
                    path,
                )
                continue
            try:
                page = await self._mimir.get_page(path)
            except Exception as exc:
                logger.debug(
                    "drive_loop: workflow artifact %s is not readable from Mimir for task %s: %s",
                    path,
                    task.task_id,
                    exc,
                )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(getattr(page, "content", "") or ""), encoding="utf-8")
            materialized.append(path)

        if not materialized:
            return
        outcome_fields.setdefault("workspace_paths", materialized)
        logger.info(
            "drive_loop: materialized workflow artifacts into workspace for task %s: %s",
            task.task_id,
            materialized,
        )

    async def handle_rpc(self, message: dict) -> dict:
        """Dispatch an incoming mesh RPC message to the registered handler.

        Falls back to an error reply if no handler is registered.
        """
        if self._rpc_handler is None:
            return {"error": "no_rpc_handler_registered"}
        try:
            return await self._rpc_handler(message)
        except Exception as exc:
            logger.error("drive_loop: rpc handler raised: %s", exc)
            return {"error": str(exc)}

    @staticmethod
    def _directed_message_context(
        content: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        """Render a directed human reply as initiative context for the target persona."""
        trimmed = content.strip()
        if not metadata:
            return "\n".join(
                [
                    "This is a directed message from a human.",
                    f"Human message: {trimmed}",
                ]
            )

        help_summary = str(metadata.get("help_summary") or "").strip()
        help_reason = str(metadata.get("help_reason") or "").strip()
        help_recommendation = str(metadata.get("help_recommendation") or "").strip()
        attempted = metadata.get("help_attempted")
        context = metadata.get("help_context")
        reply_context = metadata.get("reply_context")
        recent_room_context = metadata.get("recent_room_context")
        has_help_context = bool(
            help_summary
            or help_reason
            or help_recommendation
            or isinstance(attempted, list)
            or isinstance(context, dict)
        )

        if has_help_context:
            parts = ["This is a directed human reply for an in-progress workflow turn."]
        else:
            parts = ["This is a directed message from a human."]
        if help_summary:
            parts.append(f"Pending help summary: {help_summary}")
        if help_reason:
            parts.append(f"Reason: {help_reason}")
        if isinstance(attempted, list):
            attempted_items = [str(item).strip() for item in attempted if str(item).strip()]
            if attempted_items:
                parts.append("Already attempted:")
                parts.extend(f"  - {item}" for item in attempted_items[:6])
        if help_recommendation:
            parts.append(f"Previous recommendation: {help_recommendation}")
        if isinstance(context, dict) and context:
            parts.append(f"Workflow context: {json.dumps(context, sort_keys=True)}")
        if isinstance(reply_context, dict) and reply_context:
            prior_content = str(reply_context.get("content") or "").strip()
            if prior_content:
                parts.extend(
                    [
                        "The human replied to this prior room message:",
                        prior_content,
                    ]
                )
        elif isinstance(recent_room_context, dict) and recent_room_context:
            prior_content = str(recent_room_context.get("content") or "").strip()
            if prior_content:
                parts.extend(
                    [
                        "Most recent message from this participant in the room "
                        "(context only; the human did not explicitly reply to it):",
                        prior_content,
                    ]
                )
        label = "Human reply" if has_help_context or reply_context else "Human message"
        parts.append(f"{label}: {trimmed}")
        return "\n".join(parts)

    async def _handle_joined_session_message(
        self,
        source_session_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Enqueue a message directed at us inside a JOINED session's room.

        Perception path — tagged with its source session so the agent never
        confuses it with the operator speaking in its own room. Replies into
        that room go through the ``session_join`` tool's ``post`` action.
        """
        import time  # noqa: PLC0415

        task_id = f"task_{int(time.time() * 1000):x}_{self._next_counter()}"
        persona = self._persona_config.name if self._persona_config else None
        context = "\n".join(
            [
                f"Message received inside joined session {source_session_id} "
                "(a room you are participating in as an observer — this is "
                "NOT your operator's chat):",
                self._directed_message_context(content, metadata),
                "If a reply into that room is needed, use the session_join "
                f'tool: {{"action": "post", "session_id": "{source_session_id}", '
                '"text": "..."}}. Keep your operator informed in your own '
                "room only when something meaningful happened.",
            ]
        )
        task = AgentTask(
            task_id=task_id,
            title=f"Joined-session message ({source_session_id[:8]})",
            initiative_context=context,
            triggered_by=f"session_join:{source_session_id}",
            output_mode=OutputMode.AMBIENT,
            persona=persona,
        )
        logger.info(
            "drive_loop: joined-session message from %s enqueued as task %s",
            source_session_id,
            task_id,
        )
        await self.enqueue(task)

    async def handle_directed_message(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Enqueue a directed message from the browser as an agent task."""
        for handler in list(self._directed_message_interceptors):
            try:
                if await handler(content, metadata):
                    logger.info("drive_loop: directed message consumed by interceptor")
                    return True
            except Exception:
                logger.exception("drive_loop: directed message interceptor failed")

        if await self._try_steer_active_agent(content):
            return True

        import time

        task_id = f"task_{int(time.time() * 1000):x}_{self._next_counter()}"
        persona = self._persona_config.name if self._persona_config else None
        task = AgentTask(
            task_id=task_id,
            title="Directed message from user",
            initiative_context=self._directed_message_context(content, metadata),
            triggered_by="skuld:directed_message",
            output_mode=OutputMode.SURFACE,
            persona=persona,
            priority=1,  # high priority — user is waiting
            human_initiated=True,
        )
        if metadata:
            root_correlation_id = str(metadata.get("root_correlation_id") or "").strip()
            workflow_parent_event_id = str(metadata.get("workflow_parent_event_id") or "").strip()
            workflow_node_id = str(metadata.get("workflow_node_id") or "").strip()
            session_id = str(metadata.get("session_id") or "").strip()
            trace_context = metadata.get("trace_context")
            if root_correlation_id:
                task.root_correlation_id = root_correlation_id
            if workflow_parent_event_id:
                task.workflow_parent_event_id = workflow_parent_event_id
            if workflow_node_id:
                task.workflow_node_id = workflow_node_id
            if session_id:
                task.session_id = session_id
            if isinstance(trace_context, dict):
                task.trace_context = {str(key): str(value) for key, value in trace_context.items()}
        logger.info("drive_loop: directed message enqueued as task %s", task_id)
        return await self.enqueue(task)

    async def _try_steer_active_agent(self, content: str) -> bool:
        """Attempt to steer the currently active agent instead of queueing a new task."""
        if not content.strip():
            return False

        steerable = [
            agent
            for agent in self._active_agents.values()
            if getattr(agent, "supports_steering", False) and getattr(agent, "steering_mode", "")
        ]
        if len(steerable) != 1:
            return False

        agent = steerable[0]
        steer = getattr(agent, "steer", None)
        if steer is None:
            return False
        try:
            steered = await steer(content)
        except Exception:
            logger.warning("drive_loop: active agent steering failed; falling back to enqueue")
            return False
        if steered:
            logger.info("drive_loop: directed message applied as live steering")
        return bool(steered)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all three internal loops and run until cancelled."""
        if self._resume:
            self._load_journal()
        elif self._journal_path.exists():
            logger.info("drive_loop: discarding stale journal (use --resume to restore)")
            with suppress(OSError):
                self._journal_path.unlink()

        # Connect Skuld channel eagerly so the ravn appears in the browser
        # sidebar immediately.  set_persona_config() has already enriched the
        # channel with persona, display_name, subscribes_to, emits, tools —
        # the registration frame sent on connect carries the full identity.
        if self._skuld_channel is not None:
            self._skuld_channel.on_directed_message(self.handle_directed_message)
            self._session_join_manager.set_status_channel(self._skuld_channel)
            await self._skuld_channel.connect()

            # Session joins (secondary rooms): frames directed at us inside a
            # JOINED session are perception, not operator turns — they arrive
            # tagged with their source session and enqueue at normal priority.
            # The manager was built in __init__ and is shared with the
            # session_join tool via the tool runtime context.
            self._session_join_manager.set_observer(self._handle_joined_session_message)

        coros: list[Awaitable] = [
            self._task_executor(),
            self._heartbeat(),
        ]
        for trigger in self._triggers:
            coros.append(self._trigger_watcher(trigger))

        try:
            await asyncio.gather(*coros)
        except asyncio.CancelledError:
            logger.info("drive_loop: shutting down")
            self._shutting_down = True
            self._persist_queue()
            active_tasks = list(self._active_tasks.values())
            for task in active_tasks:
                task.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            self._persist_queue()
            if self._session_join_manager is not None:
                await self._session_join_manager.close()
            raise

    # ------------------------------------------------------------------
    # Internal coroutines
    # ------------------------------------------------------------------

    async def _trigger_watcher(self, trigger: TriggerPort) -> None:
        """Run a single trigger indefinitely, forwarding tasks to the queue."""
        logger.info("drive_loop: starting trigger %r", trigger.name)
        try:
            await trigger.run(self.enqueue)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("drive_loop: trigger %r raised unexpected error: %s", trigger.name, exc)

    async def _task_executor(self) -> None:
        """Drain the priority queue and execute tasks with the semaphore cap."""
        logger.info("drive_loop: task executor started")
        while True:
            # Reserve execution capacity before removing a durable journal entry.
            # Otherwise one extra task lives only in this coroutine while waiting
            # on the semaphore and disappears if the daemon stops in that window.
            await self._semaphore.acquire()
            try:
                _, _, task = await self._queue.get()
            except BaseException:
                self._semaphore.release()
                raise
            self._inflight_tasks[task.task_id] = task
            self._persist_queue()
            self._record_queue_state()
            logger.info(
                "drive_loop: dequeued task %s (%r) queue_size=%d active=%d",
                task.task_id,
                task.title,
                self._queue.qsize(),
                len(self._active_tasks),
            )

            # Check deadline again after any time in queue
            if task.deadline is not None and datetime.now(UTC) > task.deadline:
                logger.info(
                    "drive_loop: task %s (%r) deadline exceeded — discarding",
                    task.task_id,
                    task.title,
                )
                self._inflight_tasks.pop(task.task_id, None)
                self._persist_queue()
                self._queue.task_done()
                self._semaphore.release()
                continue

            asyncio_task = asyncio.create_task(
                self._run_task(task),
                name=f"initiative:{task.task_id}",
            )
            self._active_tasks[task.task_id] = asyncio_task
            asyncio_task.add_done_callback(lambda _t, tid=task.task_id: self._on_task_done(tid, _t))
            self._queue.task_done()

    def _on_task_done(self, task_id: str, finished_task: asyncio.Task[Any]) -> None:
        outcome = "exception"
        try:
            outcome = str(finished_task.result())
        except asyncio.CancelledError:
            outcome = "interrupted"
        except Exception as exc:
            logger.error("drive_loop: task %s crashed before completion handling: %s", task_id, exc)
        if not (self._shutting_down and outcome == "interrupted"):
            self._inflight_tasks.pop(task_id, None)
        self._persist_queue()
        self._active_tasks.pop(task_id, None)
        self._semaphore.release()
        self._record_queue_state()
        # Signal any waiters that this task is complete
        event = self._completion_events.get(task_id)
        if event is not None:
            event.set()

    def _wrap_activity_channel(self, channel: ChannelPort, task: AgentTask) -> ChannelPort:
        """Attach stable outer session/task ids to live activity events."""
        root_corr = task.root_correlation_id or task.task_id
        return TaskContextChannel(
            channel,
            correlation_id=task.task_id,
            session_id=task.session_id,
            task_id=task.task_id,
            root_correlation_id=root_corr,
        )

    def _retrieval_reflex_injector(self) -> ReflexInjector | None:
        """Lazily build the retrieval reflex injector from mimir.reflex config."""
        if self._reflex_initialised:
            return self._reflex_injector
        self._reflex_initialised = True
        self._reflex_injector = build_reflex_injector(getattr(self._settings, "mimir", None))
        return self._reflex_injector

    async def _apply_retrieval_reflex(self, prompt: str, task: AgentTask) -> str:
        """Prefix known Mímir entity pointers onto the turn prompt (NIU-1059).

        Fail-open: any reflex failure logs an ERROR and returns the prompt
        unchanged — the reflex must never crash or block a turn.
        """
        try:
            injector = self._retrieval_reflex_injector()
            if injector is None:
                return prompt
            return await injector.apply(prompt, task.session_id or task.task_id)
        except Exception:
            logger.error(
                "drive_loop: retrieval reflex failed for task %s — continuing without pointers",
                task.task_id,
                exc_info=True,
            )
            return prompt

    async def _run_task(self, task: AgentTask) -> str:
        telemetry = get_observability()
        attributes = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": task.persona or "ravn",
            "gen_ai.agent.id": self._settings.mesh.own_peer_id or self._source_id,
            "ravn.task.id": task.task_id,
            "ravn.task.trigger": task.triggered_by,
            "ravn.task.root_correlation_id": task.root_correlation_id,
            "ravn.environment.id": self._settings.environment.id,
            "ravn.environment.type": self._settings.environment.type,
        }
        metric_attributes = {
            key: attributes[key]
            for key in (
                "gen_ai.agent.name",
                "ravn.task.trigger",
                "ravn.environment.id",
                "ravn.environment.type",
            )
        }
        started = monotonic()
        restart_recovery = task.task_id in self._restored_inflight_task_ids
        restart_link = restart_recovery and bool(task.trace_context)
        if restart_link:
            attributes["ravn.trace.relationship"] = "restart_recovery_link"
            telemetry.count(
                "ravn.trace.boundaries",
                attributes={
                    "ravn.trace.relationship": "restart_recovery_link",
                    "ravn.trace.component": "agent_task",
                    "ravn.runtime.component": "resident",
                },
                description="Explicit cross-process and restart trace boundaries.",
            )
        trace_kwargs = (
            {"link_carrier": task.trace_context}
            if restart_link
            else {"carrier": task.trace_context}
        )
        with telemetry.span(
            f"invoke_agent {task.persona or 'ravn'}",
            attributes=attributes,
            **trace_kwargs,
        ) as span:
            try:
                outcome = await self._run_task_observed(task)
            except Exception:
                telemetry.count(
                    "ravn.agent.tasks",
                    attributes={**metric_attributes, "ravn.task.outcome": "exception"},
                )
                telemetry.duration(
                    "ravn.agent.task.duration",
                    monotonic() - started,
                    attributes={**metric_attributes, "ravn.task.outcome": "exception"},
                )
                raise
            span.set_attribute("ravn.task.outcome", outcome)
            telemetry.count(
                "ravn.agent.tasks",
                attributes={**metric_attributes, "ravn.task.outcome": outcome},
            )
            telemetry.duration(
                "ravn.agent.task.duration",
                monotonic() - started,
                attributes={**metric_attributes, "ravn.task.outcome": outcome},
                description="Duration of a resident agent task.",
            )
            return outcome

    async def _recover_interrupted_tool_operations(
        self,
        agent: object,
        task: AgentTask,
    ) -> list[str]:
        if task.task_id not in self._restored_inflight_task_ids:
            return []
        self._restored_inflight_task_ids.discard(task.task_id)

        tools = getattr(agent, "tools", [])
        if callable(tools):
            tools = tools()
        if not isinstance(tools, list):
            return []

        recovered: list[str] = []
        max_chars = int(getattr(self._settings.tools, "max_result_chars", 0) or 0)
        token = self._current_task_var.set(task)
        try:
            for tool in tools:
                recover = getattr(tool, "recover_pending", None)
                if not callable(recover):
                    continue
                try:
                    results = await recover()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "drive_loop: durable tool recovery failed for task %s tool=%s: %s",
                        task.task_id,
                        getattr(tool, "name", type(tool).__name__),
                        exc,
                    )
                    get_observability().event(
                        "ravn.tool.recovery.failed",
                        attributes={
                            "ravn.task.id": task.task_id,
                            "gen_ai.tool.name": str(getattr(tool, "name", type(tool).__name__)),
                            "error.type": type(exc).__name__,
                        },
                        content={"error": str(exc)},
                    )
                    recovered.append(
                        f"### {getattr(tool, 'name', type(tool).__name__)} (error)\n\n"
                        f"Durable operation recovery failed: {exc}"
                    )
                    continue
                for result in results or []:
                    content = str(getattr(result, "content", result))
                    if max_chars > 0 and len(content) > max_chars:
                        content = content[:max_chars].rstrip() + "\n… (truncated)"
                    status = "error" if bool(getattr(result, "is_error", False)) else "completed"
                    recovered.append(
                        f"### {getattr(tool, 'name', type(tool).__name__)} ({status})\n\n{content}"
                    )
        finally:
            self._current_task_var.reset(token)
        return recovered

    async def _run_task_observed(self, task: AgentTask) -> str:
        """Execute a single initiative task."""
        telemetry = get_observability()
        telemetry.event(
            "ravn.task.lifecycle",
            attributes={"ravn.task.phase": "admitted"},
            content={
                "task_id": task.task_id,
                "title": task.title,
                "triggered_by": task.triggered_by,
                "root_correlation_id": task.root_correlation_id,
                "resident_case_id": task.resident_case_id,
                "resident_turn_index": task.resident_turn_index,
            },
        )
        # Budget pre-check: skip when daily cap is reached.
        # The task is already persisted in the journal and will be retried
        # after the UTC day rolls over and the budget resets.
        if not self._budget.can_spend():
            logger.info(
                "drive_loop: task %s (%r) skipped — daily budget cap reached "
                "(spent=%.4f remaining=%.4f)",
                task.task_id,
                task.title,
                self._budget.spent_today_usd,
                self._budget.remaining_usd,
            )
            await self._emit_sleipnir_task_dropped(task, reason="budget_cap_reached")
            return "budget_dropped"

        try:
            telemetry.event(
                "ravn.task.lifecycle",
                attributes={"ravn.task.phase": "setup_started"},
            )
            # Track the capture channel separately for response_text access.
            # Residents with cascade enabled already capture bounded activity;
            # do not change channel behaviour merely to feed the HUD.
            capture_channel: CaptureChannel | None = None
            peer_id = self._settings.mesh.own_peer_id if self._settings.mesh.enabled else ""
            logger.info("drive_loop: task %s setting up channels", task.task_id)
            if self._settings.cascade.enabled:
                self._result_store.start(task.task_id, task.triggered_by)
                capture_channel = CaptureChannel(task.task_id, self._result_store)
                extra: list[ChannelPort] = []
                if self._skuld_channel is not None:
                    self._skuld_channel._persona = task.persona  # Update persona for this task
                    extra.append(self._wrap_activity_channel(self._skuld_channel, task))
                elif self._mesh is not None and peer_id:
                    extra.append(
                        self._wrap_activity_channel(MeshActivityChannel(self._mesh, peer_id), task)
                    )
                if extra:
                    channel: ChannelPort = CompositeChannel([capture_channel, *extra])
                else:
                    channel = capture_channel
            else:
                sinks: list[ChannelPort] = []
                if self._skuld_channel is not None:
                    self._skuld_channel._persona = task.persona
                    sinks.append(self._wrap_activity_channel(self._skuld_channel, task))
                elif self._mesh is not None and peer_id:
                    sinks.append(
                        self._wrap_activity_channel(MeshActivityChannel(self._mesh, peer_id), task)
                    )
                if sinks:
                    channel = CompositeChannel(sinks) if len(sinks) > 1 else sinks[0]
                else:
                    channel = SilentChannel()
            logger.info("drive_loop: task %s building agent", task.task_id)
            agent = self._agent_factory(channel, task.task_id, task.persona, task.triggered_by)
            telemetry.event(
                "ravn.task.lifecycle",
                attributes={
                    "ravn.task.phase": "agent_built",
                    "ravn.task.persona": task.persona or "ravn",
                    "ravn.agent.tool_count": len(getattr(agent, "_tools", {}) or {}),
                },
            )
            recovered_tool_results = await self._recover_interrupted_tool_operations(
                agent,
                task,
            )
            logger.info("drive_loop: task %s building prompt", task.task_id)
            prompt_task = task
            if self._resident_runtime is not None:
                resident_context = await self._resident_runtime.prepare_context(task)
                prompt_task = replace(task, initiative_context=resident_context)
            if recovered_tool_results:
                recovery_context = "\n\n".join(
                    (
                        "## Interrupted operation recovery",
                        "The runtime reconciled durable tool work that was interrupted "
                        "during the previous process. Treat these truthful tool results "
                        "as part of the current case:",
                        *recovered_tool_results,
                    )
                )
                prompt_task = replace(
                    prompt_task,
                    initiative_context=(f"{prompt_task.initiative_context}\n\n{recovery_context}"),
                )
            prompt = build_initiative_prompt(prompt_task)
            prompt = await self._apply_retrieval_reflex(prompt, task)
            telemetry.event(
                "ravn.task.prompt",
                attributes={
                    "ravn.task.phase": "prompt_built",
                    "ravn.prompt.characters": len(prompt),
                },
                content=prompt,
            )
            logger.info("drive_loop: task %s setup complete", task.task_id)
        except Exception as exc:
            logger.error("drive_loop: task %s failed during setup: %s", task.task_id, exc)
            raise

        logger.info(
            "drive_loop: executing task %s (%r) triggered_by=%r",
            task.task_id,
            task.title,
            task.triggered_by,
        )
        await self._emit_sleipnir_task_started(task)
        telemetry.event(
            "ravn.task.lifecycle",
            attributes={"ravn.task.phase": "model_turn_started"},
        )

        await channel.emit(
            RavnEvent.task_started(
                source=self._source_id,
                task_id=task.task_id,
                title=task.title,
                correlation_id=task.task_id,
                session_id=task.session_id or task.task_id,
            )
        )

        await self._event_publisher.publish(
            RavnEvent(
                type=RavnEventType.TASK_STARTED,
                source=self._source_id,
                payload={
                    "task_id": task.task_id,
                    "title": task.title,
                    "triggered_by": task.triggered_by,
                },
                timestamp=datetime.now(UTC),
                urgency=0.2,
                correlation_id=task.task_id,
                session_id=task.session_id,
                task_id=task.task_id,
            )
        )

        success = False
        response_text = ""
        turn_result = None
        resident_disposition = None
        token = self._current_task_var.set(task)
        self._active_task_contexts[task.task_id] = task
        self._active_agents[task.task_id] = agent
        try:
            try:
                turn_result = await agent.run_turn(prompt)  # type: ignore[attr-defined]
                success = True
                telemetry.event(
                    "ravn.task.lifecycle",
                    attributes={
                        "ravn.task.phase": "model_turn_completed",
                        "ravn.agent.tool_call_count": len(
                            getattr(turn_result, "tool_calls", []) or []
                        ),
                    },
                    content=getattr(turn_result, "response", ""),
                )
                cost_usd = self._record_task_cost(task, turn_result)
                await self._emit_task_usage(channel, task, turn_result, cost_usd, agent)
                response_text = (
                    capture_channel.response_text
                    if capture_channel
                    else str(getattr(turn_result, "response", "") or "")
                )
                repair_result = await self._maybe_repair_resident_valkyrie_outcome(
                    agent=agent,
                    task=task,
                    response_text=response_text,
                )
                if repair_result is not None:
                    repair_cost_usd = self._record_task_cost(task, repair_result)
                    await self._emit_task_usage(
                        channel, task, repair_result, repair_cost_usd, agent
                    )
                    turn_result = repair_result
                    response_text = repair_result.response
                workflow_repair_result = await self._maybe_repair_unroutable_workflow_outcome(
                    agent=agent,
                    task=task,
                    response_text=response_text,
                )
                if workflow_repair_result is not None:
                    repair_cost_usd = self._record_task_cost(task, workflow_repair_result)
                    await self._emit_task_usage(
                        channel, task, workflow_repair_result, repair_cost_usd, agent
                    )
                    turn_result = workflow_repair_result
                    response_text = workflow_repair_result.response
                structured_outcome, resident_outcome_valid = self._decorate_turn_result_outcome(
                    task, turn_result, response_text
                )
                if isinstance(structured_outcome, Mapping):
                    judgment_attributes = {
                        "ravn.valkyrie.decision": _clean_string(structured_outcome.get("decision")),
                        "ravn.valkyrie.continuation": _clean_string(
                            structured_outcome.get("continuation")
                        ),
                        "ravn.valkyrie.selected_next_action": _clean_string(
                            structured_outcome.get("selected_next_action")
                        ),
                        "ravn.valkyrie.tier": _clean_string(structured_outcome.get("tier")),
                        "ravn.valkyrie.action_authority": _clean_string(
                            structured_outcome.get("action_authority")
                        ),
                        "ravn.valkyrie.outcome_valid": bool(resident_outcome_valid),
                    }
                    confidence = structured_outcome.get("confidence")
                    if isinstance(confidence, int | float):
                        judgment_attributes["ravn.valkyrie.confidence"] = confidence
                    telemetry.set_attributes(judgment_attributes)
                    telemetry.event(
                        "ravn.valkyrie.judgment",
                        attributes=judgment_attributes,
                        content=dict(structured_outcome),
                    )
                    telemetry.count(
                        "ravn.valkyrie.judgments",
                        attributes={
                            key: judgment_attributes[key]
                            for key in (
                                "ravn.valkyrie.decision",
                                "ravn.valkyrie.continuation",
                                "ravn.valkyrie.tier",
                                "ravn.valkyrie.action_authority",
                                "ravn.valkyrie.outcome_valid",
                            )
                        },
                    )
                if self._resident_runtime is not None:
                    resident_disposition = await self._resident_runtime.handle_completed_turn(
                        task=task,
                        prompt=prompt,
                        result=turn_result,
                        response_text=response_text,
                        outcome_fields=structured_outcome,
                        outcome_valid=resident_outcome_valid,
                    )
                    if (
                        str(getattr(resident_disposition, "kind", "")) == "ask_operator"
                        and not task.resident_help_published
                    ):
                        await self._emit_resident_help_needed(task, resident_disposition)
                    telemetry.event(
                        "ravn.resident.disposition",
                        attributes={
                            "ravn.resident.disposition": str(
                                getattr(resident_disposition, "kind", "")
                            ),
                            "ravn.resident.case_id": task.resident_case_id
                            or task.root_correlation_id
                            or task.task_id,
                        },
                    )
                await self._maybe_publish_budget_warning(task)
                self._save_task_output(task, capture_channel or channel)
                if response_text:
                    logger.info(
                        "drive_loop: task %s output: %s",
                        task.task_id,
                        response_text[:500],
                    )
            except asyncio.CancelledError:
                telemetry.event(
                    "ravn.task.lifecycle",
                    attributes={"ravn.task.phase": "interrupted"},
                )
                logger.info("drive_loop: task %s cancelled mid-turn", task.task_id)
                self._result_store.set_status(task.task_id, "cancelled")
                await self._event_publisher.publish(
                    RavnEvent(
                        type=RavnEventType.TASK_COMPLETE,
                        source=self._source_id,
                        payload={"task_id": task.task_id, "success": False},
                        timestamp=datetime.now(UTC),
                        urgency=0.7,
                        correlation_id=task.task_id,
                        session_id=task.session_id,
                        task_id=task.task_id,
                    )
                )
                emit_fn = getattr(agent, "emit_session_ended", None)
                if emit_fn is not None and asyncio.iscoroutinefunction(emit_fn):
                    try:
                        await emit_fn("interrupted")
                    except Exception:
                        logger.warning("emit_session_ended failed; continuing", exc_info=True)
                await self._emit_sleipnir_task_completed(task, "interrupted")
                if task.triggered_by and task.triggered_by.startswith("thread:"):
                    thread_path = task.triggered_by.removeprefix("thread:")
                    await self._finalise_thread(thread_path, False)
                return "interrupted"
            except Exception as exc:
                success = False
                telemetry.event(
                    "ravn.task.lifecycle",
                    attributes={
                        "ravn.task.phase": "failed",
                        "error.type": type(exc).__name__,
                    },
                    content=str(exc),
                )
                logger.error("drive_loop: task %s failed: %s", task.task_id, exc)
                self._result_store.set_status(task.task_id, "failed")
                await channel.emit(
                    RavnEvent.error(
                        source=self._source_id,
                        message=self._format_task_error(task, exc),
                        correlation_id=task.task_id,
                        session_id=task.session_id or task.task_id,
                        task_id=task.task_id,
                        failure_kind=type(exc).__name__,
                    )
                )

            outcome = "success" if success else "error"
            telemetry.event(
                "ravn.task.lifecycle",
                attributes={
                    "ravn.task.phase": "completed",
                    "ravn.task.outcome": outcome,
                },
            )
            emit_fn = getattr(agent, "emit_session_ended", None)
            if emit_fn is not None and asyncio.iscoroutinefunction(emit_fn):
                try:
                    await emit_fn(outcome)
                except Exception:
                    logger.warning("emit_session_ended failed; continuing", exc_info=True)
            if capture_channel is not None:
                response_text = capture_channel.response_text
            await self._emit_sleipnir_task_completed(task, outcome, response_text=response_text)
            if success:
                await self._emit_sleipnir_mimir_dream_completed(task, response_text=response_text)

            # Publish outcome event to mesh for other agents to consume
            await self._emit_mesh_outcome_event(task, response_text, success)

            await self._event_publisher.publish(
                RavnEvent(
                    type=RavnEventType.TASK_COMPLETE,
                    source=self._source_id,
                    payload={"task_id": task.task_id, "success": success},
                    timestamp=datetime.now(UTC),
                    urgency=0.2 if success else 0.7,
                    correlation_id=task.task_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                )
            )
            await channel.emit(
                RavnEvent.task_complete(
                    source=self._source_id,
                    success=success,
                    correlation_id=task.task_id,
                    session_id=task.session_id or task.task_id,
                    task_id=task.task_id,
                )
            )

            if capture_channel and capture_channel.surface_triggered:
                await self._re_deliver_surface(task, capture_channel.response_text)
            elif (
                capture_channel is None
                and getattr(channel, "surface_triggered", False)
                and hasattr(channel, "response_text")
            ):
                await self._re_deliver_surface(task, channel.response_text)

            if task.triggered_by and task.triggered_by.startswith("thread:"):
                thread_path = task.triggered_by.removeprefix("thread:")
                await self._finalise_thread(thread_path, success)
            return outcome
        finally:
            if not success and self._resident_runtime is not None:
                release = getattr(self._resident_runtime, "release_failed_task", None)
                if release is not None:
                    release(task)
            close_agent = getattr(agent, "close", None)
            if close_agent is not None and inspect.iscoroutinefunction(close_agent):
                try:
                    await close_agent()
                except Exception:
                    logger.warning(
                        "drive_loop: failed to close agent for task %s",
                        task.task_id,
                        exc_info=True,
                    )
            self._active_agents.pop(task.task_id, None)
            self._active_task_contexts.pop(task.task_id, None)
            self._current_task_var.reset(token)

    def _format_task_error(self, task: AgentTask, exc: Exception) -> str:
        """Render a user-visible task failure message for live room chat."""
        if isinstance(exc, LLMError):
            return (
                f"LLM backend unavailable while handling `{task.title}`: {exc}. "
                "The task will not make progress until the upstream model/service recovers."
            )
        return f"{type(exc).__name__}: {exc}"

    async def _emit_resident_help_needed(self, task: AgentTask, disposition: object) -> None:
        """Surface one persisted resident question through existing transports."""
        case_id = str(getattr(disposition, "case_id", "") or task.task_id)
        event = build_help_needed_event(
            source=self._source_id,
            persona=task.persona or (self._persona_config.name if self._persona_config else ""),
            reason=str(getattr(disposition, "reason", "") or "operator input required"),
            summary=str(getattr(disposition, "question", "") or "Operator input required"),
            recommendation="Answer this resident continuation with the requested guidance.",
            correlation_id=task.task_id,
            session_id=task.session_id,
            task_id=task.task_id,
            context={
                "resident_case_id": case_id,
                "continuation_id": case_id,
                "root_correlation_id": task.root_correlation_id or case_id,
                "operator_ref": str(getattr(disposition, "operator_ref", "")),
            },
            trace_context=get_observability().inject() or task.trace_context,
        )
        await self._publish_help_needed(task, event)

    async def _publish_help_needed(self, task: AgentTask, event: RavnEvent) -> None:
        """Publish an LLM-selected help request through the configured operator transports."""
        telemetry = get_observability()
        attributes = {
            "ravn.task.id": task.task_id,
            "ravn.resident.case_id": task.resident_case_id or task.task_id,
            "ravn.operator.skuld_configured": self._skuld_channel is not None,
            "ravn.operator.mesh_configured": self._mesh is not None,
        }
        with telemetry.span(
            "ravn.operator.help.publish",
            attributes=attributes,
            carrier=event.trace_context or task.trace_context,
        ):
            if self._mesh is not None:
                try:
                    mesh_event = event
                    if self._skuld_channel is not None:
                        mesh_event = replace(
                            event,
                            payload={
                                **event.payload,
                                "collaboration_routing_only": True,
                            },
                        )
                    await self._mesh.publish(
                        mesh_event,
                        topic=str(RavnEventType.HELP_NEEDED),
                    )
                    telemetry.event("ravn.operator.help.mesh_published")
                except Exception:
                    logger.warning(
                        "Failed to publish resident help_needed; continuing.", exc_info=True
                    )
            if self._skuld_channel is not None:
                try:
                    await self._skuld_channel.emit(event)
                    telemetry.event("ravn.operator.help.skuld_published")
                except Exception:
                    logger.warning("Failed to emit resident help_needed to skuld.", exc_info=True)
            await self._event_publisher.publish(event)
            task.resident_help_published = True

    async def _maybe_repair_resident_valkyrie_outcome(
        self,
        *,
        agent: object,
        task: AgentTask,
        response_text: str,
    ) -> object | None:
        """Ask the same agent for one strict contract repair when a resident output is invalid."""
        if task.triggered_by == _RESIDENT_VALKYRIE_SCHEMA_REPAIR_TRIGGER:
            return None
        canonical_event_type, outcome_fields, validation_errors = (
            _resident_valkyrie_validation_result(
                response_text,
                self._persona_config,
                authoritative_fields=self._authoritative_valkyrie_fields(task),
            )
        )
        if not canonical_event_type or not validation_errors:
            return None
        run_turn = getattr(agent, "run_turn", None)
        if run_turn is None or not asyncio.iscoroutinefunction(run_turn):
            return None

        logger.warning(
            "drive_loop: repairing invalid resident Valkyrie outcome "
            "event_type=%s task_id=%s errors=%s",
            canonical_event_type,
            task.task_id,
            validation_errors,
        )
        repair_prompt = _build_resident_valkyrie_schema_repair_prompt(
            task=task,
            original_response=response_text,
            validation_errors=validation_errors,
            outcome_fields=outcome_fields,
        )
        try:
            repair_result = await run_turn(repair_prompt)
        except Exception:
            logger.warning(
                "drive_loop: resident Valkyrie schema repair failed; "
                "continuing with original response",
                exc_info=True,
            )
            return None

        repaired_text = str(getattr(repair_result, "response", "") or "")
        _, _, repaired_errors = _resident_valkyrie_validation_result(
            repaired_text,
            self._persona_config,
            authoritative_fields=self._authoritative_valkyrie_fields(task),
        )
        if repaired_errors:
            logger.warning(
                "drive_loop: resident Valkyrie schema repair still invalid task_id=%s errors=%s",
                task.task_id,
                repaired_errors,
            )
        else:
            logger.info(
                "drive_loop: resident Valkyrie schema repair succeeded task_id=%s",
                task.task_id,
            )
        return repair_result

    async def _maybe_repair_unroutable_workflow_outcome(
        self,
        *,
        agent: object,
        task: AgentTask,
        response_text: str,
    ) -> object | None:
        """Give a workflow agent one chance to revise an outcome the graph cannot route."""
        if not task.workflow_node_id or self._workflow_allowed_outcomes_resolver is None:
            return None
        canonical_event_type = str(
            getattr(getattr(self._persona_config, "produces", None), "event_type", "") or ""
        )
        if not canonical_event_type or is_valkyrie_outcome_event(canonical_event_type):
            return None
        parsed = _parse_outcome_for_persona(response_text, self._persona_config)
        if parsed is None or not parsed.valid:
            return None
        verdict = str(
            parsed.fields.get("verdict", "") or parsed.fields.get("decision", "") or ""
        ).strip()
        if not verdict or verdict == "help_needed":
            return None
        event_type_map = getattr(self._persona_config.produces, "event_type_map", {}) or {}
        alias_event_type = str(event_type_map.get(verdict) or "")
        allowed_topics = self._workflow_allowed_outcomes_resolver(task, self._persona_config)
        if allowed_topics is None:
            return None
        if canonical_event_type in allowed_topics or alias_event_type in allowed_topics:
            return None
        run_turn = getattr(agent, "run_turn", None)
        if run_turn is None or not asyncio.iscoroutinefunction(run_turn):
            return None

        telemetry = get_observability()
        attributes = {
            "ravn.task.id": task.task_id,
            "ravn.workflow.node.id": task.workflow_node_id,
            "ravn.workflow.outcome.verdict": verdict,
            "ravn.workflow.outcome.alias": alias_event_type,
        }
        logger.warning(
            "drive_loop: repairing unroutable workflow outcome task_id=%s "
            "node=%s verdict=%s alias=%s allowed=%s",
            task.task_id,
            task.workflow_node_id,
            verdict,
            alias_event_type or "-",
            sorted(allowed_topics),
        )
        telemetry.event(
            "ravn.workflow.outcome.repair_requested",
            attributes=attributes,
            content={"allowed_topics": sorted(allowed_topics)},
        )
        prompt = _build_workflow_outcome_repair_prompt(
            task=task,
            original_response=response_text,
            allowed_topics=allowed_topics,
        )
        try:
            result = await run_turn(prompt)
        except Exception as exc:
            logger.warning(
                "drive_loop: workflow outcome repair failed; continuing with original response",
                exc_info=True,
            )
            telemetry.event(
                "ravn.workflow.outcome.repair_failed",
                attributes={**attributes, "error.type": type(exc).__name__},
                content={"error": str(exc)},
            )
            return None
        telemetry.event(
            "ravn.workflow.outcome.repair_completed",
            attributes=attributes,
            content=getattr(result, "response", ""),
        )
        return result

    async def _emit_sleipnir_task_completed(
        self,
        task: AgentTask,
        outcome: str,
        *,
        response_text: str = "",
    ) -> None:
        """Publish ravn.task.completed to Sleipnir (no-op when publisher absent)."""
        if self._sleipnir_publisher is None or _sleipnir_task_completed is None:
            return
        try:
            persona = getattr(task, "persona", "") or ""
            correlation_id = task.session_id or task.task_id
            event = _sleipnir_task_completed(
                task_id=task.task_id,
                persona=persona,
                outcome=outcome,
                source=self._source_id,
                correlation_id=correlation_id,
            )
            if task.session_id:
                event.payload["session_id"] = task.session_id
            event.payload.update(self._sleipnir_task_context_payload(task))

            parsed = _parse_outcome_for_persona(response_text, self._persona_config)
            if parsed is not None:
                fields = dict(parsed.fields)
                canonical_event_type = str(
                    getattr(getattr(self._persona_config, "produces", None), "event_type", "") or ""
                )
                if is_valkyrie_outcome_event(canonical_event_type):
                    _, fields, validation_errors = _resident_valkyrie_validation_result(
                        response_text,
                        self._persona_config,
                        authoritative_fields=self._authoritative_valkyrie_fields(task),
                    )
                    outcome_valid = not validation_errors
                else:
                    outcome_valid = parsed.valid
                event.payload["structured_outcome"] = _json_safe(fields)
                event.payload["outcome_valid"] = outcome_valid
                for key in (
                    "verdict",
                    "tests_passing",
                    "scope_adherence",
                    "pr_url",
                    "summary",
                    "files_changed",
                ):
                    if key in parsed.fields:
                        event.payload[key] = _json_safe(parsed.fields[key])
            safe_payload = _json_safe(event.payload)
            if isinstance(safe_payload, dict):
                event.payload.clear()
                event.payload.update(safe_payload)
            await self._sleipnir_publisher.publish(event)
        except Exception:
            logger.warning("Failed to emit ravn.task.completed; continuing.", exc_info=True)

    async def _emit_sleipnir_task_started(self, task: AgentTask) -> None:
        """Publish ravn.task.started so resident queues are observable from NATS."""
        payload = {
            **self._sleipnir_task_context_payload(task),
            "task_id": task.task_id,
            "title": task.title,
            "persona": task.persona or "",
            "triggered_by": task.triggered_by,
            "priority": task.priority,
            "queue_size": self._queue.qsize(),
            "active_count": len(self._active_tasks),
        }
        event = SleipnirEvent(
            event_type=_RAVN_TASK_STARTED,
            source=self._source_id,
            payload=payload,
            summary=f"Ravn task {task.task_id} started: {task.title}",
            urgency=0.3,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
            correlation_id=task.root_correlation_id or task.session_id or task.task_id,
            causation_id=task.workflow_parent_event_id,
            tenant_id=payload.get("tenant_id") or None,
        )
        await self._publish_sleipnir_event(event, log_label=_RAVN_TASK_STARTED)

    async def _emit_sleipnir_task_dropped(self, task: AgentTask, *, reason: str) -> None:
        """Publish ravn.task.dropped for capacity/deadline/budget loss accounting."""
        payload = {
            **self._sleipnir_task_context_payload(task),
            "task_id": task.task_id,
            "title": task.title,
            "persona": task.persona or "",
            "triggered_by": task.triggered_by,
            "priority": task.priority,
            "reason": reason,
            "queue_size": self._queue.qsize(),
            "queue_max": self._config.task_queue_max,
            "active_count": len(self._active_tasks),
        }
        event = SleipnirEvent(
            event_type=_RAVN_TASK_DROPPED,
            source=self._source_id,
            payload=payload,
            summary=f"Ravn task {task.task_id} dropped: {reason}",
            urgency=0.7,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
            correlation_id=task.root_correlation_id or task.session_id or task.task_id,
            causation_id=task.workflow_parent_event_id,
            tenant_id=payload.get("tenant_id") or None,
        )
        await self._publish_sleipnir_event(event, log_label=_RAVN_TASK_DROPPED)

    async def _emit_sleipnir_mimir_dream_completed(
        self,
        task: AgentTask,
        *,
        response_text: str,
    ) -> None:
        """Publish mimir.dream.completed for dream-cycle tasks.

        Dream completion is orchestration bookkeeping, so the drive loop emits
        it deterministically from the final outcome instead of requiring the
        Mímir warden persona to call an extra publish tool.
        """
        if task.triggered_by != _DREAM_CYCLE_TRIGGER:
            return
        if self._sleipnir_publisher is None or _sleipnir_mimir_dream_completed is None:
            return

        try:
            counts = _extract_mimir_dream_counts(response_text, self._persona_config)
            event = _sleipnir_mimir_dream_completed(
                pages_updated=counts["pages_updated"],
                entities_created=counts["entities_created"],
                lint_fixes=counts["lint_fixes"],
                source=self._source_id,
                correlation_id=task.session_id or task.task_id,
            )
            event.payload["task_id"] = task.task_id
            if task.persona:
                event.payload["persona"] = task.persona
            if task.session_id:
                event.payload["session_id"] = task.session_id
            await self._sleipnir_publisher.publish(event)
        except Exception:
            logger.warning("Failed to emit mimir.dream.completed; continuing.", exc_info=True)

    async def emit_tool_outcome_event(
        self,
        *,
        task: AgentTask,
        persona_name: str,
        event_type: str,
        fields: dict[str, object],
    ) -> None:
        """Publish a canonical workflow outcome emitted deterministically by a tool.

        This keeps tool-originated workflow events on the same mesh/Skuld path
        as persona outcomes, which makes them visible to downstream subscribers,
        room/session timelines, and Ting's outer orchestration without inventing
        a second signaling mechanism.
        """
        if not event_type:
            return

        if self._workflow_allowed_outcomes_resolver is not None:
            allowed_topics = self._workflow_allowed_outcomes_resolver(task, self._persona_config)
            if allowed_topics is not None and event_type not in allowed_topics:
                logger.info(
                    (
                        "drive_loop: suppressing tool outcome event_type=%s "
                        "workflow_node=%s allowed=%s"
                    ),
                    event_type,
                    task.workflow_node_id or "-",
                    sorted(allowed_topics),
                )
                return

        root_corr = task.root_correlation_id or task.task_id
        payload: dict[str, object] = {
            "persona": persona_name,
            "success": True,
            "outcome": fields,
            "fields": fields,
            "valid": True,
            "task_id": task.task_id,
            "workflow_node_id": task.workflow_node_id,
            "event_type": event_type,
            "bubble_up": True,
        }
        if self._skuld_channel is not None:
            payload["collaboration_routing_only"] = True
        if task.workflow_parent_event_id:
            payload["workflow_parent_event_id"] = task.workflow_parent_event_id
        summary = fields.get("summary")
        if isinstance(summary, str) and summary.strip():
            payload["summary"] = summary.strip()

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=self._source_id,
            payload=payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id=task.task_id,
            session_id=task.session_id or "",
            task_id=task.task_id,
            root_correlation_id=root_corr,
        )

        if self._mesh is not None:
            try:
                logger.info(
                    "drive_loop: publishing tool outcome event_type=%s task_id=%s",
                    event_type,
                    task.task_id,
                )
                await self._mesh.publish(event, topic=event_type)
            except Exception:
                logger.warning(
                    "Failed to publish tool-originated mesh outcome event; continuing.",
                    exc_info=True,
                )

        if self._skuld_channel is not None:
            try:
                await self._skuld_channel.emit(event)
            except Exception:
                logger.warning(
                    "Failed to emit tool-originated outcome to skuld; continuing.",
                    exc_info=True,
                )

    async def _emit_mesh_outcome_event(
        self, task: AgentTask, response_text: str, success: bool
    ) -> None:
        """Publish persona outcomes for both routing and outward visibility.

        Canonical outcome events always keep the persona's declared
        ``produces.event_type``. Those canonical outcomes are what we bubble up
        to Skuld/Volundr/Ting so session history and higher-level orchestration
        see a stable contract.

        Verdict-specific aliases from ``event_type_map`` remain valid, but they
        are published as routing-only mesh topics so downstream personas can
        react without replacing the canonical outward event.
        """
        if self._persona_config is None:
            return
        if not success:
            logger.info(
                "drive_loop: skipping mesh outcome publish for failed task_id=%s persona=%s",
                task.task_id,
                self._persona_config.name,
            )
            return

        # Parse outcome block from response
        parsed = _parse_outcome_for_persona(response_text, self._persona_config)
        outcome_fields = dict(parsed.fields) if parsed is not None else {}
        canonical_event_type = self._persona_config.produces.event_type
        if not canonical_event_type:
            return
        tool_fields = task.tool_outcomes.get(canonical_event_type) or {}
        for key, value in tool_fields.items():
            outcome_fields.setdefault(key, value)
        if canonical_event_type == "mimir.source.ingested":
            for key, value in self._default_mimir_mount_fields().items():
                outcome_fields.setdefault(key, value)
        if is_valkyrie_outcome_event(canonical_event_type):
            outcome_fields = normalize_valkyrie_outcome(canonical_event_type, outcome_fields)
            outcome_fields = _apply_authoritative_valkyrie_fields(
                outcome_fields,
                self._authoritative_valkyrie_fields(task),
            )

        event_type_map = self._persona_config.produces.event_type_map
        success_verdict = _default_success_verdict(self._persona_config.produces)
        synthesized_pass = False
        root_corr = task.root_correlation_id or task.task_id
        allowed_topics: set[str] | None = None
        if self._workflow_allowed_outcomes_resolver is not None:
            allowed_topics = self._workflow_allowed_outcomes_resolver(task, self._persona_config)

        page_write_fields = task.tool_outcomes.get(_MIMIR_PAGE_WRITTEN_OUTCOME) or {}
        verdict = str(
            outcome_fields.get("verdict", "") or outcome_fields.get("decision", "") or ""
        ).strip()
        valid = bool(parsed.valid) if parsed is not None else False
        synthesized_from_tool_write = False
        if not verdict and page_write_fields:
            inferred_verdict = _infer_tool_written_verdict(
                allowed_topics=allowed_topics,
                event_type_map=event_type_map,
            )
            if inferred_verdict:
                verdict = inferred_verdict
                outcome_fields.setdefault("verdict", inferred_verdict)
                for key, value in page_write_fields.items():
                    outcome_fields.setdefault(key, value)
                page_path = str(outcome_fields.get("page_path", "") or "").strip()
                if page_path and not str(outcome_fields.get("summary", "") or "").strip():
                    outcome_fields["summary"] = f"Wrote {page_path}"
                synthesized_from_tool_write = True
                valid = True
        if not verdict and success and success_verdict:
            verdict = success_verdict
            outcome_fields.setdefault("verdict", verdict)
            synthesized_pass = True
        elif verdict:
            outcome_fields.setdefault("verdict", verdict)
            normalized_verdict = _normalize_outcome_verdict(verdict, self._persona_config.produces)
            if normalized_verdict != verdict:
                verdict = normalized_verdict
                outcome_fields["verdict"] = normalized_verdict
        outcome_fields = _json_safe(outcome_fields)  # type: ignore[assignment]
        summary = str(outcome_fields.get("summary", "") or "").strip()
        question = str(outcome_fields.get("question", "") or "").strip()
        files_changed = outcome_fields.get("files_changed")
        if (synthesized_pass or synthesized_from_tool_write) and not valid:
            valid = True

        if is_valkyrie_outcome_event(canonical_event_type):
            _, outcome_fields, validation_errors = _resident_valkyrie_validation_result(
                response_text,
                self._persona_config,
                authoritative_fields=self._authoritative_valkyrie_fields(task),
            )
            if validation_errors:
                reject_payload: dict[str, object] = {
                    "persona": self._persona_config.name,
                    "success": False,
                    "event_type": VALKYRIE_JUDGMENT_REJECTED,
                    "canonical_event_type": canonical_event_type,
                    "outcome": outcome_fields,
                    "fields": outcome_fields,
                    "valid": False,
                    "errors": validation_errors,
                    "task_id": task.task_id,
                    "workflow_node_id": task.workflow_node_id,
                    "bubble_up": True,
                }
                if self._skuld_channel is not None:
                    reject_payload["collaboration_routing_only"] = True
                if task.workflow_parent_event_id:
                    reject_payload["workflow_parent_event_id"] = task.workflow_parent_event_id
                reject_event = RavnEvent(
                    type=RavnEventType.OUTCOME,
                    source=self._source_id,
                    payload=reject_payload,
                    timestamp=datetime.now(UTC),
                    urgency=0.8,
                    correlation_id=task.task_id,
                    session_id=task.session_id or "",
                    task_id=task.task_id,
                    root_correlation_id=root_corr,
                )
                logger.warning(
                    "drive_loop: rejecting invalid resident Valkyrie outcome "
                    "event_type=%s task_id=%s errors=%s",
                    canonical_event_type,
                    task.task_id,
                    validation_errors,
                )
                if self._mesh is not None:
                    try:
                        await self._mesh.publish(reject_event, topic=VALKYRIE_JUDGMENT_REJECTED)
                    except Exception:
                        logger.warning(
                            "Failed to publish resident Valkyrie rejected outcome; continuing.",
                            exc_info=True,
                        )
                if self._skuld_channel is not None:
                    try:
                        await self._skuld_channel.emit(reject_event)
                    except Exception:
                        logger.warning(
                            "Failed to emit resident Valkyrie rejected outcome to skuld; "
                            "continuing.",
                            exc_info=True,
                        )
                await self._emit_sleipnir_valkyrie_outcome(
                    VALKYRIE_JUDGMENT_REJECTED,
                    outcome_fields,
                    task,
                    root_corr,
                    valid=False,
                    errors=validation_errors,
                    canonical_event_type=canonical_event_type,
                )
                return
            valid = True

        outcome_errors: list[str] = []
        if verdict == "help_needed" and not question:
            outcome_errors.append("help_needed requires a non-empty question")

        artifact_publish_error = ""
        if not outcome_errors:
            artifact_publish_error = await self._maybe_publish_workflow_artifacts(
                task,
                outcome_fields,
            )
        if not outcome_errors and not artifact_publish_error:
            await self._maybe_materialize_workflow_artifacts(task, outcome_fields)
        if artifact_publish_error:
            outcome_errors.append(artifact_publish_error)

        base_payload: dict[str, object] = {
            "persona": self._persona_config.name,
            "success": success and not outcome_errors,
            "outcome": outcome_fields,
            "fields": outcome_fields,
            "valid": valid and not outcome_errors,
            "task_id": task.task_id,
            "workflow_node_id": task.workflow_node_id,
        }
        if outcome_errors:
            base_payload["errors"] = outcome_errors
        if artifact_publish_error:
            base_payload["artifact_publish_error"] = artifact_publish_error
        if task.workflow_parent_event_id:
            base_payload["workflow_parent_event_id"] = task.workflow_parent_event_id
        if verdict:
            base_payload["verdict"] = verdict
        if summary:
            base_payload["summary"] = summary
        if isinstance(files_changed, list) and files_changed:
            base_payload["files_changed"] = files_changed

        alias_event_type = ""
        if verdict and verdict in event_type_map:
            alias_event_type = event_type_map[verdict]

        if allowed_topics is not None:
            canonical_allowed = canonical_event_type in allowed_topics or (
                bool(alias_event_type) and alias_event_type in allowed_topics
            )
            if verdict == "help_needed":
                canonical_allowed = True
            if not canonical_allowed:
                logger.info(
                    "drive_loop: suppressing outcome event_type=%s workflow_node=%s allowed=%s",
                    canonical_event_type,
                    task.workflow_node_id or "-",
                    sorted(allowed_topics),
                )
                return

        canonical_payload = dict(base_payload)
        canonical_payload["event_type"] = canonical_event_type
        canonical_payload["bubble_up"] = True
        if self._skuld_channel is not None:
            canonical_payload["collaboration_routing_only"] = True

        canonical_event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=self._source_id,
            payload=canonical_payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id=task.task_id,
            session_id=task.session_id or "",
            task_id=task.task_id,
            root_correlation_id=root_corr,
        )

        if self._mesh is not None and not outcome_errors:
            try:
                logger.info(
                    "drive_loop: publishing canonical outcome event_type=%s task_id=%s",
                    canonical_event_type,
                    task.task_id,
                )
                await self._mesh.publish(canonical_event, topic=canonical_event_type)
            except Exception:
                logger.warning(
                    "Failed to publish canonical mesh outcome event; continuing.",
                    exc_info=True,
                )

        if self._skuld_channel is not None:
            try:
                await self._skuld_channel.emit(canonical_event)
            except Exception:
                logger.warning(
                    "Failed to emit canonical outcome to skuld; continuing.",
                    exc_info=True,
                )

        if is_valkyrie_outcome_event(canonical_event_type):
            await self._emit_sleipnir_valkyrie_outcome(
                canonical_event_type,
                outcome_fields,
                task,
                root_corr,
                valid=valid,
            )

        if outcome_errors:
            return

        if self._mesh is None:
            mesh_available = False
        else:
            mesh_available = True

        if verdict == "help_needed" and not task.resident_help_published:
            attempted = outcome_fields.get("attempted")
            if not isinstance(attempted, list):
                attempted = []
            help_context = outcome_fields.get("context")
            if not isinstance(help_context, dict):
                help_context = {}
            help_context = {
                **help_context,
                "root_correlation_id": root_corr,
                "workflow_parent_event_id": task.workflow_parent_event_id,
                "workflow_node_id": task.workflow_node_id,
                "session_id": task.session_id,
            }
            help_event = build_help_needed_event(
                source=self._source_id,
                persona=self._persona_config.name,
                reason=str(outcome_fields.get("reason") or "needs_context"),
                summary=question,
                attempted=attempted,
                recommendation=str(
                    outcome_fields.get("recommendation")
                    or "Reply directly to this agent with the missing guidance."
                ),
                correlation_id=task.task_id,
                session_id=task.session_id or "",
                task_id=task.task_id,
                context=help_context,
                trace_context=get_observability().inject() or task.trace_context,
            )
            await self._publish_help_needed(task, help_event)

        if not mesh_available:
            return

        if not alias_event_type or alias_event_type == canonical_event_type:
            return

        alias_payload = dict(base_payload)
        alias_payload["event_type"] = alias_event_type
        alias_payload["canonical_event_type"] = canonical_event_type
        alias_payload["routing_only"] = True
        alias_payload["bubble_up"] = False

        alias_event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=self._source_id,
            payload=alias_payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id=task.task_id,
            session_id=task.session_id or "",
            task_id=task.task_id,
            root_correlation_id=root_corr,
        )
        try:
            logger.info(
                "drive_loop: publishing routing outcome alias=%s canonical=%s task_id=%s",
                alias_event_type,
                canonical_event_type,
                task.task_id,
            )
            await self._mesh.publish(alias_event, topic=alias_event_type)
        except Exception:
            logger.warning(
                "Failed to publish routing mesh outcome alias; continuing.",
                exc_info=True,
            )

        if self._skuld_channel is not None:
            try:
                await self._skuld_channel.emit(alias_event)
            except Exception:
                logger.warning(
                    "Failed to emit routing outcome alias to skuld; continuing.",
                    exc_info=True,
                )

    async def _emit_sleipnir_valkyrie_outcome(
        self,
        event_type: str,
        outcome_fields: dict[str, object],
        task: AgentTask,
        root_correlation_id: str,
        *,
        valid: bool,
        errors: list[str] | None = None,
        canonical_event_type: str = "",
    ) -> None:
        """Mirror validated resident Valkyrie outcomes onto Sleipnir/NATS."""
        payload: dict[str, object] = {
            **self._sleipnir_task_context_payload(task),
            **outcome_fields,
            "task_id": task.task_id,
            "persona": task.persona or getattr(self._persona_config, "name", "") or "",
            "valid": valid,
        }
        if errors:
            payload["errors"] = errors
        if canonical_event_type:
            payload["canonical_event_type"] = canonical_event_type
        payload = _json_safe(payload)  # type: ignore[assignment]

        environment_id = str(payload.get("environment_id") or payload.get("environmentId") or "")
        valkyrie_id = str(payload.get("valkyrie_id") or payload.get("valkyrieId") or "")
        confidence = _coerce_float(payload.get("confidence"), default=0.5)
        tier = str(payload.get("tier") or payload.get("attention_tier") or "ambient")
        urgency = max(0.2, min(1.0, confidence if tier == "urgent" else confidence * 0.8))
        event = SleipnirEvent(
            event_type=event_type,
            source=f"ravn:valkyrie:{environment_id or 'unknown'}",
            payload=payload,
            summary=self._valkyrie_outcome_summary(
                event_type,
                environment_id=environment_id,
                valkyrie_id=valkyrie_id,
                payload=payload,
            ),
            urgency=urgency if valid else 0.8,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
            correlation_id=root_correlation_id,
            causation_id=task.workflow_parent_event_id,
            tenant_id=str(payload.get("tenant_id") or "") or None,
        )
        await self._publish_sleipnir_event(event, log_label=event_type)

    @staticmethod
    def _valkyrie_outcome_summary(
        event_type: str,
        *,
        environment_id: str,
        valkyrie_id: str,
        payload: dict[str, object],
    ) -> str:
        if event_type == VALKYRIE_JUDGMENT_REJECTED:
            return f"Valkyrie {valkyrie_id or 'unknown'} judgment rejected"
        if event_type.startswith("valkyrie.judgment."):
            from sleipnir.domain.catalog import judgment_summary  # noqa: PLC0415

            evidence = payload.get("evidence")
            return judgment_summary(
                valkyrie_id=valkyrie_id,
                environment_id=environment_id,
                attention_tier=str(
                    payload.get("tier") or payload.get("attention_tier") or "ambient"
                ),
                recommended_action=str(payload.get("recommended_action") or ""),
                evidence=evidence if isinstance(evidence, list) else None,
            )
        if event_type.startswith("valkyrie.action."):
            capability = str(
                payload.get("capability") or payload.get("recommended_action") or "action"
            )
            return f"Valkyrie {valkyrie_id or 'unknown'} action in {environment_id}: {capability}"
        return f"Valkyrie {valkyrie_id or 'unknown'} outcome in {environment_id}"

    def _sleipnir_task_context_payload(self, task: AgentTask) -> dict[str, object]:
        """Return shared task/environment metadata for durable telemetry events."""
        environment = getattr(self._settings, "environment", None)
        mesh = getattr(self._settings, "mesh", None)
        env_id = _clean_string(getattr(environment, "id", ""), default="")
        env_type = _clean_string(getattr(environment, "type", ""), default="")
        env_name = _clean_string(getattr(environment, "name", ""), default=env_id)
        valkyrie_id = _clean_string(getattr(mesh, "own_peer_id", ""), default="")
        tenant_id = env_id
        return {
            "environment_id": env_id,
            "environment_name": env_name,
            "environment_type": env_type,
            "tenant_id": tenant_id,
            "valkyrie_id": valkyrie_id,
            "session_id": task.session_id or "",
            "root_correlation_id": task.root_correlation_id or task.task_id,
            "workflow_parent_event_id": task.workflow_parent_event_id or "",
            "workflow_node_id": task.workflow_node_id or "",
        }

    async def _publish_sleipnir_event(self, event: SleipnirEvent, *, log_label: str) -> None:
        if self._sleipnir_publisher is None:
            return
        try:
            await self._sleipnir_publisher.publish(event)
        except Exception:
            logger.warning("Failed to emit %s; continuing.", log_label, exc_info=True)

    def _save_task_output(self, task: AgentTask, channel: ChannelPort) -> None:
        """Persist agent response to ``task.output_path`` when set (cron tasks)."""
        if task.output_path is None:
            return
        text = getattr(channel, "response_text", "")
        try:
            task.output_path.parent.mkdir(parents=True, exist_ok=True)
            header = f"# {task.title}\n\ntriggered_by: {task.triggered_by}\n\n"
            task.output_path.write_text(header + text)
            logger.debug("drive_loop: saved task output to %s", task.output_path)
        except Exception as exc:
            logger.warning("drive_loop: failed to save task output: %s", exc)

    async def _maybe_publish_budget_warning(self, task: AgentTask) -> None:
        """Publish a DECISION warning event once per UTC day when spend crosses the threshold."""
        if not self._budget.warn_threshold_reached:
            return
        if self._budget.warn_emitted_today:
            return
        budget_cfg = getattr(self._settings, "budget", None)
        if isinstance(budget_cfg, BudgetConfig):
            daily_cap_usd = budget_cfg.daily_cap_usd
            warn_at_percent = budget_cfg.warn_at_percent
        else:
            daily_cap_usd = self._budget._daily_cap_usd
            warn_at_percent = self._budget._warn_at_percent
        logger.warning(
            "drive_loop: daily budget warning — spent=%.4f remaining=%.4f cap=%.4f",
            self._budget.spent_today_usd,
            self._budget.remaining_usd,
            daily_cap_usd,
        )
        self._budget.mark_warn_emitted()
        await self._event_publisher.publish(
            RavnEvent(
                type=RavnEventType.DECISION,
                source=self._source_id,
                payload={
                    "budget_warning": True,
                    "spent_today_usd": self._budget.spent_today_usd,
                    "remaining_usd": self._budget.remaining_usd,
                    "daily_cap_usd": daily_cap_usd,
                    "warn_at_percent": warn_at_percent,
                },
                timestamp=datetime.now(UTC),
                urgency=0.5,
                correlation_id=task.task_id,
                session_id=task.session_id,
                task_id=task.task_id,
            )
        )

    def _record_task_cost(self, task: AgentTask, turn_result: object) -> float | None:
        """Compute cost from turn_result.usage and record it on the budget tracker."""
        usage = getattr(turn_result, "usage", None)
        if usage is None:
            return None
        input_tokens: int = getattr(usage, "input_tokens", 0)
        output_tokens: int = getattr(usage, "output_tokens", 0)
        budget_cfg = getattr(self._settings, "budget", None)
        if isinstance(budget_cfg, BudgetConfig):
            input_rate = budget_cfg.input_token_cost_per_million
            output_rate = budget_cfg.output_token_cost_per_million
        else:
            input_rate = 3.0
            output_rate = 15.0
        cost_usd = compute_cost(input_tokens, output_tokens, input_rate, output_rate)
        self._budget.record(cost_usd)
        logger.debug(
            "drive_loop: task %s cost=%.6f spent_today=%.6f remaining=%.6f",
            task.task_id,
            cost_usd,
            self._budget.spent_today_usd,
            self._budget.remaining_usd,
        )
        return cost_usd

    async def _emit_task_usage(
        self,
        channel: ChannelPort,
        task: AgentTask,
        turn_result: object,
        cost_usd: float | None,
        agent: object,
    ) -> None:
        usage = getattr(turn_result, "usage", None)
        if usage is None:
            return

        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read_tokens = int(getattr(usage, "cache_read_tokens", 0) or 0)
        cache_write_tokens = int(getattr(usage, "cache_write_tokens", 0) or 0)
        thinking_tokens = int(getattr(usage, "thinking_tokens", 0) or 0)
        if input_tokens + output_tokens + cache_read_tokens + cache_write_tokens <= 0:
            return

        model = str(getattr(agent, "_model", "") or getattr(self._settings.llm, "model", ""))
        usage_id = (
            f"{task.task_id}:{id(turn_result)}:{model}:{input_tokens}:{output_tokens}:"
            f"{cache_read_tokens}:{cache_write_tokens}:{thinking_tokens}"
        )
        await channel.emit(
            RavnEvent.usage(
                source=self._source_id,
                model=model or "unknown",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                thinking_tokens=thinking_tokens,
                cost_usd=cost_usd,
                usage_id=usage_id,
                persona=task.persona,
                correlation_id=task.task_id,
                session_id=task.session_id or task.task_id,
                task_id=task.task_id,
            )
        )

    async def _re_deliver_surface(self, task: AgentTask, text: str) -> None:
        """Re-publish a [SURFACE]-prefixed response to Sleipnir at AMBIENT urgency."""
        logger.info(
            "drive_loop: task %s surface escalation — publishing to Sleipnir",
            task.task_id,
        )
        event = RavnEvent(
            type=RavnEventType.RESPONSE,
            source=self._source_id,
            payload={"text": text, "surface_escalation": True},
            timestamp=datetime.now(UTC),
            urgency=0.7,
            correlation_id=task.task_id,
            session_id=task.session_id,
            task_id=task.task_id,
        )
        await self._event_publisher.publish(event)

    async def _finalise_thread(self, thread_path: str, success: bool) -> None:
        """Transition thread state after task completion.

        On success: ``pulling → closed``.
        On failure: ``pulling → open`` and ownership is released.
        All Mímir errors are caught and logged; never propagated.
        """
        if self._mimir is None:
            return
        try:
            if success:
                await self._mimir.update_thread_state(thread_path, ThreadState.closed)
                logger.info("drive_loop: thread %r closed after successful task", thread_path)
            else:
                await self._mimir.update_thread_state(thread_path, ThreadState.open)
                await self._mimir.assign_thread_owner(thread_path, None)
                logger.info("drive_loop: thread %r returned to open after failed task", thread_path)
        except Exception as exc:
            logger.warning("drive_loop: failed to finalise thread %r: %s", thread_path, exc)

    async def _heartbeat(self) -> None:
        """Publish periodic heartbeat events via the event publisher."""
        while True:
            await asyncio.sleep(self._config.heartbeat_interval_seconds)
            active = len(self._active_tasks)
            queued = self._queue.qsize()
            event = RavnEvent(
                type=RavnEventType.TASK_STARTED,
                source=self._source_id,
                payload={
                    "active_tasks": active,
                    "queued_tasks": queued,
                    "trigger_count": len(self._triggers),
                    "budget_spent_usd": round(self._budget.spent_today_usd, 6),
                    "budget_remaining_usd": round(self._budget.remaining_usd, 6),
                    "heartbeat": True,
                },
                timestamp=datetime.now(UTC),
                urgency=0.0,
                correlation_id="heartbeat",
                session_id="daemon",
                task_id=None,
            )
            await self._event_publisher.publish(event)
            self._record_queue_state()
            logger.debug(
                "drive_loop: heartbeat — active=%d queued=%d triggers=%d",
                active,
                queued,
                len(self._triggers),
            )

            # Sweep expired fan-in slots
            expired = self._fan_in.sweep_expired()
            if expired:
                self._persist_queue()

    # ------------------------------------------------------------------
    # Queue journal
    # ------------------------------------------------------------------

    def _persist_queue(self) -> None:
        """Snapshot pending and in-flight tasks to the journal file."""
        try:
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            items = list(self._queue._queue)  # type: ignore[attr-defined]
            records = [self._task_journal_record(task) for _prio, _counter, task in items]
            inflight = [self._task_journal_record(task) for task in self._inflight_tasks.values()]
            journal = {"queue": records, "inflight": inflight}
            if self._fan_in.pending_count > 0:
                journal["fan_in_pending"] = self._fan_in.to_dict()
            temporary_path = self._journal_path.with_suffix(f"{self._journal_path.suffix}.tmp")
            temporary_path.write_text(json.dumps(journal, indent=2))
            temporary_path.replace(self._journal_path)
        except Exception as exc:
            logger.warning("drive_loop: failed to persist queue journal: %s", exc)

    @staticmethod
    def _task_journal_record(task: AgentTask) -> dict[str, object]:
        return {
            "task_id": task.task_id,
            "title": task.title,
            "initiative_context": task.initiative_context,
            "triggered_by": task.triggered_by,
            "output_mode": str(task.output_mode),
            "persona": task.persona,
            "priority": task.priority,
            "max_tokens": task.max_tokens,
            "deadline": task.deadline.isoformat() if task.deadline else None,
            "output_path": str(task.output_path) if task.output_path else None,
            "root_correlation_id": task.root_correlation_id,
            "workflow_parent_event_id": task.workflow_parent_event_id,
            "workflow_node_id": task.workflow_node_id,
            "tool_outcomes": task.tool_outcomes,
            "human_initiated": task.human_initiated,
            "resident_case_id": task.resident_case_id,
            "resident_mandate": task.resident_mandate,
            "resident_turn_index": task.resident_turn_index,
            "resident_input_tokens": task.resident_input_tokens,
            "resident_output_tokens": task.resident_output_tokens,
            "resident_started_at": task.resident_started_at,
            "resident_parent_turn_ref": task.resident_parent_turn_ref,
            "resident_inbox_refs": task.resident_inbox_refs,
            "resident_answer_ref": task.resident_answer_ref,
            "resident_wake_ref": task.resident_wake_ref,
            "resident_help_published": task.resident_help_published,
            "trace_context": task.trace_context,
            "created_at": task.created_at.isoformat(),
        }

    def _load_journal(self) -> None:
        """Restore pending tasks and fan-in state from the journal file."""
        if not self._journal_path.exists():
            return
        try:
            raw = json.loads(self._journal_path.read_text())
        except Exception as exc:
            logger.warning("drive_loop: failed to load queue journal: %s", exc)
            return

        # Support old format (bare list) and new format (dict with queue key)
        if isinstance(raw, list):
            records = raw
            inflight_task_ids: set[str] = set()
            fan_in_data: dict = {}
        else:
            inflight_records = raw.get("inflight", [])
            records = [*inflight_records, *raw.get("queue", [])]
            inflight_task_ids = {
                str(record.get("task_id") or "")
                for record in inflight_records
                if isinstance(record, dict)
            }
            fan_in_data = raw.get("fan_in_pending", {})

        restored = 0
        for rec in records:
            try:
                deadline = datetime.fromisoformat(rec["deadline"]) if rec.get("deadline") else None
                created_at = (
                    datetime.fromisoformat(rec["created_at"])
                    if rec.get("created_at")
                    else datetime.now(UTC)
                )
                output_path_str = rec.get("output_path")
                task = AgentTask(
                    task_id=rec["task_id"],
                    title=rec["title"],
                    initiative_context=rec["initiative_context"],
                    triggered_by=rec["triggered_by"],
                    output_mode=OutputMode(rec.get("output_mode", "silent")),
                    persona=rec.get("persona"),
                    priority=rec.get("priority", 10),
                    max_tokens=rec.get("max_tokens"),
                    deadline=deadline,
                    output_path=Path(output_path_str) if output_path_str else None,
                    root_correlation_id=rec.get("root_correlation_id", ""),
                    workflow_parent_event_id=rec.get("workflow_parent_event_id", ""),
                    workflow_node_id=rec.get("workflow_node_id", ""),
                    tool_outcomes=rec.get("tool_outcomes", {}) or {},
                    human_initiated=rec.get("human_initiated", False),
                    resident_case_id=rec.get("resident_case_id", ""),
                    resident_mandate=rec.get("resident_mandate", ""),
                    resident_turn_index=rec.get("resident_turn_index", 0),
                    resident_input_tokens=rec.get("resident_input_tokens", 0),
                    resident_output_tokens=rec.get("resident_output_tokens", 0),
                    resident_started_at=rec.get("resident_started_at", ""),
                    resident_parent_turn_ref=rec.get("resident_parent_turn_ref", ""),
                    resident_inbox_refs=rec.get("resident_inbox_refs", []) or [],
                    resident_answer_ref=rec.get("resident_answer_ref", ""),
                    resident_wake_ref=rec.get("resident_wake_ref", ""),
                    resident_help_published=rec.get("resident_help_published", False),
                    trace_context=rec.get("trace_context", {}) or {},
                    created_at=created_at,
                )
                if deadline is not None and datetime.now(UTC) > deadline:
                    logger.info(
                        "drive_loop: journal task %s deadline exceeded — skipping",
                        task.task_id,
                    )
                    continue
                counter = self._next_counter()
                self._queue.put_nowait((task.priority, counter, task))
                track = getattr(self._resident_runtime, "track_task", None)
                if track is not None:
                    track(task)
                if task.task_id in inflight_task_ids:
                    self._restored_inflight_task_ids.add(task.task_id)
                restored += 1
            except Exception as exc:
                logger.warning("drive_loop: failed to restore journal entry: %s", exc)

        if restored:
            logger.info("drive_loop: restored %d task(s) from journal", restored)

        # Restore fan-in buffer state
        if fan_in_data:
            self._fan_in.load_dict(fan_in_data)
            logger.info(
                "drive_loop: restored %d fan-in slot(s) from journal",
                self._fan_in.pending_count,
            )
