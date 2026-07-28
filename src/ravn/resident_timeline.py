"""Project durable resident turns into a working-state timeline.

The resident's working state is overwritten in place: only the latest snapshot
survives at ``working-state/{resident}.md``. The *history* lives in the
append-only turn records, each of which carries the ``working_state`` the model
authored on that turn inside its outcome fields.

This module reconstructs that history through ``ResidentStatePort`` and
computes, per turn, which entries appeared, survived, or were dropped — the
difference between a snapshot of a mind and a record of one changing.

Outcome fields are persisted with ``repr()``, so parsing belongs here in Python
rather than in whatever renders the result.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ravn.domain.resident_continuation import RESIDENT_WORKING_STATE_FIELDS
from ravn.domain.resident_state import ResidentStatePort

_TURN_SEGMENT = "/turns/"
_OUTCOME_HEADING = "Outcome Fields"


@dataclass(frozen=True)
class ResidentStateChange:
    """What happened to one working-state list between consecutive turns."""

    added: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "added": list(self.added),
            "retained": list(self.retained),
            "removed": list(self.removed),
        }


@dataclass(frozen=True)
class ResidentTimelineTurn:
    """One durable turn, with the working state it produced."""

    turn_ref: str
    turn_index: int
    case_id: str
    root_correlation_id: str
    task_id: str
    triggered_by: str
    updated_at: str
    persona: str
    tools_used: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    continuation: str
    next_action_timing: str
    selected_next_action: str
    rationale: str
    judgment: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    inbox_refs: tuple[str, ...] = ()
    working_state: dict[str, list[str]] = field(default_factory=dict)
    working_state_turn_index: int = 0
    working_state_updated_at: str = ""
    working_state_authored: bool = False
    changes: dict[str, ResidentStateChange] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_ref": self.turn_ref,
            "turn_index": self.turn_index,
            "case_id": self.case_id,
            "root_correlation_id": self.root_correlation_id,
            "task_id": self.task_id,
            "triggered_by": self.triggered_by,
            "updated_at": self.updated_at,
            "persona": self.persona,
            "tools_used": list(self.tools_used),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "continuation": self.continuation,
            "next_action_timing": self.next_action_timing,
            "selected_next_action": self.selected_next_action,
            "rationale": self.rationale,
            "judgment": self.judgment,
            "evidence_refs": list(self.evidence_refs),
            "inbox_refs": list(self.inbox_refs),
            "working_state": {key: list(value) for key, value in self.working_state.items()},
            "working_state_turn_index": self.working_state_turn_index,
            "working_state_updated_at": self.working_state_updated_at,
            "working_state_authored": self.working_state_authored,
            "changes": {key: value.as_dict() for key, value in self.changes.items()},
        }


@dataclass(frozen=True)
class ResidentTimeline:
    """The reconstructed history of one resident's model of its environment."""

    resident_id: str
    charter: str
    environment_name: str
    environment_type: str
    turns: tuple[ResidentTimelineTurn, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "resident_id": self.resident_id,
            "charter": self.charter,
            "environment": {
                "name": self.environment_name,
                "type": self.environment_type,
            },
            "fields": list(RESIDENT_WORKING_STATE_FIELDS),
            "turns": [turn.as_dict() for turn in self.turns],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, ensure_ascii=False)


async def build_resident_timeline(
    state: ResidentStatePort,
    *,
    resident_id: str = "resident",
    charter: str = "",
    environment_name: str = "",
    environment_type: str = "",
    prefix: str = "",
) -> ResidentTimeline:
    """Read every durable turn and project it into an ordered timeline."""
    refs = [ref for ref in await state.list_refs(prefix) if _TURN_SEGMENT in ref]
    turns: list[ResidentTimelineTurn] = []
    for ref in sorted(refs):
        entry = await state.read(ref)
        if entry is None:
            continue
        turn = _turn_from_content(ref, entry.content)
        if turn is not None:
            turns.append(turn)
    turns.sort(key=lambda item: (item.updated_at, item.turn_index))
    return ResidentTimeline(
        resident_id=resident_id,
        charter=charter,
        environment_name=environment_name,
        environment_type=environment_type,
        turns=tuple(_with_changes(turns)),
    )


def _with_changes(turns: list[ResidentTimelineTurn]) -> list[ResidentTimelineTurn]:
    """Attach per-field added/retained/removed against the previous turn."""
    projected: list[ResidentTimelineTurn] = []
    previous: dict[str, list[str]] = {}
    previous_turn_index = 0
    previous_updated_at = ""
    for turn in turns:
        authored = bool(turn.working_state)
        if authored:
            current_state = {key: list(value) for key, value in turn.working_state.items()}
            state_turn_index = turn.turn_index
            state_updated_at = turn.updated_at
        else:
            current_state = {key: list(value) for key, value in previous.items()}
            state_turn_index = previous_turn_index
            state_updated_at = previous_updated_at
        changes: dict[str, ResidentStateChange] = {}
        for name in RESIDENT_WORKING_STATE_FIELDS:
            if not authored:
                # A turn that authored no snapshot revised nothing. Diffing it
                # against the baseline would render as the resident dropping its
                # entire model, which is the opposite of what happened.
                changes[name] = ResidentStateChange()
                continue
            current_entries = current_state.get(name, [])
            prior_entries = previous.get(name, [])
            prior_set = set(prior_entries)
            current_set = set(current_entries)
            changes[name] = ResidentStateChange(
                added=tuple(item for item in current_entries if item not in prior_set),
                retained=tuple(item for item in current_entries if item in prior_set),
                removed=tuple(item for item in prior_entries if item not in current_set),
            )
        projected.append(
            ResidentTimelineTurn(
                turn_ref=turn.turn_ref,
                turn_index=turn.turn_index,
                case_id=turn.case_id,
                root_correlation_id=turn.root_correlation_id,
                task_id=turn.task_id,
                triggered_by=turn.triggered_by,
                updated_at=turn.updated_at,
                persona=turn.persona,
                tools_used=turn.tools_used,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                continuation=turn.continuation,
                next_action_timing=turn.next_action_timing,
                selected_next_action=turn.selected_next_action,
                rationale=turn.rationale,
                judgment=turn.judgment,
                evidence_refs=turn.evidence_refs,
                inbox_refs=turn.inbox_refs,
                working_state=current_state,
                working_state_turn_index=state_turn_index,
                working_state_updated_at=state_updated_at,
                working_state_authored=authored,
                changes=changes,
            )
        )
        # Only a turn that authored a snapshot moves the baseline; a turn with an
        # invalid outcome must not read as the resident dropping everything.
        if authored:
            previous = current_state
            previous_turn_index = turn.turn_index
            previous_updated_at = turn.updated_at
    return projected


def _turn_from_content(ref: str, content: str) -> ResidentTimelineTurn | None:
    fields = _outcome_fields(content)
    working_state = _normalized_working_state(fields.get("working_state"))
    judgment = {key: value for key, value in fields.items() if key != "working_state"}
    return ResidentTimelineTurn(
        turn_ref=ref,
        turn_index=_int_line(content, "turn", _index_from_ref(ref)),
        case_id=_line(content, "case_id"),
        root_correlation_id=_line(content, "root_correlation_id"),
        task_id=_line(content, "task_id"),
        triggered_by=_line(content, "triggered_by"),
        updated_at=_line(content, "updated_at"),
        persona=_line(content, "persona"),
        tools_used=_tools(content),
        input_tokens=_int_line(content, "input_tokens", 0),
        output_tokens=_int_line(content, "output_tokens", 0),
        continuation=str(fields.get("continuation") or ""),
        next_action_timing=str(fields.get("next_action_timing") or ""),
        selected_next_action=str(
            fields.get("selected_next_action") or _section(content, "Selected Next Action") or ""
        ),
        rationale=str(fields.get("rationale") or ""),
        judgment=judgment,
        evidence_refs=_section_items(content, "Evidence References"),
        inbox_refs=_section_items(content, "Inbox References"),
        working_state=working_state,
    )


def _outcome_fields(content: str) -> dict[str, Any]:
    """Recover the outcome mapping from its persisted ``repr()`` lines."""
    section = _section(content, _OUTCOME_HEADING)
    fields: dict[str, Any] = {}
    for line in section.splitlines():
        match = re.match(r"^- ([A-Za-z0-9_]+): (.*)$", line.strip())
        if match is None:
            continue
        key, raw = match.group(1), match.group(2)
        try:
            fields[key] = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            # A value that is not a Python literal is still useful as text.
            fields[key] = raw
    return fields


def _normalized_working_state(value: Any) -> dict[str, list[str]]:
    """Coerce a model-authored snapshot into displayable strings per field."""
    if not isinstance(value, dict):
        return {}
    state: dict[str, list[str]] = {}
    for name in RESIDENT_WORKING_STATE_FIELDS:
        entries = value.get(name)
        if not isinstance(entries, list):
            continue
        state[name] = [_entry_text(entry) for entry in entries if _entry_text(entry)]
    return state


def _entry_text(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
    return str(entry).strip()


def _section(content: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n+(.*?)(?=^## |\Z)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _section_items(content: str, heading: str) -> tuple[str, ...]:
    return tuple(
        line.removeprefix("- ").strip()
        for line in _section(content, heading).splitlines()
        if line.startswith("- ") and line.removeprefix("- ").strip() != "none"
    )


def _line(content: str, key: str) -> str:
    match = re.search(
        rf"^- {re.escape(key)}:[^\S\n]*(.*?)[^\S\n]*$",
        content,
        flags=re.MULTILINE,
    )
    return match.group(1).strip().strip("'\"") if match else ""


def _int_line(content: str, key: str, default: int) -> int:
    try:
        return int(_line(content, key))
    except ValueError:
        return default


def _tools(content: str) -> tuple[str, ...]:
    raw = _line(content, "tools_used")
    if not raw or raw == "none":
        return ()
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _index_from_ref(ref: str) -> int:
    match = re.search(r"-(\d+)\.md$", ref)
    return int(match.group(1)) if match else 0


__all__ = [
    "ResidentStateChange",
    "ResidentTimeline",
    "ResidentTimelineTurn",
    "build_resident_timeline",
]
