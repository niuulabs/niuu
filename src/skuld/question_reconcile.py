"""Close every question the CLI is not asking, using its session file (LEX-1979).

A question can be open in three places: the transport's native-control card, the
broker's pending and restored ``ask_user_question`` lists (replayed to every client
that connects), and the conversation record (an AskUserQuestion tool_use with no
tool_result). The CLI's session file is the truth. For each question still open in
the record or the restored list, and not an open card the transport checks itself:

* its result is in the session file: close it with that answer;
* its tool call is not in the session file at all: close it as never shown;
* its tool call has no result: keep it while the pane shows a live menu (or at broker
  start, before the CLI has drawn anything), otherwise close it as no longer shown.

A close is an ``ask_user_resolved`` frame that goes through the broker's ordinary
event path, so it clears all three places, is logged, and reaches every client.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from niuu.domain.transcript_reducer import TOOL_STARTED_AT, tool_result_ids
from skuld.native_question_index import (
    ANSWERED,
    NEVER_SHOWN,
    QUESTION_TOOL,
    NativeQuestionIndex,
    NativeTranscriptUnavailableError,
    claude_project_dir,
)
from skuld.transports.tmux_interactive import (
    DISMISS_MENU_NOT_SHOWN,
    DISMISS_NEVER_SHOWN,
    DISMISS_RESOLVED_IN_TERMINAL,
    MENU_GONE,
)

logger = logging.getLogger(__name__)

#: ``dismiss_trigger`` values of a reconcile: at broker start, a sweep, an owner's call.
RECONCILE_START = "reconcile_start"
RECONCILE_SWEEP = "reconcile_sweep"
RECONCILE_OWNER = "reconcile_owner"

_DETAILS = {
    DISMISS_RESOLVED_IN_TERMINAL: "answered in the terminal",
    DISMISS_NEVER_SHOWN: "the CLI never showed it (its call is not in the session file)",
    DISMISS_MENU_NOT_SHOWN: "the CLI is no longer showing it",
}


class NativeQuestionsUnsupportedError(RuntimeError):
    """This session's transport keeps no native session file to reconcile against."""


class QuestionReconcileMixin:
    """Broker side of LEX-1979; mixed into :class:`skuld.broker.Broker`."""

    _question_index: NativeQuestionIndex | None = None
    _record_answered: frozenset[str] | set[str] = frozenset()
    _question_reconcile_task: asyncio.Task[None] | None = None

    @property
    def question_reconcile_counts(self) -> Counter[str]:
        counts = getattr(self, "_question_reconcile_counter", None)
        if counts is None:
            counts = self._question_reconcile_counter = Counter()
        return counts

    def _open_record_questions(self, *, settled_s: float = 0.0) -> dict[str, str]:
        """``{tool_use_id: request_id}`` for every question still open in the broker.

        The conversation record (an AskUserQuestion tool_use no turn has a result for)
        and the restored and pending question lists. A question with no card id gets a
        ``reconcile:<tool_use_id>`` request id for its close. A record question asked
        less than ``settled_s`` ago is left out: its card may not be open yet, nor its
        call written to the session file.
        """
        records = [*(self._turn_records_for_reconcile()), {"parts": self._pending_assistant_parts}]
        answered = tool_result_ids(records)
        settled_before = (datetime.now(UTC) - timedelta(seconds=settled_s)).isoformat()
        open_questions: dict[str, str] = {}
        for record in records:
            for part in record.get("parts") or []:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "tool_use"
                    and part.get("name") == QUESTION_TOOL
                    and isinstance(part.get("id"), str)
                    and part["id"] not in answered
                    and str(part.get(TOOL_STARTED_AT) or "") <= settled_before
                ):
                    open_questions[part["id"]] = f"reconcile:{part['id']}"
        # The restored and pending lists also hold permission gates (sent as
        # ask_user_question too): only a question counts, by its card's kind or, for a card
        # from an older broker without one, by its call being an AskUserQuestion in the
        # record. A card whose question the record already answered is still listed, so
        # its replay stops.
        record_questions = {
            part["id"]
            for record in records
            for part in record.get("parts") or []
            if isinstance(part, dict)
            and part.get("type") == "tool_use"
            and part.get("name") == QUESTION_TOOL
            and isinstance(part.get("id"), str)
        }
        for controls in (self._unrestored_questions, self._pending_ask_user_questions):
            for request_id, data in controls.items():
                data = data or {}
                tool_use_id = str(data.get("tool_use_id") or "")
                kind = (data.get("metadata") or {}).get("control_kind")
                if not tool_use_id or tool_use_id == request_id:
                    continue
                if kind == "question" or (kind is None and tool_use_id in record_questions):
                    open_questions[tool_use_id] = request_id
        self._record_answered = answered
        return open_questions

    def _turn_records_for_reconcile(self) -> list[dict[str, Any]]:
        return [{"role": turn.role, "parts": turn.parts} for turn in self._conversation_turns]

    def _native_question_index_for(self, transport: Any) -> NativeQuestionIndex:
        native_id = transport.session_id
        if not native_id:
            raise NativeTranscriptUnavailableError(
                "The CLI's session id is not known yet, so its session file cannot be read"
            )
        index = self._question_index
        if index is None or index.native_session_id != native_id:
            path = Path(
                transport.native_transcript_path()
                or claude_project_dir(transport.workspace_dir) / f"{native_id}.jsonl"
            )
            index = self._question_index = NativeQuestionIndex(path, native_id)
        return index

    async def reconcile_open_questions(self, *, trigger: str) -> dict[str, Any]:
        """Check every open question against the CLI's session file; close the stale ones.

        One reconcile at a time (the sweep and the owner route share the index).
        """
        lock = getattr(self, "_question_reconcile_lock", None)
        if lock is None:
            lock = self._question_reconcile_lock = asyncio.Lock()
        async with lock:
            return await self._reconcile_open_questions_locked(trigger=trigger)

    async def _reconcile_open_questions_locked(self, *, trigger: str) -> dict[str, Any]:
        transport = self._transport
        if transport is None or not transport.capabilities.native_controls:
            raise NativeQuestionsUnsupportedError(
                "This session's transport keeps no native session file to reconcile against"
            )
        candidates = self._open_record_questions(
            settled_s=self._settings.tmux_native_control_sweep_interval_s
        )
        if not candidates:
            return {"checked": 0, "closed": [], "kept": []}
        index = self._native_question_index_for(transport)
        try:
            await asyncio.to_thread(index.refresh)
        except NativeTranscriptUnavailableError:
            self.question_reconcile_counts["transcript_unavailable"] += 1
            raise
        cards = transport.open_question_tool_use_ids()
        closed: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        menu_state: str | None = None
        for tool_use_id, request_id in candidates.items():
            if tool_use_id in cards:
                # A card whose call the session file never got is no question of this
                # session (LEX-2025); the transport closes it under its own guards.
                closed_card = None
                if index.state(tool_use_id) == NEVER_SHOWN:
                    closed_card = await transport.dismiss_never_shown_question(
                        tool_use_id, trigger=trigger
                    )
                if closed_card is None:
                    kept.append(
                        {"tool_use_id": tool_use_id, "why": "an open card; its own checks apply"}
                    )
                    continue
                closed.append(
                    {
                        "tool_use_id": tool_use_id,
                        "request_id": closed_card,
                        "reason": DISMISS_NEVER_SHOWN,
                    }
                )
                still_open = self._open_record_questions().get(tool_use_id)
                if still_open is not None and still_open != closed_card:
                    await self._close_record_question(
                        tool_use_id, still_open, DISMISS_NEVER_SHOWN, trigger=trigger, index=index
                    )
                continue
            state = index.state(tool_use_id)
            if tool_use_id in self._record_answered or state == ANSWERED:
                reason = DISMISS_RESOLVED_IN_TERMINAL
            elif state == NEVER_SHOWN:
                reason = DISMISS_NEVER_SHOWN
            elif trigger == RECONCILE_START:
                kept.append(
                    {"tool_use_id": tool_use_id, "why": "unanswered; checked again by the sweep"}
                )
                continue
            else:
                if menu_state is None:
                    try:
                        menu_state = await transport.observe_native_menu_state()
                    except TimeoutError:
                        # A capture that hangs proves nothing: keep every question.
                        self.question_reconcile_counts["screen_check_timeouts"] += 1
                        logger.error("Question reconcile: the pane capture timed out")
                        menu_state = "timed out"
                if menu_state != MENU_GONE:
                    kept.append(
                        {"tool_use_id": tool_use_id, "why": f"unanswered, menu {menu_state}"}
                    )
                    continue
                reason = DISMISS_MENU_NOT_SHOWN
            await self._close_record_question(
                tool_use_id, request_id, reason, trigger=trigger, index=index
            )
            closed.append({"tool_use_id": tool_use_id, "request_id": request_id, "reason": reason})
        return {"checked": len(candidates), "closed": closed, "kept": kept}

    async def _close_record_question(
        self,
        tool_use_id: str,
        request_id: str,
        reason: str,
        *,
        trigger: str,
        index: NativeQuestionIndex,
    ) -> None:
        result = index.result(tool_use_id)
        frame: dict[str, Any] = {
            "type": "ask_user_resolved",
            "event_type": "ask_user.resolved",
            "request_id": request_id,
            "tool_use_id": tool_use_id,
            "decision": "completed_elsewhere" if result else reason,
            "accepted": False,
            "dismissed": True,
            "dismiss_reason": reason,
            "dismiss_detail": _DETAILS[reason],
            "dismiss_trigger": trigger,
            "control_kind": "question",
            "metadata": {"source": "skuld_question_reconcile"},
        }
        if result is not None and not result.is_error and result.content:
            frame["native_result_content"] = result.content
        logger.warning(
            "AUDIT session %s: question %s (%s) closed by %s: %s",
            self.session_id,
            tool_use_id,
            request_id,
            trigger,
            _DETAILS[reason],
        )
        await self._handle_cli_event(frame)  # counted there, with every other close

    async def dismiss_record_question(self, tool_use_id: str, *, dismissed_by: str) -> dict:
        """An owner closes a question that is open only in the record (audited)."""
        request_id = self._open_record_questions().get(tool_use_id)
        if request_id is None:
            raise LookupError(f"No open question {tool_use_id}")
        detail = f"dismissed by operator {dismissed_by}"
        logger.warning(
            "AUDIT session %s: question %s (%s) %s",
            self.session_id,
            tool_use_id,
            request_id,
            detail,
        )
        await self._handle_cli_event(
            {
                "type": "ask_user_resolved",
                "event_type": "ask_user.resolved",
                "request_id": request_id,
                "tool_use_id": tool_use_id,
                "decision": "dismissed_by_operator",
                "accepted": False,
                "dismissed": True,
                "dismiss_reason": "dismissed_by_operator",
                "dismiss_detail": detail,
                "dismiss_trigger": "operator",
                "control_kind": "question",
                "metadata": {"source": "skuld_question_reconcile"},
            }
        )  # counted where the frame is handled, with every other close
        return {"tool_use_id": tool_use_id, "request_id": request_id, "dismiss_detail": detail}

    async def _run_question_reconcile_loop(self) -> None:
        """Reconcile once the transport is up, then every sweep interval (LEX-1979)."""
        interval_s = self._settings.tmux_native_control_sweep_interval_s
        trigger = RECONCILE_START
        while True:
            await asyncio.sleep(interval_s if trigger != RECONCILE_START else 0)
            transport = self._transport
            if transport is None or not transport.is_alive:
                await asyncio.sleep(interval_s)
                continue
            if not transport.capabilities.native_controls:
                return
            try:
                await self.reconcile_open_questions(trigger=trigger)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed check never closes anything; the next one retries.
                self.question_reconcile_counts["failures"] += 1
                logger.error("Question reconcile (%s) failed", trigger, exc_info=True)
            trigger = RECONCILE_SWEEP
