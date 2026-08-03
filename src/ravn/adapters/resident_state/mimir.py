"""Mimir/local resident state adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ravn.adapters.resident_pages import collect_pages
from ravn.domain.resident_continuation import (
    ResidentA2ATaskRecord,
    ResidentBudgetSnapshot,
    ResidentMemoryEntry,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentScheduledWakeRecord,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)
from ravn.domain.resident_state import ResidentStatePort
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import (
    _A2A_TASKS_PATH,
    _OPERATOR_ANSWER_PATH,
    _OPERATOR_NEEDED_PATH,
    _SCHEDULED_WAKE_PATH,
    LocalResidentMemory,
    _a2a_task_path,
    _case_path,
    _compact_line,
    _first_heading_or_line,
    _operator_answer_is_consumed,
    _operator_marker_is_pending,
    _parse_policy_observation,
    _render_a2a_task,
    _render_answered_operator_needed,
    _render_budget_snapshot,
    _render_consumed_operator_answer,
    _render_consumed_scheduled_wake,
    _render_operator_answer,
    _render_operator_needed,
    _render_policy_decision,
    _render_policy_observation,
    _render_scheduled_wake,
    _render_turn_record,
    _render_working_state,
    _slug,
    _timestamp_slug,
)


class MimirResidentState(ResidentStatePort):
    """One Mimir-backed resident state adapter."""

    def __init__(
        self,
        mimir: MimirPort,
        *,
        continuation_prefix: str = "resident/continuation",
    ) -> None:
        self._mimir = mimir
        self._prefix = continuation_prefix.strip("/").strip() or "resident/continuation"

    async def available(self) -> bool:
        return True

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        query = _compact_line(mandate) or "resident continuation"
        pages = await self._mimir.search(query)
        entries: list[ResidentMemoryEntry] = []
        for page in pages:
            path = getattr(page.meta, "path", "")
            if not path.startswith(self._prefix):
                continue
            summary = getattr(page.meta, "summary", "") or _first_heading_or_line(page.content)
            entries.append(ResidentMemoryEntry(path=path, summary=summary, content=page.content))
            if len(entries) >= limit:
                break
        return entries

    async def read(self, ref: str) -> ResidentMemoryEntry | None:
        try:
            content = await self._mimir.read_page(ref)
        except FileNotFoundError:
            return None
        return ResidentMemoryEntry(
            path=ref,
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def read_working_state(self, resident_id: str) -> ResidentMemoryEntry | None:
        return await self.read(self._working_state_path(resident_id))

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%SZ")
        path = str(
            Path(self._prefix) / _case_path(record.case_id, f"turns/{stamp}-{record.turn_index}.md")
        )
        await self._mimir.upsert_page(path, _render_turn_record(record))
        return path

    async def write_working_state(self, record: ResidentWorkingStateRecord) -> str:
        path = self._working_state_path(record.resident_id)
        await self._mimir.upsert_page(path, _render_working_state(record))
        return path

    async def read_a2a_task(self, task_id: str) -> ResidentMemoryEntry | None:
        return await self.read(str(Path(self._prefix) / _a2a_task_path(task_id)))

    async def write_a2a_task(self, record: ResidentA2ATaskRecord) -> str:
        path = str(Path(self._prefix) / _a2a_task_path(record.task_id))
        await self._mimir.upsert_page(path, _render_a2a_task(record))
        return path

    async def list_a2a_tasks(self) -> list[ResidentMemoryEntry]:
        prefix = str(Path(self._prefix) / _A2A_TASKS_PATH)
        pages = await self._mimir.list_pages(prefix=prefix)
        entries: list[ResidentMemoryEntry] = []
        for page in pages:
            path = str(getattr(page, "path", "") or "")
            if not path.endswith(".md"):
                continue
            try:
                content = await self._mimir.read_page(path)
            except FileNotFoundError:
                continue
            entries.append(
                ResidentMemoryEntry(
                    path=path,
                    summary=_first_heading_or_line(content),
                    content=content,
                )
            )
        return entries

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        path = str(Path(self._prefix) / _case_path(snapshot.case_id, "budget/latest.md"))
        await self._mimir.upsert_page(
            path,
            _render_budget_snapshot(snapshot, updated_at=datetime.now(UTC)),
        )
        return path

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        slug = _slug(observation.subject) or "policy-observation"
        path = f"{self._prefix}/policy/{slug}.md"
        await self._mimir.upsert_page(path, _render_policy_observation(observation))
        return path

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        return await collect_pages(self._mimir, f"{self._prefix}/policy", _parse_policy_observation)

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        stamp = decision.created_at.strftime("%Y%m%dT%H%M%SZ")
        slug = _slug(decision.action_title) or "policy-decision"
        path = f"{self._prefix}/policy-decisions/{stamp}-{decision.turn_index}-{slug}.md"
        await self._mimir.upsert_page(path, _render_policy_decision(decision))
        return path

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
        case_id: str = "",
        turn_ref: str = "",
    ) -> str:
        path = str(Path(self._prefix) / _case_path(case_id or turn.case_id, _OPERATOR_NEEDED_PATH))
        await self._mimir.upsert_page(
            path,
            _render_operator_needed(
                question=question,
                reason=reason,
                turn=turn,
                status="pending",
                turn_ref=turn_ref,
            ),
        )
        return path

    async def read_operator_needed(self, case_id: str = "") -> ResidentMemoryEntry | None:
        path = str(Path(self._prefix) / _case_path(case_id, _OPERATOR_NEEDED_PATH))
        try:
            content = await self._mimir.read_page(path)
        except FileNotFoundError:
            return None
        if not _operator_marker_is_pending(content):
            return None
        return ResidentMemoryEntry(
            path=path,
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def write_operator_answer(self, answer: str, *, case_id: str = "") -> str:
        now = datetime.now(UTC)
        marker_path = str(Path(self._prefix) / _case_path(case_id, _OPERATOR_NEEDED_PATH))
        try:
            prior = await self._mimir.read_page(marker_path)
        except FileNotFoundError:
            prior = ""
        answer_path = str(Path(self._prefix) / _case_path(case_id, _OPERATOR_ANSWER_PATH))
        await self._mimir.upsert_page(
            answer_path,
            _render_operator_answer(
                answer,
                answered_at=now,
                case_id=case_id,
                pending_context=prior,
            ),
        )
        history_path = str(
            Path(self._prefix) / _case_path(case_id, f"operator-answers/{_timestamp_slug(now)}.md")
        )
        await self._mimir.upsert_page(
            history_path,
            _render_operator_answer(
                answer,
                answered_at=now,
                case_id=case_id,
                pending_context=prior,
            ),
        )
        await self._mimir.upsert_page(
            marker_path,
            _render_answered_operator_needed(prior, answer_path=answer_path, answered_at=now),
        )
        return answer_path

    async def read_operator_answer(self, case_id: str = "") -> ResidentMemoryEntry | None:
        path = str(Path(self._prefix) / _case_path(case_id, _OPERATOR_ANSWER_PATH))
        try:
            content = await self._mimir.read_page(path)
        except FileNotFoundError:
            return None
        if _operator_answer_is_consumed(content):
            return None
        return ResidentMemoryEntry(
            path=path,
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        path = answer.path or f"{self._prefix}/{_OPERATOR_ANSWER_PATH}"
        try:
            prior = await self._mimir.read_page(path)
        except FileNotFoundError:
            prior = answer.content
        await self._mimir.upsert_page(
            path,
            _render_consumed_operator_answer(prior, consumed_at=datetime.now(UTC)),
        )
        return path

    async def list_operator_needed(self) -> list[ResidentMemoryEntry]:
        return await self._list_case_entries(_OPERATOR_NEEDED_PATH, pending=True)

    async def list_operator_answers(self) -> list[ResidentMemoryEntry]:
        return await self._list_case_entries(_OPERATOR_ANSWER_PATH, pending=False)

    async def _list_case_entries(
        self,
        leaf: str,
        *,
        pending: bool,
    ) -> list[ResidentMemoryEntry]:
        prefix = f"{self._prefix}/cases"
        pages = await self._mimir.list_pages(prefix=prefix)
        entries: list[ResidentMemoryEntry] = []
        for page in pages:
            path = str(getattr(page, "path", "") or "")
            if not path.endswith(leaf):
                continue
            try:
                content = await self._mimir.read_page(path)
            except FileNotFoundError:
                continue
            available = (
                _operator_marker_is_pending(content)
                if pending
                else not _operator_answer_is_consumed(content)
            )
            if available:
                entries.append(
                    ResidentMemoryEntry(
                        path=path,
                        summary=_first_heading_or_line(content),
                        content=content,
                    )
                )
        return sorted(entries, key=lambda item: item.path)

    async def write_scheduled_wake(self, record: ResidentScheduledWakeRecord) -> str:
        path = str(Path(self._prefix) / _case_path(record.case_id, _SCHEDULED_WAKE_PATH))
        await self._mimir.upsert_page(path, _render_scheduled_wake(record))
        return path

    async def list_scheduled_wakes(self) -> list[ResidentMemoryEntry]:
        return await self._list_case_entries(_SCHEDULED_WAKE_PATH, pending=True)

    async def consume_scheduled_wake(self, wake: ResidentMemoryEntry) -> str:
        path = wake.path or f"{self._prefix}/{_SCHEDULED_WAKE_PATH}"
        try:
            prior = await self._mimir.read_page(path)
        except FileNotFoundError:
            prior = wake.content
        await self._mimir.upsert_page(
            path,
            _render_consumed_scheduled_wake(prior, consumed_at=datetime.now(UTC)),
        )
        return path

    async def list_refs(self, prefix: str = "") -> list[str]:
        pages = await self._mimir.list_pages(prefix=prefix or self._prefix)
        return sorted(str(getattr(page, "path", "")) for page in pages if getattr(page, "path", ""))

    def _working_state_path(self, resident_id: str) -> str:
        resident_slug = _slug(resident_id) or "resident"
        return f"{self._prefix}/working-state/{resident_slug}.md"


class LocalResidentState(LocalResidentMemory, ResidentStatePort):
    """Filesystem-backed resident state adapter for local development/tests."""

    def __init__(
        self,
        root: Path,
        *,
        continuation_prefix: str = "resident/continuation",
    ) -> None:
        LocalResidentMemory.__init__(self, root, prefix=continuation_prefix)

    async def available(self) -> bool:
        return True

    async def list_refs(self, prefix: str = "") -> list[str]:
        root = self._root.resolve()
        base = (root / self._prefix).resolve()
        if not base.is_relative_to(root) or not base.exists():
            return []

        requested = PurePosixPath(prefix.replace("\\", "/")) if prefix else None
        if requested and (requested.is_absolute() or ".." in requested.parts):
            return []

        refs = sorted(str(path.relative_to(root)) for path in base.rglob("*.md") if path.is_file())
        if requested is None:
            return refs
        requested_ref = requested.as_posix().rstrip("/")
        return [ref for ref in refs if ref == requested_ref or ref.startswith(f"{requested_ref}/")]

    async def write_scheduled_wake(self, record: ResidentScheduledWakeRecord) -> str:
        rel = self._prefix / _case_path(record.case_id, _SCHEDULED_WAKE_PATH)
        return self._write(rel, _render_scheduled_wake(record))

    async def list_scheduled_wakes(self) -> list[ResidentMemoryEntry]:
        return self._list_case_entries(_SCHEDULED_WAKE_PATH, pending=True)

    async def consume_scheduled_wake(self, wake: ResidentMemoryEntry) -> str:
        rel = Path(wake.path) if wake.path else self._prefix / _SCHEDULED_WAKE_PATH
        path = self._root / rel
        prior = path.read_text(encoding="utf-8") if path.exists() else wake.content
        return self._write(
            rel,
            _render_consumed_scheduled_wake(prior, consumed_at=datetime.now(UTC)),
        )
