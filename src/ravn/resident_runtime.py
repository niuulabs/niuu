"""Durable resident turn, continuation, inbox, and operator coordination."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from niuu.observability import get_observability
from ravn.domain.models import AgentTask, OutputMode, TokenUsage, TurnResult
from ravn.domain.resident_continuation import (
    ContinuationDecisionKind,
    ResidentA2ATaskRecord,
    ResidentBudgetSnapshot,
    ResidentMemoryEntry,
    ResidentScheduledWakeRecord,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
    resident_working_state_from_outcome,
    selected_action_from_outcome,
    validate_resident_working_state,
)
from ravn.domain.resident_state import ResidentStatePort
from ravn.ports.trigger import TriggerPort
from ravn.resident_continuation import _parse_a2a_task, _scheduled_wake_at
from ravn.resident_inbox import (
    ResidentInboxBackend,
    ResidentInboxClassification,
    ResidentInboxSignal,
    ResidentInboxStatus,
    aggregate_summary_lines,
)
from ravn.resident_text import compact_line

logger = logging.getLogger(__name__)

EnqueueResidentTask = Callable[[AgentTask], Awaitable[bool | None]]

_A2A_TERMINAL_STATES = frozenset(
    {
        "TASK_STATE_CANCELED",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_REJECTED",
    }
)

_WORKING_STATE_PROTOCOL = """## Resident working-state protocol

When present, the durable state above was authored by an earlier resident turn. It is
not an authoritative fact store. Re-evaluate it against the new observations and evidence.
References such as `resident/...` and provider-specific URIs are opaque audit identifiers,
not workspace paths. Do not pass them to filesystem tools unless a tool explicitly supports
that reference scheme; the bounded content needed for this turn is already included above.

In the final structured outcome, include a `working_state` mapping with these lists:

- `objectives`: active operator-given or self-authored outcomes worth pursuing across turns
- `observations`: evidence-grounded observations with their authoritative references
- `hypotheses`: revisable interpretations, preserving uncertainty and supporting references
- `unknowns`: unresolved questions that could change a judgment
- `capability_gaps`: missing access or abilities that prevent useful investigation or action
- `attempts`: relevant research, delegation, operator outreach, or tool attempts and results

This is a current model, not an event log: replace superseded entries and keep at most five
entries per list, each no longer than 500 characters. Do not invent evidence or convert
hypotheses into observations. The runtime persists this mapping exactly; it does not interpret
the environment or manufacture state on your behalf.

Use available tools when they can materially reduce uncertainty, test a hypothesis, or perform
a needed action. Do not call a tool merely to demonstrate tool use.
The runtime will not turn a prose `selected_next_action` into another immediate model turn. If
useful progress requires a future external event or passage of time, use `sleep`; a new
observation or configured schedule will wake the resident. Use `ask_operator` when an operator's
knowledge, intent, or authority is the best available way to progress, and `stop` when no wake
is required. Declare `next_action_timing` as `external_event` or `scheduled_time` for sleep,
`operator_input` for ask_operator, and `none` for stop.

When you sleep with `scheduled_time`, also give `wake_at` as an ISO timestamp. The runtime
persists it and wakes this same case at that time; without a usable `wake_at` it falls back to
its configured default delay. Sleeping with `external_event` schedules nothing — the next
observation is the wake source."""


@dataclass(frozen=True)
class ResidentTurnDisposition:
    """Observable decision made after one durable resident turn."""

    kind: ContinuationDecisionKind
    case_id: str
    turn_ref: str
    budget_ref: str
    reason: str
    continuation_task_id: str = ""
    operator_ref: str = ""
    question: str = ""
    wake_ref: str = ""
    wake_at: str = ""


class ResidentRuntime:
    """Connect completed agent turns to the existing durable resident state."""

    def __init__(
        self,
        *,
        state: ResidentStatePort,
        inbox: ResidentInboxBackend | None = None,
        resident_id: str = "resident",
        resident_personality: str = "",
        charter: str = "",
        max_turns: int = 3,
        max_tokens: int = 0,
        context_max_chars: int = 12000,
        tool_result_max_chars: int = 2000,
        scheduled_wake_default_seconds: float = 3600.0,
        stewardship_interval_seconds: float = 0.0,
        directed_messages_enabled: bool = True,
    ) -> None:
        self._state = state
        self._inbox = inbox
        self._resident_id = resident_id.strip() or "resident"
        self._resident_personality = resident_personality.strip()
        self._charter = charter.strip()
        self._max_turns = max(1, int(max_turns))
        self._max_tokens = max(0, int(max_tokens))
        self._context_max_chars = max(1000, int(context_max_chars))
        self._tool_result_max_chars = max(100, int(tool_result_max_chars))
        self._scheduled_wake_default_seconds = max(1.0, float(scheduled_wake_default_seconds))
        self._stewardship_interval_seconds = max(0.0, float(stewardship_interval_seconds))
        self._directed_messages_enabled = directed_messages_enabled
        self._enqueue: EnqueueResidentTask | None = None
        self._inflight_cases: set[str] = set()
        self._inflight_refs: set[str] = set()

    @property
    def state(self) -> ResidentStatePort:
        return self._state

    @property
    def resident_id(self) -> str:
        return self._resident_id

    @property
    def charter(self) -> str:
        return self._charter

    def bind_enqueue(self, enqueue: EnqueueResidentTask) -> None:
        self._enqueue = enqueue

    async def prepare_context(self, task: AgentTask) -> str:
        """Present the resident's exact prior working state to a new model turn."""
        telemetry = get_observability()
        attributes = {
            "ravn.task.id": task.task_id,
            "ravn.resident.id": self._resident_id,
        }
        with telemetry.span(
            "ravn.port.resident_state.read_working_state",
            attributes=attributes,
        ) as span:
            prior = await self._state.read_working_state(self._resident_id)
            span.set_attribute("ravn.resident.working_state.available", prior is not None)
            if prior is not None:
                span.set_attribute("ravn.resident.working_state.ref", prior.path)
        telemetry.count(
            "ravn.resident.working_context",
            attributes={
                "ravn.resident.id": self._resident_id,
                "ravn.resident.working_state.available": prior is not None,
            },
            description="Resident turns prepared with or without prior working state.",
        )
        if prior is None:
            state_block = "(No prior resident working state exists yet.)"
            state_ref = "none"
        else:
            state_block = _bounded(prior.content, self._context_max_chars)
            state_ref = prior.path
            telemetry.event(
                "ravn.resident.working_state_recalled",
                attributes={
                    "ravn.resident.id": self._resident_id,
                    "ravn.resident.working_state.ref": state_ref,
                },
                content=prior.content,
            )
        a2a_tasks = await self.find_a2a_tasks(
            case_id=task.resident_case_id,
            active_only=True,
            limit=10,
        )
        a2a_block = (
            json.dumps(a2a_tasks, indent=2, sort_keys=True)
            if a2a_tasks
            else "(No active durable A2A task handles.)"
        )
        return (
            f"{task.initiative_context.rstrip()}\n\n"
            "## Durable resident working state\n\n"
            f"Reference: `{state_ref}`\n\n"
            f"{state_block}\n\n"
            "## Durable A2A task handles\n\n"
            f"{a2a_block}\n\n"
            f"{_WORKING_STATE_PROTOCOL}\n"
        )

    def track_task(self, task: AgentTask) -> None:
        """Suppress duplicate home/resume work while a durable task is queued or running."""
        if task.resident_case_id:
            self._inflight_cases.add(task.resident_case_id)
        self._inflight_refs.update(task.resident_inbox_refs)

    async def capture_directed_message(
        self,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        """Persist a human message so a failed immediate turn cannot lose it."""
        if not self._directed_messages_enabled or self._inbox is None:
            return ""
        telemetry = get_observability()
        with telemetry.span(
            "ravn.port.resident_inbox.write_directed_message",
            attributes={"ravn.resident.id": self._resident_id},
        ) as span:
            ref = await self._inbox.write_directed_message(
                content=content,
                metadata=metadata,
            )
            span.set_attribute("ravn.resident.inbox_ref", ref)
        telemetry.count(
            "ravn.resident.directed_messages.persisted",
            attributes={"ravn.resident.id": self._resident_id},
        )
        return ref

    async def record_a2a_activity(self, activity: Mapping[str, object]) -> str:
        """Persist the continuation handle observed by the A2A tool."""
        task_id = str(activity.get("task_id") or "").strip()
        write = getattr(self._state, "write_a2a_task", None)
        if not task_id or write is None:
            return ""
        existing = await self._read_a2a_record(task_id)

        def value(key: str, fallback: str = "") -> str:
            current = str(activity.get(key) or "").strip()
            prior = str(getattr(existing, key, "") or "").strip() if existing else ""
            return current or prior or fallback

        push_registered = activity.get("push_registered")
        if not isinstance(push_registered, bool):
            push_registered = existing.push_registered if existing else None
        record = ResidentA2ATaskRecord(
            task_id=task_id,
            agent_id=value("agent_id"),
            skill_id=value("skill_id"),
            state=value("state", "TASK_STATE_UNSPECIFIED"),
            operation=value("operation"),
            prompt=value("prompt"),
            status_message=value("status_message"),
            question=value("question"),
            case_id=value("case_id"),
            root_correlation_id=value("root_correlation_id"),
            parent_task_id=value("parent_task_id"),
            mandate=value("mandate"),
            turn_index=int(activity.get("turn_index") or (existing.turn_index if existing else 0)),
            case_input_tokens=int(
                activity.get("case_input_tokens") or (existing.case_input_tokens if existing else 0)
            ),
            case_output_tokens=int(
                activity.get("case_output_tokens")
                or (existing.case_output_tokens if existing else 0)
            ),
            case_started_at=value("case_started_at"),
            push_registered=push_registered,
            update_fingerprint=value("update_fingerprint"),
        )
        return await write(record)

    async def find_a2a_tasks(
        self,
        *,
        query: str = "",
        case_id: str = "",
        active_only: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find durable A2A handles, with bounded legacy-memory recovery."""
        list_tasks = getattr(self._state, "list_a2a_tasks", None)
        entries = await list_tasks() if list_tasks is not None else []
        records = [record for entry in entries if (record := _parse_a2a_task(entry.content))]
        needle = query.strip().casefold()

        def matches(record: ResidentA2ATaskRecord) -> bool:
            if active_only and record.state in _A2A_TERMINAL_STATES:
                return False
            if case_id and record.case_id not in {"", case_id}:
                return False
            if not needle:
                return True
            haystack = " ".join(
                (
                    record.task_id,
                    record.agent_id,
                    record.skill_id,
                    record.prompt,
                    record.status_message,
                    record.case_id,
                )
            ).casefold()
            return needle in haystack

        found = [record for record in records if matches(record)]
        found.sort(key=lambda record: record.updated_at, reverse=True)
        rendered = [_a2a_record_payload(record) for record in found[: max(1, limit)]]
        if rendered or not needle:
            return rendered

        # Older turns predate the registry. Existing state search remains the
        # recovery path; only identifier envelopes are projected back out.
        for entry in await self._state.recall(query, limit=max(5, limit)):
            rendered.extend(_legacy_a2a_handles(entry, query=query))
            if len(rendered) >= limit:
                break
        return rendered[: max(1, limit)]

    async def _read_a2a_record(self, task_id: str) -> ResidentA2ATaskRecord | None:
        read = getattr(self._state, "read_a2a_task", None)
        if read is None:
            return None
        entry = await read(task_id)
        return _parse_a2a_task(entry.content) if entry is not None else None

    async def submit_a2a_push(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist an A2A task update and immediately wake the resident when idle."""
        if self._inbox is None:
            raise RuntimeError("Resident inbox is not configured")
        event = next(
            (
                payload.get(key)
                for key in ("task", "statusUpdate", "artifactUpdate")
                if isinstance(payload.get(key), dict)
            ),
            None,
        )
        if not isinstance(event, dict):
            raise ValueError("A2A callback must contain task, statusUpdate, or artifactUpdate")
        task_id = str(event.get("id") or event.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("A2A callback does not identify a task")
        status = event.get("status")
        status = status if isinstance(status, dict) else {}
        state = str(status.get("state") or event.get("state") or "TASK_STATE_UNSPECIFIED")
        now = datetime.now(UTC)
        normalized = json.loads(json.dumps(payload, sort_keys=True, default=str))
        existing = await self._read_a2a_record(task_id)
        semantic_update = {
            "task_id": task_id,
            "state": state,
            "status_message": status.get("message") or status.get("update") or "",
            "artifact": payload.get("artifactUpdate") or event.get("artifact") or {},
        }
        digest = hashlib.sha256(
            json.dumps(semantic_update, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        if existing is not None and existing.update_fingerprint == digest:
            return {
                "task_id": task_id,
                "state": state,
                "inbox_ref": "",
                "queued": False,
                "duplicate": True,
            }
        activity: dict[str, object] = {
            "task_id": task_id,
            "state": state,
            "operation": "push",
            "status_message": str(semantic_update["status_message"]),
            "update_fingerprint": digest,
        }
        if existing is not None:
            activity.update(_a2a_record_payload(existing))
            activity.update(
                {
                    "state": state,
                    "operation": "push",
                    "status_message": str(semantic_update["status_message"]),
                    "update_fingerprint": digest,
                }
            )
            normalized["_resident"] = {
                "case_id": existing.case_id,
                "root_correlation_id": existing.root_correlation_id,
                "turn_index": existing.turn_index,
                "mandate": existing.mandate,
                "case_input_tokens": existing.case_input_tokens,
                "case_output_tokens": existing.case_output_tokens,
                "case_started_at": existing.case_started_at,
            }
        await self.record_a2a_activity(activity)
        signal = ResidentInboxSignal(
            id=f"a2a-{task_id}-{digest}",
            source="a2a:push",
            kind="a2a.task_update",
            summary=f"A2A task {task_id} changed to {state}.",
            payload=normalized,
            classification=ResidentInboxClassification.STATUS_UPDATE.value,
            confidence=1.0,
            status=ResidentInboxStatus.NEW.value,
            observed_at=now.isoformat(),
        )
        inbox_ref = await self._inbox.write_signal(signal)
        task = await self.next_home_task(
            limit=1,
            persona=None,
            output_mode=OutputMode.AMBIENT,
        )
        queued = bool(task and await self._enqueue_task(task))
        if task is not None and not queued:
            self.release_failed_task(task)
        return {
            "task_id": task_id,
            "state": state,
            "inbox_ref": inbox_ref,
            "queued": queued,
            "duplicate": False,
        }

    async def handle_completed_turn(
        self,
        *,
        task: AgentTask,
        prompt: str,
        result: TurnResult,
        response_text: str,
        outcome_fields: Mapping[str, Any] | None = None,
        outcome_valid: bool | None = None,
    ) -> ResidentTurnDisposition:
        telemetry = get_observability()
        attributes = {
            "ravn.task.id": task.task_id,
            "ravn.resident.case_id": task.resident_case_id
            or task.root_correlation_id
            or task.task_id,
            "ravn.resident.turn": task.resident_turn_index or 1,
        }
        with telemetry.span("ravn.resident.complete_turn", attributes=attributes) as span:
            disposition = await self._handle_completed_turn_observed(
                task=task,
                prompt=prompt,
                result=result,
                response_text=response_text,
                outcome_fields=outcome_fields,
                outcome_valid=outcome_valid,
            )
            kind = str(disposition.kind)
            span.set_attribute("ravn.resident.disposition", kind)
            telemetry.count(
                "ravn.resident.turns",
                attributes={"ravn.resident.disposition": kind},
                description="Completed durable resident turns by disposition.",
            )
            return disposition

    async def _handle_completed_turn_observed(
        self,
        *,
        task: AgentTask,
        prompt: str,
        result: TurnResult,
        response_text: str,
        outcome_fields: Mapping[str, Any] | None,
        outcome_valid: bool | None,
    ) -> ResidentTurnDisposition:
        """Persist one turn, acknowledge its observations, then decide transport."""
        telemetry = get_observability()
        case_id = task.resident_case_id or task.root_correlation_id or task.task_id
        root_id = task.root_correlation_id or case_id
        turn_index = task.resident_turn_index or 1
        mandate = task.resident_mandate or task.initiative_context
        episode = getattr(result, "episode", None)
        fields = dict(
            outcome_fields
            if outcome_fields is not None
            else getattr(episode, "structured_outcome", None) or {}
        )
        if outcome_valid is None:
            outcome_valid = getattr(episode, "outcome_valid", None) is not False
        action = selected_action_from_outcome(fields)
        telemetry.event(
            "ravn.resident.outcome_interpreted",
            attributes={
                "ravn.resident.case_id": case_id,
                "ravn.resident.continuation": str(fields.get("continuation") or ""),
                "ravn.resident.has_selected_action": action is not None,
            },
            content=fields,
        )
        evidence_refs = _evidence_refs(fields, task)
        cumulative = TokenUsage(
            input_tokens=task.resident_input_tokens + result.usage.input_tokens,
            output_tokens=task.resident_output_tokens + result.usage.output_tokens,
        )
        record = ResidentTurnRecord(
            turn_index=turn_index,
            mandate=mandate,
            prompt=prompt,
            response=response_text or result.response,
            outcome_fields=fields,
            tool_names=tuple(dict.fromkeys(call.name for call in result.tool_calls)),
            tool_results=_tool_result_summaries(
                result,
                max_chars=self._tool_result_max_chars,
            ),
            usage=result.usage,
            cumulative_usage=cumulative,
            selected_next_action=action,
            case_id=case_id,
            root_correlation_id=root_id,
            task_id=task.task_id,
            triggered_by=task.triggered_by,
            persona=task.persona or "",
            evidence_refs=evidence_refs,
            inbox_refs=tuple(task.resident_inbox_refs),
        )
        with telemetry.span("ravn.port.resident_state.write_turn") as state_span:
            turn_ref = await self._state.write_turn(record)
            state_span.set_attribute("ravn.resident.turn_ref", turn_ref)
        with telemetry.span("ravn.port.resident_state.read_turn"):
            durable_turn = await self._state.read(turn_ref)
        if durable_turn is None:
            raise RuntimeError(f"resident turn was not readable after write: {turn_ref}")

        working_state = resident_working_state_from_outcome(fields) if outcome_valid else None
        if outcome_valid and "working_state" in fields and working_state is None:
            working_state_errors = validate_resident_working_state(fields.get("working_state"))
            telemetry.event(
                "ravn.resident.working_state_rejected",
                attributes={
                    "ravn.resident.id": self._resident_id,
                    "ravn.resident.working_state.error_count": len(working_state_errors),
                },
                content={"errors": working_state_errors},
            )
        if working_state is not None:
            with telemetry.span(
                "ravn.port.resident_state.write_working_state",
                attributes={"ravn.resident.id": self._resident_id},
            ) as state_span:
                working_state_ref = await self._state.write_working_state(
                    ResidentWorkingStateRecord(
                        resident_id=self._resident_id,
                        state=working_state,
                        source_turn_ref=turn_ref,
                        source_case_id=case_id,
                        source_task_id=task.task_id,
                        signal_refs=_string_refs(fields.get("signal_refs")),
                        evidence_refs=evidence_refs,
                    )
                )
                state_span.set_attribute(
                    "ravn.resident.working_state.ref",
                    working_state_ref,
                )
                durable_working_state = await self._state.read_working_state(self._resident_id)
                if durable_working_state is None:
                    raise RuntimeError(
                        f"resident working state was not readable after write: {working_state_ref}"
                    )
                telemetry.event(
                    "ravn.resident.working_state_updated",
                    attributes={
                        "ravn.resident.id": self._resident_id,
                        "ravn.resident.working_state.ref": working_state_ref,
                        "ravn.resident.source_turn_ref": turn_ref,
                    },
                    content=working_state,
                )
                telemetry.count(
                    "ravn.resident.working_state_updates",
                    attributes={"ravn.resident.id": self._resident_id},
                )

        snapshot = ResidentBudgetSnapshot(
            turns_used=turn_index,
            elapsed_seconds=_elapsed(task.resident_started_at or task.created_at.isoformat()),
            usage=cumulative,
            case_id=case_id,
            root_correlation_id=root_id,
        )
        with telemetry.span("ravn.port.resident_state.write_budget") as budget_span:
            budget_ref = await self._state.write_budget(snapshot)
            budget_span.set_attribute("ravn.resident.budget_ref", budget_ref)

        # The wake is a delivery marker, not evidence. Once the resumed turn and
        # its budget are durable, that wake has been handled even when the model
        # misses the outcome schema. Executor crashes never reach this point, so
        # their wake remains pending for recovery.
        if task.resident_wake_ref:
            wake = await self._state.read(task.resident_wake_ref)
            if wake is not None and wake.path == task.resident_wake_ref:
                with telemetry.span(
                    "ravn.port.resident_state.consume_scheduled_wake",
                    attributes={
                        "ravn.resident.case_id": case_id,
                        "ravn.resident.wake_ref": task.resident_wake_ref,
                    },
                ):
                    await self._state.consume_scheduled_wake(wake)

        if not outcome_valid:
            # The turn and budget records are durable, so this attempt really
            # happened. Count it: a slot no turn can validly judge must stop
            # being retried forever and become visible to a human instead.
            await self._record_invalid_outcome(task, case_id=case_id, turn_index=turn_index)
            self._inflight_cases.discard(case_id)
            self._inflight_refs.difference_update(task.resident_inbox_refs)
            return ResidentTurnDisposition(
                kind=ContinuationDecisionKind.STOP,
                case_id=case_id,
                turn_ref=turn_ref,
                budget_ref=budget_ref,
                reason="resident outcome contract was invalid",
            )

        # A signal is no longer NEW only after both durable turn and budget
        # records exist.  Replayed signals therefore cannot disappear on a
        # failed model turn or failed state write.
        if self._inbox is not None and task.resident_inbox_refs:
            with telemetry.span(
                "ravn.port.resident_inbox.acknowledge",
                attributes={"ravn.resident.inbox_ref_count": len(task.resident_inbox_refs)},
            ):
                await self._inbox.acknowledge(
                    tuple(task.resident_inbox_refs),
                    status=ResidentInboxStatus.REMEMBERED.value,
                    reason=f"recorded by resident case {case_id} turn {turn_index}",
                    expected=dict(task.resident_inbox_expected),
                )
        if task.resident_answer_ref:
            answer = await self._state.read_operator_answer(case_id)
            if answer is not None and answer.path == task.resident_answer_ref:
                await self._state.consume_operator_answer(answer)

        self._inflight_cases.discard(case_id)
        self._inflight_refs.difference_update(task.resident_inbox_refs)
        control = str(fields.get("continuation") or "").strip().casefold()
        if control == "sleep":
            timing = str(fields.get("next_action_timing") or "").strip().casefold()
            if timing == "scheduled_time":
                budget_reason = _budget_stop_reason(
                    turn_index=turn_index,
                    total_tokens=cumulative.total_tokens,
                    max_turns=self._max_turns,
                    max_tokens=self._max_tokens,
                )
                if budget_reason:
                    return ResidentTurnDisposition(
                        kind=ContinuationDecisionKind.STOP,
                        case_id=case_id,
                        turn_ref=turn_ref,
                        budget_ref=budget_ref,
                        reason=budget_reason,
                    )
            wake_ref, wake_at = await self._schedule_wake(
                task=task,
                fields=fields,
                case_id=case_id,
                root_id=root_id,
                turn_ref=turn_ref,
                turn_index=turn_index,
                mandate=mandate,
                cumulative=cumulative,
            )
            return ResidentTurnDisposition(
                kind=ContinuationDecisionKind.SLEEP,
                case_id=case_id,
                turn_ref=turn_ref,
                budget_ref=budget_ref,
                reason=(
                    f"model selected sleep until {wake_at}"
                    if wake_at
                    else "model selected sleep pending an external event"
                ),
                wake_ref=wake_ref,
                wake_at=wake_at,
            )

        if control == "stop":
            return ResidentTurnDisposition(
                kind=ContinuationDecisionKind.STOP,
                case_id=case_id,
                turn_ref=turn_ref,
                budget_ref=budget_ref,
                reason="model selected stop",
            )

        if _asks_operator(fields, control):
            budget_reason = _budget_stop_reason(
                turn_index=turn_index,
                total_tokens=cumulative.total_tokens,
                max_turns=self._max_turns,
                max_tokens=self._max_tokens,
            )
            if budget_reason:
                return ResidentTurnDisposition(
                    kind=ContinuationDecisionKind.STOP,
                    case_id=case_id,
                    turn_ref=turn_ref,
                    budget_ref=budget_ref,
                    reason=budget_reason,
                )
            return await self._ask_operator(
                task=task,
                record=record,
                fields=fields,
                turn_ref=turn_ref,
                budget_ref=budget_ref,
                reason=str(fields.get("reason") or "operator input required"),
            )

        if control == "continue":
            return ResidentTurnDisposition(
                kind=ContinuationDecisionKind.STOP,
                case_id=case_id,
                turn_ref=turn_ref,
                budget_ref=budget_ref,
                reason=(
                    "free-text immediate continuation is unsupported; execute available "
                    "tools in the current turn or wait for a real wake source"
                ),
            )

        return ResidentTurnDisposition(
            kind=ContinuationDecisionKind.STOP,
            case_id=case_id,
            turn_ref=turn_ref,
            budget_ref=budget_ref,
            reason=(
                "selected next action recorded without a wake request"
                if action is not None
                else "no selected next action"
            ),
        )

    async def _schedule_wake(
        self,
        *,
        task: AgentTask,
        fields: Mapping[str, Any],
        case_id: str,
        root_id: str,
        turn_ref: str,
        turn_index: int,
        mandate: str,
        cumulative: TokenUsage,
    ) -> tuple[str, str]:
        """Persist a durable wake when a sleeping turn expects time to pass.

        A turn sleeping on ``external_event`` already has a wake source: the next
        observation. Only ``scheduled_time`` needs the runtime to remember it.
        """
        timing = str(fields.get("next_action_timing") or "").strip().casefold()
        if timing != "scheduled_time":
            return "", ""
        telemetry = get_observability()
        now = datetime.now(UTC)
        wake_at, fallback_reason = self._resolve_wake_at(fields.get("wake_at"), now)
        if fallback_reason:
            telemetry.event(
                "ravn.resident.scheduled_wake_defaulted",
                attributes={
                    "ravn.resident.case_id": case_id,
                    "ravn.resident.wake_fallback_reason": fallback_reason,
                },
                content={"wake_at": str(fields.get("wake_at") or "")},
            )
        with telemetry.span(
            "ravn.port.resident_state.write_scheduled_wake",
            attributes={"ravn.resident.case_id": case_id},
        ) as span:
            wake_ref = await self._state.write_scheduled_wake(
                ResidentScheduledWakeRecord(
                    case_id=case_id,
                    root_correlation_id=root_id,
                    wake_at=wake_at,
                    reason=str(
                        fields.get("selected_next_action")
                        or fields.get("recommended_action")
                        or "scheduled recheck"
                    ),
                    mandate=mandate,
                    turn_index=turn_index,
                    turn_ref=turn_ref,
                    persona=task.persona or "",
                    task_id=task.task_id,
                    case_input_tokens=cumulative.input_tokens,
                    case_output_tokens=cumulative.output_tokens,
                    case_started_at=task.resident_started_at or task.created_at.isoformat(),
                )
            )
            span.set_attribute("ravn.resident.wake_ref", wake_ref)
        telemetry.count(
            "ravn.resident.scheduled_wakes",
            attributes={"ravn.resident.id": self._resident_id},
            description="Durable resident wakes scheduled for a future time.",
        )
        return wake_ref, wake_at.isoformat()

    def _resolve_wake_at(self, raw: Any, now: datetime) -> tuple[datetime, str]:
        """Resolve the requested wake time, falling back to the configured delay."""
        default = now + timedelta(seconds=self._scheduled_wake_default_seconds)
        text = str(raw or "").strip()
        if not text:
            return default, "no wake_at supplied"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return default, "wake_at was not a valid ISO timestamp"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed <= now:
            return default, "wake_at was already in the past"
        return parsed, ""

    async def resume_due_wakes(self) -> int:
        """Re-enqueue durable cases whose scheduled wake time has arrived."""
        telemetry = get_observability()
        now = datetime.now(UTC)
        queued = 0
        with telemetry.span(
            "ravn.port.resident_state.list_scheduled_wakes",
            attributes={"ravn.resident.id": self._resident_id},
        ) as span:
            wakes = await self._state.list_scheduled_wakes()
            span.set_attribute("ravn.resident.scheduled_wake.pending_count", len(wakes))
        telemetry.gauge(
            "ravn.resident.scheduled_wakes.pending",
            len(wakes),
            attributes={"ravn.resident.id": self._resident_id},
            description="Durable scheduled resident wakes still pending.",
        )
        for wake in wakes:
            case_id = _metadata(wake.content, "case_id") or _case_from_path(wake.path)
            if not case_id or case_id in self._inflight_cases:
                continue
            wake_at = _scheduled_wake_at(wake.content)
            if wake_at is None:
                # A wake with no readable time can never become due; consume it so
                # it cannot accumulate as permanent invisible backlog.
                telemetry.event(
                    "ravn.resident.scheduled_wake_unreadable",
                    attributes={
                        "ravn.resident.case_id": case_id,
                        "ravn.resident.wake_ref": wake.path,
                    },
                )
                with telemetry.span(
                    "ravn.port.resident_state.consume_scheduled_wake",
                    attributes={"ravn.resident.wake_ref": wake.path},
                ):
                    await self._state.consume_scheduled_wake(wake)
                continue
            if wake_at > now:
                continue
            task = _scheduled_wake_task(
                wake,
                wake_at=wake_at,
                prior_turn_handoff=await self._read_parent_handoff(wake),
            )
            accepted = await self._enqueue_task(task)
            telemetry.event(
                "ravn.resident.scheduled_wake_resumed",
                attributes={
                    "ravn.resident.case_id": case_id,
                    "ravn.resident.wake_ref": wake.path,
                    "ravn.resident.wake_queued": accepted,
                    "ravn.resident.wake_lag_seconds": max(
                        0.0,
                        (now - wake_at).total_seconds(),
                    ),
                },
            )
            if not accepted:
                continue
            self._inflight_cases.add(case_id)
            telemetry.count(
                "ravn.resident.scheduled_wake_resumptions",
                attributes={"ravn.resident.id": self._resident_id},
                description="Due scheduled resident wakes accepted by the task queue.",
            )
            queued += 1
        return queued

    async def _ask_operator(
        self,
        *,
        task: AgentTask,
        record: ResidentTurnRecord,
        fields: dict[str, Any],
        turn_ref: str,
        budget_ref: str,
        reason: str,
    ) -> ResidentTurnDisposition:
        question = _operator_question(fields, record)
        telemetry = get_observability()
        with telemetry.span("ravn.port.resident_state.write_operator_needed") as span:
            operator_ref = await self._state.write_operator_needed(
                question=question,
                reason=reason,
                turn=record,
                case_id=record.case_id,
                turn_ref=turn_ref,
            )
            span.set_attribute("ravn.resident.operator_ref", operator_ref)
            telemetry.event(
                "ravn.resident.operator_question",
                attributes={"ravn.resident.case_id": record.case_id},
                content={"question": question, "reason": reason},
            )
        task.resident_case_id = record.case_id
        return ResidentTurnDisposition(
            kind=ContinuationDecisionKind.ASK_OPERATOR,
            case_id=record.case_id,
            turn_ref=turn_ref,
            budget_ref=budget_ref,
            reason=reason,
            operator_ref=operator_ref,
            question=question,
        )

    async def pending_questions(self) -> list[dict[str, str]]:
        return [_pending_view(item) for item in await self._state.list_operator_needed()]

    async def submit_operator_answer(
        self,
        *,
        case_id: str,
        answer: str,
        trace_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        case_id = case_id.strip()
        answer = answer.strip()
        if not case_id or not answer:
            raise ValueError("case_id and answer are required")
        telemetry = get_observability()
        with telemetry.span(
            "ravn.resident.operator_answer.receive",
            attributes={"ravn.resident.case_id": case_id},
            carrier=trace_context,
        ):
            pending = await self._state.read_operator_needed(case_id)
            if pending is None:
                raise LookupError(f"no pending operator question for case {case_id}")
            prior_turn_handoff = await self._read_parent_handoff(pending)
            answer_ref = await self._state.write_operator_answer(answer, case_id=case_id)
            task = _operator_resume_task(
                pending,
                answer=answer,
                answer_ref=answer_ref,
                prior_turn_handoff=prior_turn_handoff,
            )
            task.trace_context = telemetry.inject() or dict(trace_context or {})
            accepted = await self._enqueue_task(task)
            if accepted:
                self._inflight_cases.add(case_id)
            telemetry.set_attributes(
                {
                    "ravn.resident.answer_ref": answer_ref,
                    "ravn.resident.answer_queued": accepted,
                }
            )
            return {
                "case_id": case_id,
                "answer_ref": answer_ref,
                "continuation_task_id": task.task_id if accepted else "",
                "queued": accepted,
            }

    async def consume_directed_message(
        self,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> bool:
        metadata = metadata or {}
        context = metadata.get("help_context")
        if not isinstance(context, dict):
            context = {}
        case_id = str(
            metadata.get("resident_case_id")
            or metadata.get("case_id")
            or context.get("resident_case_id")
            or context.get("case_id")
            or ""
        ).strip()
        if not case_id:
            pending = await self._state.list_operator_needed()
            if len(pending) != 1:
                return False
            case_id = _metadata(pending[0].content, "case_id")
        if not case_id or await self._state.read_operator_needed(case_id) is None:
            return False
        trace_context = metadata.get("trace_context")
        if not isinstance(trace_context, dict):
            trace_context = {}
        await self.submit_operator_answer(
            case_id=case_id,
            answer=content,
            trace_context=trace_context,
        )
        return True

    async def _record_invalid_outcome(
        self,
        task: AgentTask,
        *,
        case_id: str,
        turn_index: int,
    ) -> None:
        """Count an invalid outcome against the observations it failed to judge."""
        if self._inbox is None or not task.resident_inbox_refs:
            return
        telemetry = get_observability()
        blocked = await self._inbox.record_failed_attempt(
            tuple(task.resident_inbox_refs),
            reason=(
                f"blocked after repeated invalid resident outcomes; "
                f"last case {case_id} turn {turn_index}"
            ),
        )
        telemetry.count(
            "ravn.resident.inbox_invalid_outcomes",
            attributes={"ravn.resident.id": self._resident_id},
        )
        if not blocked:
            return
        # Escalate once: a metric alone leaves nobody looking at work that can
        # never be judged.
        telemetry.event(
            "ravn.resident.inbox_blocked",
            attributes={
                "ravn.resident.id": self._resident_id,
                "ravn.resident.case_id": case_id,
                "ravn.resident.blocked_ref_count": len(blocked),
            },
            content={"refs": list(blocked)},
        )
        logger.error(
            "resident inbox: %d observation slot(s) blocked after repeated invalid "
            "outcomes and need operator review: %s",
            len(blocked),
            ", ".join(blocked),
        )

    async def next_home_task(
        self,
        *,
        limit: int,
        persona: str | None,
        output_mode: OutputMode,
    ) -> AgentTask | None:
        if self._inbox is None:
            return None
        if any(case_id.startswith("resident-home-") for case_id in self._inflight_cases):
            return None
        rows = await self._inbox.list_signals(
            status=ResidentInboxStatus.NEW.value,
            limit=max(1, limit),
        )
        rows = [(ref, signal) for ref, signal in rows if ref not in self._inflight_inbox_refs()]
        if not rows:
            return None
        origin = _a2a_signal_origin(rows[0][1])
        if origin.get("case_id"):
            rows = rows[:1]
        refs = [ref for ref, _signal in rows]
        digest = hashlib.sha256("\n".join(sorted(refs)).encode()).hexdigest()[:16]
        case_id = str(origin.get("case_id") or f"resident-home-{digest}")
        if case_id in self._inflight_cases:
            return None
        now = datetime.now(UTC)
        context_lines = [
            "Resident home turn over durable, unconsumed observations.",
            "Judge what these observations mean. Retrieve full evidence only when it can "
            "improve the judgment. Investigate, research, collaborate, ask the operator, "
            "act, schedule a recheck, or stop as appropriate.",
            "",
        ]
        if self._resident_personality or self._charter:
            context_lines.append("Configured resident context:")
            if self._resident_personality:
                context_lines.append(f"- Personality: {self._resident_personality}")
            if self._charter:
                context_lines.append(f"- Charter: {self._charter}")
            context_lines.append("")
        context_lines.append("Observations:")
        payload_budget = max(
            200,
            min(4000, self._context_max_chars // (2 * max(1, len(rows)))),
        )
        for ref, signal in rows:
            evidence = signal.raw_ref or ", ".join(signal.evidence_refs) or "none"
            context_lines.append(
                f"- inbox_ref={ref}; source={signal.source}; kind={signal.kind}; "
                f"observed_at={signal.observed_at}; evidence_ref={evidence}; "
                f"summary={compact_line(signal.summary, limit=280)}"
            )
            context_lines.extend(_coalesced_evidence_lines(signal))
            raw_payload = signal.payload.get("payload", signal.payload)
            context_lines.extend(
                (
                    "  Bounded raw payload:",
                    "  ```json",
                    _indent(
                        _bounded(
                            json.dumps(
                                raw_payload,
                                indent=2,
                                sort_keys=True,
                                ensure_ascii=False,
                                default=str,
                            ),
                            payload_budget,
                        ),
                        "  ",
                    ),
                    "  ```",
                )
            )
            context_lines.extend(_extreme_payload_lines(signal, payload_budget))
        task = AgentTask(
            task_id=_task_id("resident_home"),
            title=f"Resident home turn ({len(rows)} observations)",
            initiative_context="\n".join(context_lines),
            triggered_by="resident:home",
            output_mode=output_mode,
            persona=persona,
            root_correlation_id=str(origin.get("root_correlation_id") or case_id),
            resident_case_id=case_id,
            resident_turn_index=int(origin.get("turn_index") or 0) + 1,
            resident_started_at=str(origin.get("case_started_at") or now.isoformat()),
            resident_inbox_refs=refs,
            resident_inbox_expected={
                ref: signal.last_archive_ref for ref, signal in rows if signal.last_archive_ref
            },
            trace_context=next(
                (dict(signal.trace_context) for _ref, signal in rows if signal.trace_context),
                {},
            ),
        )
        task.resident_mandate = str(origin.get("mandate") or task.initiative_context)
        task.resident_input_tokens = int(origin.get("case_input_tokens") or 0)
        task.resident_output_tokens = int(origin.get("case_output_tokens") or 0)
        self._inflight_cases.add(case_id)
        self._inflight_refs.update(refs)
        return task

    async def next_stewardship_task(
        self,
        *,
        persona: str | None,
        output_mode: OutputMode,
    ) -> AgentTask | None:
        """Ask the resident to reconsider its environment when nothing woke it.

        Every other wake path requires an inbound observation, so a quiet
        environment produces no turns at all. This is the charter-driven wake: it
        runs only when the resident has been idle longer than the configured
        interval, and only when the deployment opts in.
        """
        if self._stewardship_interval_seconds <= 0:
            return None
        if self._inflight_cases:
            # Any queued or running case means the resident is not idle. Waiting
            # also keeps a stewardship turn from racing the case that is about to
            # refresh the working state this turn reads.
            return None
        now = datetime.now(UTC)
        quiet_seconds, last_examined = await self._quiet_seconds(now)
        if quiet_seconds < self._stewardship_interval_seconds:
            return None
        telemetry = get_observability()
        case_id = f"resident-stewardship-{now.strftime('%Y%m%dT%H%M%SZ')}"
        context_lines = [
            "Resident stewardship turn. No new observation prompted this; the "
            "environment has simply gone unexamined for a while.",
            "",
            f"Time since the resident last recorded working state: {_duration(quiet_seconds)}.",
            f"Last examined: {last_examined or 'never — this is the first stewardship turn'}.",
            "",
        ]
        if self._resident_personality or self._charter:
            context_lines.append("Configured resident context:")
            if self._resident_personality:
                context_lines.append(f"- Personality: {self._resident_personality}")
            if self._charter:
                context_lines.append(f"- Charter: {self._charter}")
            context_lines.append("")
        context_lines.extend(
            (
                "Judge, against the charter and your durable working state, whether "
                "anything in this environment now deserves attention: an active objective "
                "worth advancing, a stale belief worth re-checking, an unknown worth "
                "resolving, a capability gap worth researching, a risk worth investigating, "
                "or an improvement worth proposing.",
                "",
                "Deciding that nothing warrants action is a correct and expected "
                "outcome. Do not manufacture work to justify this turn. Prefer "
                "`stop` or `sleep` over acting on a hypothesis you cannot ground in "
                "evidence you actually have or can obtain now.",
            )
        )
        task = AgentTask(
            task_id=_task_id("resident_stewardship"),
            title="Resident stewardship turn",
            initiative_context="\n".join(context_lines),
            triggered_by="resident:stewardship",
            output_mode=output_mode,
            persona=persona,
            root_correlation_id=case_id,
            resident_case_id=case_id,
            resident_turn_index=1,
            resident_started_at=now.isoformat(),
        )
        task.resident_mandate = task.initiative_context
        self._inflight_cases.add(case_id)
        telemetry.event(
            "ravn.resident.stewardship_wake",
            attributes={
                "ravn.resident.id": self._resident_id,
                "ravn.resident.case_id": case_id,
                "ravn.resident.quiet_seconds": quiet_seconds,
            },
            content={"last_examined": last_examined},
        )
        return task

    async def _quiet_seconds(self, now: datetime) -> tuple[float, str]:
        """Seconds since the resident last recorded working state.

        The working state is rewritten on every completed turn, so its age is
        already an accurate idle clock — no separate stewardship bookkeeping is
        needed. A resident that has never run is treated as maximally overdue.
        """
        prior = await self._state.read_working_state(self._resident_id)
        if prior is None:
            return float("inf"), ""
        updated_at = _metadata(prior.content, "updated_at")
        try:
            parsed = datetime.fromisoformat(updated_at)
        except ValueError:
            return float("inf"), updated_at
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (now - parsed).total_seconds()), updated_at

    async def retry_unconsumed_answers(self) -> int:
        queued = 0
        for answer in await self._state.list_operator_answers():
            case_id = _metadata(answer.content, "case_id") or _case_from_path(answer.path)
            if not case_id or case_id in self._inflight_cases:
                continue
            pending = await self._state.read_operator_needed(case_id)
            if pending is not None:
                # The pending marker is changed to answered when the answer is
                # written.  A still-pending question has no accepted answer yet.
                continue
            task = _operator_resume_task(
                ResidentMemoryEntry(
                    path=answer.path,
                    summary=answer.summary,
                    content=answer.content,
                ),
                answer=_section(answer.content, "Answer") or _body(answer.content),
                answer_ref=answer.path,
                prior_turn_handoff=await self._read_parent_handoff(answer),
            )
            if await self._enqueue_task(task):
                self._inflight_cases.add(case_id)
                queued += 1
        return queued

    def release_failed_task(self, task: AgentTask) -> None:
        if task.resident_case_id:
            self._inflight_cases.discard(task.resident_case_id)
        self._inflight_refs.difference_update(task.resident_inbox_refs)

    async def _enqueue_task(self, task: AgentTask) -> bool:
        if self._enqueue is None:
            return False
        result = await self._enqueue(task)
        return result is not False

    async def _read_parent_handoff(self, entry: ResidentMemoryEntry) -> str:
        turn_ref = _metadata(entry.content, "turn_ref")
        if not turn_ref:
            return ""
        turn = await self._state.read(turn_ref)
        if turn is None:
            raise RuntimeError(f"resident parent turn is not readable: {turn_ref}")
        return _bounded(_resident_turn_handoff(turn.content), self._context_max_chars)

    def _inflight_inbox_refs(self) -> set[str]:
        return set(self._inflight_refs)


class ResidentHomeTrigger(TriggerPort):
    """Periodic home wake over durable inbox records and resumable answers."""

    def __init__(
        self,
        runtime: ResidentRuntime,
        *,
        interval_seconds: float,
        max_signals: int,
        persona: str | None,
        output_mode: OutputMode,
    ) -> None:
        self._runtime = runtime
        self._interval = max(0.1, float(interval_seconds))
        self._max_signals = max(1, int(max_signals))
        self._persona = persona
        self._output_mode = output_mode

    @property
    def name(self) -> str:
        return "resident:home"

    async def run(self, enqueue: EnqueueResidentTask) -> None:
        # Give queue-journal restoration a chance to run before considering
        # the same durable records after a restart.
        await asyncio.sleep(self._interval)
        while True:
            await self.run_once(enqueue)
            await asyncio.sleep(self._interval)

    async def run_once(self, enqueue: EnqueueResidentTask) -> bool:
        self._runtime.bind_enqueue(enqueue)
        await self._runtime.retry_unconsumed_answers()
        await self._runtime.resume_due_wakes()
        task = await self._runtime.next_home_task(
            limit=self._max_signals,
            persona=self._persona,
            output_mode=self._output_mode,
        )
        if task is None:
            # Nothing observed. The charter is still a standing responsibility, so
            # give the resident a turn to reconsider it when it has gone quiet.
            task = await self._runtime.next_stewardship_task(
                persona=self._persona,
                output_mode=self._output_mode,
            )
        if task is None:
            return False
        accepted = await enqueue(task)
        if accepted is False:
            self._runtime.release_failed_task(task)
            return False
        return True


def _operator_resume_task(
    pending: ResidentMemoryEntry,
    *,
    answer: str,
    answer_ref: str,
    prior_turn_handoff: str = "",
) -> AgentTask:
    case_id = _metadata(pending.content, "case_id") or _case_from_path(pending.path)
    root_id = _metadata(pending.content, "root_correlation_id") or case_id
    prior_turn_index = _int_metadata(pending.content, "turn", 0)
    mandate = _section(pending.content, "Mandate") or pending.content
    question = _metadata(pending.content, "question")
    context = (
        f"Resume resident case {case_id} after operator input.\n"
        f"Pending question: {question}\n"
        f"Operator answer: {answer}\n"
        f"Durable answer reference: {answer_ref}\n\n"
        "## Prior-turn handoff\n\n"
        f"{prior_turn_handoff or '(no relevant prior-turn details)'}\n\n"
        "Treat the answer as a new observation, not as proof of unrelated facts. Continue "
        "the same case and produce the normal structured outcome."
    )
    return AgentTask(
        task_id=_task_id("resident_answer"),
        title=f"Resume resident case {case_id}",
        initiative_context=context,
        triggered_by="resident:operator_answer",
        output_mode=OutputMode.SURFACE,
        persona=_metadata(pending.content, "persona") or None,
        priority=1,
        root_correlation_id=root_id,
        resident_case_id=case_id,
        resident_mandate=mandate,
        resident_turn_index=prior_turn_index + 1,
        resident_input_tokens=_int_metadata(pending.content, "case_input_tokens", 0),
        resident_output_tokens=_int_metadata(pending.content, "case_output_tokens", 0),
        resident_started_at=_metadata(pending.content, "created_at"),
        resident_answer_ref=answer_ref,
    )


def _scheduled_wake_task(
    wake: ResidentMemoryEntry,
    *,
    wake_at: datetime,
    prior_turn_handoff: str = "",
) -> AgentTask:
    case_id = _metadata(wake.content, "case_id") or _case_from_path(wake.path)
    root_id = _metadata(wake.content, "root_correlation_id") or case_id
    prior_turn_index = _int_metadata(wake.content, "turn", 0)
    mandate = _section(wake.content, "Mandate") or wake.content
    reason = _metadata(wake.content, "reason")
    context = (
        f"Resume resident case {case_id}: the scheduled wake time has arrived.\n"
        f"Scheduled for: {wake_at.isoformat()}\n"
        f"Reason recorded when sleeping: {reason or 'scheduled recheck'}\n"
        f"Durable wake reference: {wake.path}\n\n"
        "## Prior-turn handoff\n\n"
        f"{prior_turn_handoff or '(no relevant prior-turn details)'}\n\n"
        "Time passing is not evidence. Re-check what you intended to re-check, revise "
        "the working state against what you actually find, and continue the same case "
        "with the normal structured outcome. Stopping is a valid outcome when the "
        "recheck shows nothing further is warranted."
    )
    return AgentTask(
        task_id=_task_id("resident_wake"),
        title=f"Resume resident case {case_id}",
        initiative_context=context,
        triggered_by="resident:scheduled_wake",
        output_mode=OutputMode.SURFACE,
        persona=_metadata(wake.content, "persona") or None,
        priority=1,
        root_correlation_id=root_id,
        resident_case_id=case_id,
        resident_mandate=mandate,
        resident_turn_index=prior_turn_index + 1,
        resident_input_tokens=_int_metadata(wake.content, "case_input_tokens", 0),
        resident_output_tokens=_int_metadata(wake.content, "case_output_tokens", 0),
        resident_started_at=(
            _metadata(wake.content, "case_started_at") or _metadata(wake.content, "created_at")
        ),
        resident_wake_ref=wake.path,
    )


def _asks_operator(fields: dict[str, Any], control: str) -> bool:
    verdict = str(fields.get("verdict") or "").strip().casefold()
    return control == "ask_operator" or verdict == "help_needed"


def _operator_question(fields: dict[str, Any], record: ResidentTurnRecord) -> str:
    direct = str(fields.get("question") or "").strip()
    if direct:
        return direct
    questions = fields.get("open_questions")
    if isinstance(questions, list):
        for item in questions:
            text = str(item).strip()
            if text:
                return text
    if record.selected_next_action is not None:
        return f"May I proceed with: {record.selected_next_action.action}?"
    return str(fields.get("recommendation") or "What information should I use to continue?")


def _budget_stop_reason(
    *,
    turn_index: int,
    total_tokens: int,
    max_turns: int,
    max_tokens: int,
) -> str:
    if turn_index >= max_turns:
        return f"resident continuation turn budget reached: {max_turns}"
    if max_tokens > 0 and total_tokens >= max_tokens:
        return f"resident continuation token budget reached: {max_tokens}"
    return ""


def _tool_result_summaries(result: TurnResult, *, max_chars: int) -> tuple[str, ...]:
    names = {call.id: call.name for call in result.tool_calls}
    return tuple(
        (
            f"### {names.get(item.tool_call_id, item.tool_call_id or 'tool')} "
            f"({'error' if item.is_error else 'ok'})\n\n"
            f"{_bounded(item.content, max_chars)}"
        )
        for item in result.tool_results
    )


def _a2a_record_payload(record: ResidentA2ATaskRecord) -> dict[str, Any]:
    return {
        "task_id": record.task_id,
        "agent_id": record.agent_id,
        "skill_id": record.skill_id,
        "state": record.state,
        "operation": record.operation,
        "prompt": record.prompt,
        "status_message": record.status_message,
        "question": record.question,
        "case_id": record.case_id,
        "root_correlation_id": record.root_correlation_id,
        "parent_task_id": record.parent_task_id,
        "mandate": record.mandate,
        "turn_index": record.turn_index,
        "case_input_tokens": record.case_input_tokens,
        "case_output_tokens": record.case_output_tokens,
        "case_started_at": record.case_started_at,
        "push_registered": record.push_registered,
        "update_fingerprint": record.update_fingerprint,
        "updated_at": record.updated_at.isoformat(),
    }


def _legacy_a2a_handles(entry: ResidentMemoryEntry, *, query: str) -> list[dict[str, Any]]:
    terms = {
        term for term in re.findall(r"[a-z0-9][a-z0-9._:-]+", query.casefold()) if len(term) >= 4
    }
    content = entry.content.casefold()
    if terms and not any(term in content for term in terms):
        return []
    task_ids = re.findall(
        r'(?:A2A task\s+|"task_id"\s*:\s*")([A-Za-z0-9][A-Za-z0-9._:-]{7,})',
        entry.content,
    )
    agent_match = re.search(r'"agent_id"\s*:\s*"([^"]+)"', entry.content)
    state_match = re.search(r"TASK_STATE_[A-Z_]+", entry.content)
    return [
        {
            "task_id": task_id,
            "agent_id": agent_match.group(1) if agent_match else "",
            "state": state_match.group(0) if state_match else "TASK_STATE_UNSPECIFIED",
            "source_ref": entry.path,
            "recovered_from_legacy_state": True,
        }
        for task_id in dict.fromkeys(task_ids)
    ]


def _a2a_signal_origin(signal: ResidentInboxSignal) -> dict[str, Any]:
    if signal.source != "a2a:push" or not isinstance(signal.payload, dict):
        return {}
    origin = signal.payload.get("_resident")
    return dict(origin) if isinstance(origin, dict) else {}


def _string_refs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _coalesced_evidence_lines(signal: ResidentInboxSignal) -> list[str]:
    """Say what a coalesced slot actually stands for.

    A slot's payload is only its newest observation.  Presenting that alone
    would hide both how many observations it represents and how far their
    values ranged — the resident would judge one tick and unknowingly
    acknowledge hundreds.  The structural inventory is never summarised away:
    ranges, distinct values and the payloads at each numeric extreme all travel
    with the slot, and the raw archive range names the durable evidence behind
    them.
    """
    if signal.observation_count <= 1:
        return []
    lines = [
        f"  Coalesced: {signal.observation_count} observations of this exact shape, "
        f"{signal.first_observed_at} to {signal.observed_at}",
        f"  Raw archive range: {signal.first_archive_ref}..{signal.last_archive_ref}",
    ]
    lines.extend(aggregate_summary_lines(signal.aggregate))
    return lines


def _extreme_payload_lines(signal: ResidentInboxSignal, budget: int) -> list[str]:
    """Attach the full payload observed at each numeric extreme.

    An excursion inside a large slot is exactly the observation a summary would
    lose, so its whole payload travels rather than just its bound.
    """
    extremes = signal.aggregate.extreme_payloads
    if signal.observation_count <= 1 or not extremes:
        return []
    lines = ["  Payloads at numeric extremes:"]
    for key in sorted(extremes):
        lines.extend(
            (
                f"  {key}:",
                "  ```json",
                _indent(
                    _bounded(
                        json.dumps(
                            extremes[key],
                            indent=2,
                            sort_keys=True,
                            ensure_ascii=False,
                            default=str,
                        ),
                        budget,
                    ),
                    "  ",
                ),
                "  ```",
            )
        )
    return lines


def _bounded(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n… (truncated)"


def _indent(value: str, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" for line in value.splitlines())


def _evidence_refs(fields: dict[str, Any], task: AgentTask) -> tuple[str, ...]:
    refs: list[str] = list(task.resident_inbox_refs)
    for key in ("signal_refs", "evidence_refs", "dissent_refs"):
        value = fields.get(key)
        if isinstance(value, list):
            refs.extend(str(item).strip() for item in value if str(item).strip())
    evidence = fields.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            for key in ("ref", "path", "url", "event_id", "source_id"):
                value = str(item.get(key) or "").strip()
                if value:
                    refs.append(value)
    for fields_by_event in task.tool_outcomes.values():
        for key in ("ref", "path", "url", "artifact_ref", "page_path"):
            value = str(fields_by_event.get(key) or "").strip()
            if value:
                refs.append(value)
    return tuple(dict.fromkeys(refs))


def _pending_view(entry: ResidentMemoryEntry) -> dict[str, str]:
    return {
        "case_id": _metadata(entry.content, "case_id") or _case_from_path(entry.path),
        "question": _metadata(entry.content, "question"),
        "reason": _metadata(entry.content, "reason"),
        "root_correlation_id": _metadata(entry.content, "root_correlation_id"),
        "operator_ref": entry.path,
    }


def _metadata(content: str, key: str) -> str:
    # Horizontal whitespace only: a plain ``\s*`` crosses the newline after an
    # empty value and captures the *next* metadata line's value instead.
    match = re.search(
        rf"^- {re.escape(key)}:[^\S\n]*(.*?)[^\S\n]*$",
        content,
        flags=re.MULTILINE,
    )
    return match.group(1).strip().strip("'\"") if match else ""


def _int_metadata(content: str, key: str, default: int) -> int:
    try:
        return int(_metadata(content, key))
    except ValueError:
        return default


def _section(content: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n+(.*?)(?=^## |\Z)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _resident_turn_handoff(content: str) -> str:
    """Project a durable turn into the non-duplicative context needed to resume it."""
    selected_action = _section(content, "Selected Next Action") or "none"
    tool_results = _section(content, "Tool Results") or "none"
    evidence_refs = _section(content, "Evidence References") or "- none"
    return (
        f"Selected next action: {selected_action}\n\n"
        "### Relevant tool results\n\n"
        f"{tool_results}\n\n"
        "### Evidence references\n\n"
        f"{evidence_refs}"
    )


def _body(content: str) -> str:
    parts = re.split(r"\n\n", content, maxsplit=2)
    return parts[-1].strip() if parts else content.strip()


def _case_from_path(path: str) -> str:
    match = re.search(r"/cases/([^/]+)/", f"/{path.lstrip('/')}")
    return match.group(1) if match else ""


def _task_id(prefix: str) -> str:
    return f"task_{prefix}_{time.time_ns():x}"


def _elapsed(started_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - started).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def _duration(seconds: float) -> str:
    """Render an idle span for the prompt without implying false precision."""
    if seconds == float("inf"):
        return "unknown (no prior working state)"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


__all__ = ["ResidentHomeTrigger", "ResidentRuntime", "ResidentTurnDisposition"]
