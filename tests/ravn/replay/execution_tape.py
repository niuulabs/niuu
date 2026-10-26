"""Deterministic, side-effect-free execution-tape replay for Ravn harness tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

TAPE_SCHEMA_VERSION = 1
_REQUIRED_USAGE_KEYS = frozenset({"input_tokens", "output_tokens"})


class TapeSchemaError(ValueError):
    """Raised when a versioned tape fixture is malformed."""


class TapeInvariantError(AssertionError):
    """Raised when replayed structural behavior differs from a tape contract."""


@dataclass(frozen=True)
class ExecutionTape:
    """A reviewed, provider-neutral record of harness-visible execution events."""

    case: str
    capabilities: frozenset[str]
    events: tuple[dict[str, Any], ...]
    expected: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> ExecutionTape:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TapeSchemaError(f"{path}: tape must be a JSON object")
        if raw.get("schema_version") != TAPE_SCHEMA_VERSION:
            raise TapeSchemaError(
                f"{path}: expected schema_version {TAPE_SCHEMA_VERSION}, got "
                f"{raw.get('schema_version')!r}"
            )
        case = raw.get("case")
        capabilities = raw.get("capabilities")
        events = raw.get("events")
        expected = raw.get("expected")
        if not isinstance(case, str) or not case:
            raise TapeSchemaError(f"{path}: case must be a non-empty string")
        if not isinstance(capabilities, list) or not all(
            isinstance(capability, str) and capability for capability in capabilities
        ):
            raise TapeSchemaError(f"{path}: capabilities must be a list of non-empty strings")
        if not isinstance(events, list) or not events:
            raise TapeSchemaError(f"{path}: events must be a non-empty list")
        event_has_kind = all(
            isinstance(event, dict) and isinstance(event.get("kind"), str) for event in events
        )
        if not event_has_kind:
            raise TapeSchemaError(f"{path}: every event must have a string kind")
        if not isinstance(expected, dict):
            raise TapeSchemaError(f"{path}: expected must be an object")
        return cls(case, frozenset(capabilities), tuple(events), expected)


@dataclass(frozen=True)
class ReplayResult:
    """Canonical replay facts suitable for native/CLI structural comparison."""

    harness_kind: Literal["native", "cli_backed"]
    event_kinds: tuple[str, ...]
    tool_order: tuple[str, ...]
    paired_tool_ids: tuple[str, ...]
    resume_points: tuple[str, ...]
    outcome: str | None
    usage_shape: str
    context_safe: bool
    capability_violations: int
    unknown_side_effect: bool

    def canonical(self) -> dict[str, object]:
        """Return the provider-content-free facts compared by differential mode."""
        return {
            "event_kinds": self.event_kinds,
            "tool_order": self.tool_order,
            "paired_tool_ids": self.paired_tool_ids,
            "resume_points": self.resume_points,
            "outcome": self.outcome,
            "usage_shape": self.usage_shape,
            "context_safe": self.context_safe,
            "capability_violations": self.capability_violations,
            "unknown_side_effect": self.unknown_side_effect,
        }


class ScriptedModel:
    """Consumes only model events from a tape; it never contacts a provider."""

    def __init__(self, events: tuple[dict[str, Any], ...]) -> None:
        self._events = iter(event for event in events if event["kind"] == "model")

    def next_response(self) -> dict[str, Any]:
        try:
            return next(self._events)
        except StopIteration as exc:
            raise TapeInvariantError("model was asked for an unscripted response") from exc


class ScriptedTransport:
    """Records dispatches without executing tools or performing external writes."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def dispatch(self, tool_call_id: str) -> None:
        self.dispatched.append(tool_call_id)


class ExecutionTapeRunner:
    """Folds a tape into canonical facts with explicit crash/resume boundaries."""

    def __init__(
        self,
        *,
        harness_kind: Literal["native", "cli_backed"] = "native",
        fold_event: Callable[[list[str], str], None] | None = None,
        ledger_result: Callable[[dict[str, bool], str], None] | None = None,
        capability_check: Callable[[frozenset[str], str], bool] | None = None,
        context_safe: Callable[[set[str], set[str]], bool] | None = None,
    ) -> None:
        self._harness_kind = harness_kind
        self._fold_event = fold_event or _append_event
        self._ledger_result = ledger_result or _record_result
        self._capability_check = capability_check or _has_capability
        self._context_safe = context_safe or _protected_context_is_retained

    def run(self, tape: ExecutionTape) -> ReplayResult:
        model = ScriptedModel(tape.events)
        transport = ScriptedTransport()
        event_kinds: list[str] = []
        requested: set[str] = set()
        dispatched: set[str] = set()
        completed: dict[str, bool] = {}
        tool_order: list[str] = []
        resume_points: list[str] = []
        protected: set[str] = set()
        removed: set[str] = set()
        context_safe = True
        capability_violations = 0
        unknown_side_effect = False
        outcome: str | None = None
        usage_seen = False
        usage_valid = True
        crashed = False

        for event in tape.events:
            kind = event["kind"]
            self._fold_event(event_kinds, kind)
            if kind == "model":
                response = model.next_response().get("response", {})
                tool_calls = response.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    raise TapeInvariantError("model response tool_calls must be a list")
                for call in tool_calls:
                    call_id = _required_string(call, "id", "model tool call")
                    requested.add(call_id)
                usage = event.get("usage")
                if usage is not None:
                    usage_seen = True
                    usage_valid = (
                        usage_valid
                        and isinstance(usage, dict)
                        and _REQUIRED_USAGE_KEYS <= usage.keys()
                    )
                continue
            if kind == "dispatch":
                call_id = _required_string(event, "tool_call_id", "dispatch")
                capability = _required_string(event, "capability", "dispatch")
                if call_id not in requested:
                    raise TapeInvariantError(f"dispatch {call_id!r} has no model tool call")
                if not self._capability_check(tape.capabilities, capability):
                    capability_violations += 1
                    continue
                transport.dispatch(call_id)
                dispatched.add(call_id)
                tool_order.append(call_id)
                continue
            if kind == "result":
                call_id = _required_string(event, "tool_call_id", "result")
                if call_id not in dispatched:
                    raise TapeInvariantError(f"result {call_id!r} was not dispatched")
                self._ledger_result(completed, call_id)
                continue
            if kind == "approval_pending":
                resume_points.append("approval_pending")
                continue
            if kind == "crash":
                phase = _required_string(event, "phase", "crash")
                if phase not in {"before_dispatch", "after_dispatch"}:
                    raise TapeInvariantError(f"unsupported crash phase {phase!r}")
                resume_points.append(phase)
                crashed = True
                continue
            if kind == "resume":
                if (
                    not crashed
                    and "approval_pending" not in resume_points
                    and "provider_session_lost" not in resume_points
                ):
                    raise TapeInvariantError("resume has no pending crash or approval")
                crashed = False
                continue
            if kind == "unknown_side_effect":
                unknown_side_effect = True
                continue
            if kind == "provider_session_lost":
                resume_points.append("provider_session_lost")
                continue
            if kind == "compaction":
                protected.update(_string_list(event, "protected_message_ids", "compaction"))
                removed.update(_string_list(event, "removed_message_ids", "compaction"))
                context_safe = context_safe and self._context_safe(protected, removed)
                continue
            if kind == "runtime_switch":
                _required_string(event, "from", "runtime_switch")
                _required_string(event, "to", "runtime_switch")
                continue
            if kind == "oversized_tool_result":
                original = event.get("original_bytes")
                stored = event.get("stored_bytes")
                if (
                    not isinstance(original, int)
                    or not isinstance(stored, int)
                    or not 0 <= stored < original
                ):
                    raise TapeInvariantError(
                        "oversized tool result must be safely reduced before context"
                    )
                continue
            if kind == "final":
                outcome = _required_string(event, "outcome", "final")
                continue
            raise TapeInvariantError(f"unknown tape event kind {kind!r}")

        if crashed:
            raise TapeInvariantError("tape ends while crashed without a resume")
        if not completed.keys() <= dispatched:
            raise TapeInvariantError("ledger contains a result without a dispatch")
        if not set(transport.dispatched) == dispatched:
            raise TapeInvariantError("transport dispatch ledger diverged")
        result = ReplayResult(
            harness_kind=self._harness_kind,
            event_kinds=tuple(event_kinds),
            tool_order=tuple(tool_order),
            paired_tool_ids=tuple(completed),
            resume_points=tuple(resume_points),
            outcome=outcome,
            usage_shape="reported" if usage_seen and usage_valid else "unreported",
            context_safe=context_safe,
            capability_violations=capability_violations,
            unknown_side_effect=unknown_side_effect,
        )
        self.assert_expected(tape, result)
        return result

    @staticmethod
    def assert_expected(tape: ExecutionTape, result: ReplayResult) -> None:
        expected = tape.expected
        actual = result.canonical()
        for field, expected_value in expected.items():
            if field not in actual:
                raise TapeSchemaError(f"{tape.case}: unsupported expected field {field!r}")
            if actual[field] != _as_tuple(expected_value, actual[field]):
                raise TapeInvariantError(
                    f"{tape.case}: {field} expected {expected_value!r}, got {actual[field]!r}"
                )


def structural_diff(left: ReplayResult, right: ReplayResult) -> dict[str, tuple[object, object]]:
    """Report canonical differences, never provider prose or tool payloads."""
    return {
        field: (left_value, right_value)
        for field, left_value in left.canonical().items()
        if (right_value := right.canonical()[field]) != left_value
    }


def _append_event(events: list[str], kind: str) -> None:
    events.append(kind)


def _record_result(completed: dict[str, bool], call_id: str) -> None:
    completed[call_id] = True


def _has_capability(capabilities: frozenset[str], capability: str) -> bool:
    return capability in capabilities


def _protected_context_is_retained(protected: set[str], removed: set[str]) -> bool:
    return protected.isdisjoint(removed)


def _required_string(data: dict[str, Any], field: str, subject: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise TapeInvariantError(f"{subject} requires non-empty {field}")
    return value


def _string_list(data: dict[str, Any], field: str, subject: str) -> list[str]:
    value = data.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise TapeInvariantError(f"{subject} requires {field} as non-empty strings")
    return value


def _as_tuple(expected: object, actual: object) -> object:
    if isinstance(actual, tuple) and isinstance(expected, list):
        return tuple(expected)
    return expected
