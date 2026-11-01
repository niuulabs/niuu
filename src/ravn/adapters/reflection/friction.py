"""Friction a resident keeps hitting, recorded so it can act on it.

A *friction signal* is one wall hit in one session, read from that session's
own record rather than from the model's account of it:

* from tool results (carried on ``ravn.session.ended`` by the agent):
  ``tool_failure``, ``timeout``, ``retry``, ``budget_exhausted``, and
  ``route_recovery`` — a workaround, the failed route that was bypassed;
* from the outcome contract: ``escalation`` (``continuation: ask_operator`` or
  ``decision: escalate``), ``capability_gap`` (``working_state.capability_gaps``)
  and ``blocked``.

The ledger clusters signals by meaning into one Mímir page per friction
(``<ledger_prefix>/<kind>/<slug>.md``), counts occurrences, links the lessons
reflection says would prevent it, and records what the self-improvement loop
did about it and whether that helped. It also answers the resident runtime's
"do I already have a resolution for this?" before an escalation reaches the
operator (:class:`~ravn.ports.known_resolutions.KnownResolutionPort`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from niuu.observability import get_observability
from ravn.ports.known_resolutions import KnownResolution, KnownResolutionPort
from ravn.resident_text import compact_line, slug

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort
    from ravn.adapters.reflection.lessons import LessonIndex
    from ravn.config import FrictionConfig

#: Friction a human sees: the resident asked, declared a gap, or stopped. Whether
#: an improvement helped is judged by whether escalations still get through.
HUMAN_FRICTION_KINDS = frozenset({"escalation", "capability_gap", "blocked"})
TOOL_FRICTION_KINDS = frozenset(
    {"tool_failure", "timeout", "retry", "route_recovery", "budget_exhausted"}
)

OPEN = "open"
ADDRESSED = "addressed"
RESOLVED = "resolved"

_STATE_BLOCK = re.compile(r"^```json\n(.*?)\n```$", re.MULTILINE | re.DOTALL)
_URL_KEYS = ("url", "uri", "endpoint", "href")


@dataclass(frozen=True)
class FrictionSignal:
    kind: str
    subject: str
    detail: str


@dataclass
class FrictionRecord:
    """One recurring friction and everything done about it."""

    path: str
    kind: str
    subject: str
    status: str = OPEN
    total: int = 0
    first_seen: str = ""
    last_seen: str = ""
    occurrences: list[dict[str, str]] = field(default_factory=list)
    linked_lessons: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    redirected_cases: list[str] = field(default_factory=list)
    escalated_anyway: list[dict[str, str]] = field(default_factory=list)

    def recurrences_since(self, moment: str) -> int:
        """How often this friction came back after *moment* (an ISO timestamp)."""
        if self.kind in HUMAN_FRICTION_KINDS:
            return sum(1 for item in self.escalated_anyway if item.get("at", "") > moment)
        return sum(1 for item in self.occurrences if item.get("at", "") > moment)

    def live_action(self) -> dict[str, str] | None:
        """The action currently standing for this friction, if any."""
        for action in reversed(self.actions):
            if action.get("status") in {"pending", "effective"}:
                return action
        return None

    def tried(self) -> set[str]:
        """Action kinds already judged ineffective here; never retried."""
        return {a.get("kind", "") for a in self.actions if a.get("status") == "ineffective"}


def friction_signals(payload: dict, *, excerpt_chars: int) -> list[FrictionSignal]:
    """Extract this session's friction from its ``ravn.session.ended`` payload."""
    signals: list[FrictionSignal] = []
    fields = payload.get("structured_outcome")
    if isinstance(fields, dict):
        signals.extend(_outcome_friction(fields, excerpt_chars=excerpt_chars))
    for item in payload.get("friction") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind == "budget_exhausted":
            signals.append(
                FrictionSignal(kind, "iteration budget exhausted", str(item.get("detail") or ""))
            )
        elif kind in TOOL_FRICTION_KINDS and item.get("tool"):
            subject = f"{item['tool']}: {_route_target(str(item.get('input') or ''))}"
            signals.append(
                FrictionSignal(
                    kind, subject, compact_line(str(item.get("detail") or ""), limit=excerpt_chars)
                )
            )
    for item in payload.get("route_recoveries") or []:
        if not isinstance(item, dict) or not item.get("tool"):
            continue
        signals.append(
            FrictionSignal(
                "route_recovery",
                f"{item['tool']}: {_route_target(str(item.get('failed_input') or ''))}",
                f"worked instead: {_route_target(str(item.get('succeeded_input') or ''))}",
            )
        )
    unique: dict[tuple[str, str], FrictionSignal] = {}
    for signal in signals:
        if signal.subject.strip():
            unique.setdefault((signal.kind, signal.subject), signal)
    return list(unique.values())


def _outcome_friction(fields: dict, *, excerpt_chars: int) -> list[FrictionSignal]:
    signals: list[FrictionSignal] = []
    continuation = str(fields.get("continuation") or "").strip().casefold()
    decision = str(fields.get("decision") or "").strip().casefold()
    rationale = compact_line(str(fields.get("rationale") or ""), limit=excerpt_chars)
    if continuation == "ask_operator" or decision == "escalate":
        question = str(fields.get("question") or "").strip()
        if not question and isinstance(fields.get("open_questions"), list):
            question = next(
                (str(q).strip() for q in fields["open_questions"] if str(q).strip()), ""
            )
        subject = question or rationale
        if subject:
            signals.append(
                FrictionSignal("escalation", compact_line(subject, limit=excerpt_chars), rationale)
            )
    if decision == "blocked" and rationale:
        signals.append(
            FrictionSignal("blocked", rationale, str(fields.get("recommended_action") or ""))
        )
    working_state = fields.get("working_state")
    gaps = working_state.get("capability_gaps") if isinstance(working_state, dict) else None
    for gap in gaps if isinstance(gaps, list) else []:
        if isinstance(gap, dict):
            text = str(gap.get("need") or gap.get("capability") or gap.get("description") or "")
        else:
            text = str(gap)
        if text.strip():
            signals.append(
                FrictionSignal("capability_gap", compact_line(text, limit=excerpt_chars), rationale)
            )
    return signals


def _route_target(raw_input: str) -> str:
    """The route a tool call aimed at: host and path for URLs, else the input."""
    try:
        parsed: Any = json.loads(raw_input)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        for key in _URL_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and "://" in value:
                parts = urlsplit(value)
                return f"{parts.netloc}{parts.path}"
    return compact_line(raw_input, limit=160)


class FrictionLedger(KnownResolutionPort):
    """Friction records on Mímir, clustered by meaning."""

    def __init__(
        self,
        *,
        mimir: MimirPort,
        lessons: LessonIndex,
        config: FrictionConfig,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._mimir = mimir
        self._lessons = lessons
        self._config = config
        self._clock = clock
        self._prefix = config.ledger_prefix.strip("/")
        self._cache: dict[str, tuple[datetime, FrictionRecord]] = {}

    async def load(self) -> list[FrictionRecord]:
        records: list[FrictionRecord] = []
        listed: set[str] = set()
        for meta in await self._mimir.list_pages(prefix=f"{self._prefix}/"):
            listed.add(meta.path)
            cached = self._cache.get(meta.path)
            if cached is not None and cached[0] == meta.updated_at:
                records.append(cached[1])
                continue
            record = parse_record(meta.path, await self._mimir.read_page(meta.path))
            self._cache[meta.path] = (meta.updated_at, record)
            records.append(record)
        for path in [path for path in self._cache if path not in listed]:
            del self._cache[path]
        return records

    async def record(
        self,
        signals: list[FrictionSignal],
        *,
        session_id: str,
        links: dict[int, str] | None = None,
    ) -> list[FrictionRecord]:
        """Add one session's signals to the ledger; *links* maps a signal to a lesson."""
        if not signals:
            return []
        now = self._clock().isoformat()
        records = await self.load()
        touched: list[FrictionRecord] = []
        telemetry = get_observability()
        for index, signal in enumerate(signals):
            record = await self._match(signal, records)
            if record is None:
                record = FrictionRecord(
                    path=self._path_for(signal, records),
                    kind=signal.kind,
                    subject=signal.subject,
                    first_seen=now,
                )
                records.append(record)
            record.total += 1
            record.last_seen = now
            record.occurrences.append(
                {"at": now, "session_id": session_id, "detail": signal.detail}
            )
            record.occurrences = record.occurrences[-self._config.max_occurrences_kept :]
            lesson = (links or {}).get(index, "")
            if lesson and lesson not in record.linked_lessons:
                record.linked_lessons.append(lesson)
            await self.save(record)
            touched.append(record)
            telemetry.count(
                "ravn.friction.recorded",
                attributes={"ravn.friction.kind": signal.kind},
                description="Friction signals recorded into the resident friction ledger.",
            )
            telemetry.event(
                "ravn.friction.recorded",
                attributes={
                    "ravn.friction.kind": signal.kind,
                    "ravn.friction.ref": record.path,
                    "ravn.friction.occurrences": record.total,
                },
                content={"subject": signal.subject, "detail": signal.detail},
            )
        return touched

    async def save(self, record: FrictionRecord) -> None:
        await self._mimir.upsert_page(record.path, render_record(record))

    # ------------------------------------------------------------------
    # KnownResolutionPort
    # ------------------------------------------------------------------

    async def resolution_for(self, question: str) -> KnownResolution | None:
        candidates = [
            record
            for record in await self.load()
            if record.kind in HUMAN_FRICTION_KINDS
            and record.status in {ADDRESSED, RESOLVED}
            and record.live_action() is not None
        ]
        if not candidates or not question.strip():
            return None
        scores = await self._lessons.similarities(question, [r.subject for r in candidates])
        best, score = max(zip(candidates, scores, strict=True), key=lambda item: item[1])
        if score < self._config.similarity_threshold:
            return None
        action = best.live_action()
        assert action is not None  # noqa: S101 — filtered above
        return KnownResolution(ref=best.path, instruction=action.get("instruction", ""))

    async def note_redirect(self, ref: str, case_id: str) -> bool:
        record = await self._get(ref)
        if case_id in record.redirected_cases:
            return False
        record.redirected_cases.append(case_id)
        await self.save(record)
        return True

    async def note_escalated_anyway(self, ref: str, case_id: str, question: str) -> None:
        record = await self._get(ref)
        record.escalated_anyway.append(
            {"at": self._clock().isoformat(), "case_id": case_id, "question": question}
        )
        await self.save(record)

    # ------------------------------------------------------------------

    async def _get(self, ref: str) -> FrictionRecord:
        return parse_record(ref, await self._mimir.read_page(ref))

    async def _match(
        self, signal: FrictionSignal, records: list[FrictionRecord]
    ) -> FrictionRecord | None:
        same_kind = [record for record in records if record.kind == signal.kind]
        exact = next((r for r in same_kind if r.subject == signal.subject), None)
        if exact is not None or not same_kind:
            return exact
        scores = await self._lessons.similarities(signal.subject, [r.subject for r in same_kind])
        best, score = max(zip(same_kind, scores, strict=True), key=lambda item: item[1])
        return best if score >= self._config.similarity_threshold else None

    def _path_for(self, signal: FrictionSignal, records: list[FrictionRecord]) -> str:
        name = slug(signal.subject, max_length=60, fallback="friction")
        base = f"{self._prefix}/{signal.kind}/{name}"
        taken = {record.path for record in records}
        path = f"{base}.md"
        if path in taken:
            digest = hashlib.sha256(signal.subject.encode("utf-8")).hexdigest()[:8]
            path = f"{base}-{digest}.md"
        return path


def render_record(record: FrictionRecord) -> str:
    """Render a friction record: readable sections plus one machine-readable block."""
    state = asdict(record)
    del state["path"]
    lines = [
        "---",
        f'title: "Friction: {_quote(compact_line(record.subject, limit=120))}"',
        "type: observation",
        f"kind: {record.kind}",
        f"status: {record.status}",
        f"occurrences: {record.total}",
        f"last_seen: {record.last_seen}",
        "---",
        "",
        f"# Friction ({record.kind}): {compact_line(record.subject, limit=200)}",
        "",
        f"Seen {record.total} time(s), first {record.first_seen}, last {record.last_seen}. "
        f"Status: **{record.status}**.",
    ]
    if record.linked_lessons:
        lines += ["", "## Linked lessons", *[f"- `{path}`" for path in record.linked_lessons]]
    if record.actions:
        lines += ["", "## Improvement actions"]
        for action in record.actions:
            lines.append(
                f"- {action.get('acted_at', '')} **{action.get('kind', '')}** "
                f"({action.get('status', '')}): {action.get('expected_benefit', '')}"
            )
    lines += ["", "## Record", "```json", json.dumps(state, indent=2, sort_keys=True), "```", ""]
    return "\n".join(lines)


def parse_record(path: str, content: str) -> FrictionRecord:
    match = _STATE_BLOCK.search(content)
    if match is None:
        raise ValueError(f"friction record {path!r} has no ```json record block")
    state = json.loads(match.group(1))
    known = {name for name in FrictionRecord.__dataclass_fields__ if name != "path"}
    return FrictionRecord(path=path, **{k: v for k, v in state.items() if k in known})


def _quote(text: str) -> str:
    return text.replace('"', '\\"')
