"""PostgreSQL adapter for the durable, append-only session event log.

Implements :class:`SessionEventLogRepository`. Writes are idempotent on
(session_id, seq) via ON CONFLICT DO NOTHING, so the producer (skuld) can retry
at-least-once without creating duplicates. Reads are cursor-based (seq) so any
client can resume a full transcript replay.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from niuu.domain.text_projection import (
    REPAIR_KIND,
    apply_repair_markers,
    projection_digest,
)
from niuu.domain.work_facts import (
    HOOK_KIND,
    TOOL_CLOSE_HOOKS,
    TOOL_OPEN_HOOK,
    TURN_END_HOOK,
    TURN_END_KIND,
    WORK_HOOKS,
    WORK_KINDS,
)
from volundr.adapters.outbound._jsonb import dumps_jsonb, force_scrub_json, scrub_text
from volundr.domain.history_import import (
    HistoryImportConflictError,
    HistoryImportSessionNotFoundError,
    HistoryImportValidationError,
)
from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository, _log_entries_conflict
from volundr.domain.session_usage import fold_usage, observation, parse_json, projection_key

logger = logging.getLogger(__name__)


def _sql_list(values: Iterable[str]) -> str:
    """A SQL literal list from fixed module constants (never from input)."""
    return ", ".join(f"'{value}'" for value in sorted(values))


# The final-reply projection (latest_final_*) and the work facts (G10, F12d:
# last_work_at, open_tool_count) are folded in ONE update per inserted row, so the
# session row is written at most once per frame, and not at all when the frame
# changes nothing. Only newly inserted rows fold (ON CONFLICT DO NOTHING), which keeps
# producer retries idempotent. Which rows count as work is
# niuu.domain.work_facts, the same predicate Skuld and the control plane use.
_FINAL_OUTPUT_SQL = """i.kind = 'conversation.turn' AND i.payload->>'type' = 'conversation.turn'
           AND i.payload->'turn'->>'role' = 'assistant'
           AND COALESCE(i.payload->'turn'->>'visibility', 'public') = 'public'
           AND i.payload->'turn'->'metadata'->'final_output' = 'true'::jsonb
           AND COALESCE(i.payload->'turn'->'metadata'->>'status', '')
               NOT IN ('error', 'interrupted')
           AND COALESCE(i.payload->'turn'->'metadata'->>'is_error', 'false') = 'false'
           AND jsonb_typeof(i.payload->'turn'->'content') = 'string'
           AND LENGTH(BTRIM(COALESCE(i.payload->'turn'->>'content', ''))) > 0
           AND LENGTH(COALESCE(i.payload->'turn'->>'id', '')) > 0"""

_INSERT_SQL = f"""WITH locked_session AS MATERIALIZED (
           SELECT id FROM sessions WHERE id = $1 FOR UPDATE
       ), inserted AS (
           INSERT INTO session_event_log
           (session_id, seq, kind, role, request_id, payload, ts)
           SELECT $1, $2, $3, $4, $5, $6, $7
           FROM (SELECT COUNT(*) FROM locked_session) AS lock_guard
           ON CONFLICT (session_id, seq) DO NOTHING
           RETURNING session_id, seq, kind, payload, ts
       ), facts AS (
           SELECT i.*, ({_FINAL_OUTPUT_SQL}) AS is_final,
               (i.kind IN ({_sql_list(WORK_KINDS)})
                OR (i.kind = '{HOOK_KIND}' AND h.hook IN ({_sql_list(WORK_HOOKS)}))) AS is_work,
               h.hook
           FROM inserted i
           CROSS JOIN LATERAL (
               SELECT COALESCE(i.payload->>'hook_event_name',
                               i.payload->'payload'->>'hook_event_name', '') AS hook
           ) h
       )
       UPDATE sessions s
       SET latest_final_seq = CASE WHEN f.is_final AND f.seq > s.latest_final_seq
               THEN f.seq ELSE s.latest_final_seq END,
           latest_final_turn_id = CASE WHEN f.is_final AND f.seq > s.latest_final_seq
               THEN f.payload->'turn'->>'id' ELSE s.latest_final_turn_id END,
           latest_final_at = CASE WHEN f.is_final AND f.seq > s.latest_final_seq
               THEN f.ts ELSE s.latest_final_at END,
           last_work_at = CASE WHEN f.is_work
               THEN GREATEST(s.last_work_at, f.ts) ELSE s.last_work_at END,
           open_tool_count = CASE
               WHEN NOT f.is_work THEN s.open_tool_count
               WHEN f.hook = '{TOOL_OPEN_HOOK}' THEN s.open_tool_count + 1
               WHEN f.hook IN ({_sql_list(TOOL_CLOSE_HOOKS)})
                   THEN GREATEST(s.open_tool_count - 1, 0)
               WHEN f.kind = '{TURN_END_KIND}' OR f.hook = '{TURN_END_HOOK}' THEN 0
               ELSE s.open_tool_count END
       FROM facts f
       WHERE s.id = f.session_id
           AND ((f.is_final AND f.seq > s.latest_final_seq)
                OR (f.is_work AND (
                    s.last_work_at IS NULL OR f.ts > s.last_work_at
                    OR f.hook = '{TOOL_OPEN_HOOK}'
                    OR (s.open_tool_count > 0 AND (
                        f.hook IN ({_sql_list(TOOL_CLOSE_HOOKS)})
                        OR f.kind = '{TURN_END_KIND}' OR f.hook = '{TURN_END_HOOK}')))))"""

_IMPORT_INSERT_SQL = """INSERT INTO session_event_log
       (session_id, seq, kind, role, request_id, payload, ts)
       VALUES ($1, $2, $3, $4, $5, $6, $7)"""

_HISTORY_IMPORT_IDLE_STATUSES = frozenset({"created", "stopped", "failed"})
_HISTORY_IMPORT_CHROME_KINDS = ("init", "capabilities", "available_commands")


class PostgresSessionEventLog(SessionEventLogRepository):
    """PostgreSQL adapter for the full-fidelity session event log."""

    def __init__(self, pool: asyncpg.Pool, *, usage_model_windows: dict[str, int] | None = None):
        self._pool = pool
        self._usage_model_windows = dict(usage_model_windows or {})

    async def append_projection_repair(
        self, session_id: UUID, marker: dict, *, expected_head: int
    ) -> int:
        """Append a verified maintenance projection with a stopped-session CAS.

        The operator must additionally verify the old broker is no longer alive
        before replacing its cache. This transaction locks the same session row
        as normal appends and validates every replacement against durable seeds.
        It never updates or deletes an existing event. Exact retries are no-ops.
        """
        if type(expected_head) is not int or expected_head < 0:
            raise ValueError("Projection repair requires a nonnegative expected head")
        if not isinstance(marker, dict) or marker.get("type") != REPAIR_KIND:
            raise ValueError("Invalid projection repair marker")
        repairs = marker.get("repairs")
        if not isinstance(repairs, list) or not repairs:
            raise ValueError("Projection repair must contain verified replacements")
        ids = [repair.get("turn_id") if isinstance(repair, dict) else None for repair in repairs]
        if any(not isinstance(identity, str) or not identity for identity in ids):
            raise ValueError("Projection repair turn identities must be nonempty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("Projection repair contains duplicate turn identities")
        if any(not isinstance(repair.get("proof"), dict) for repair in repairs):
            raise ValueError("Projection repair lacks a valid proof")
        if json.loads(dumps_jsonb(marker)) != marker:
            raise ValueError("Projection repair requires exact JSONB-safe content")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status FROM sessions WHERE id = $1 FOR UPDATE", session_id
                )
                if row is None:
                    raise ValueError("Projection repair session does not exist")
                previous = await conn.fetch(
                    "SELECT seq, payload FROM session_event_log "
                    "WHERE session_id=$1 AND kind=$2 ORDER BY seq",
                    session_id,
                    REPAIR_KIND,
                )
                for existing in previous:
                    payload = existing["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if payload == marker:
                        return existing["seq"]
                if row["status"] != "stopped":
                    raise ValueError("Projection repair requires a stopped session")
                head = await conn.fetchval(
                    "SELECT COALESCE(MAX(seq), 0) FROM session_event_log WHERE session_id=$1",
                    session_id,
                )
                if head != expected_head:
                    raise ValueError(
                        "Projection repair ledger head changed; create a fresh preview"
                    )
                seeds = await conn.fetch(
                    "SELECT payload FROM session_event_log WHERE session_id=$1 "
                    "AND kind='conversation.turn' AND payload->'turn'->>'id'=ANY($2::text[]) "
                    "ORDER BY seq",
                    session_id,
                    ids,
                )
                turns = {}
                for seed in seeds:
                    payload = seed["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    turn = payload["turn"]
                    turns.setdefault(turn["id"], turn)
                if set(turns) != set(ids):
                    raise ValueError("Projection repair lacks authoritative turn seeds")
                now = datetime.now(UTC)
                prior_frames = [
                    SessionLogEntry(
                        session_id=session_id,
                        seq=record["seq"],
                        kind=REPAIR_KIND,
                        payload=json.loads(record["payload"])
                        if isinstance(record["payload"], str)
                        else record["payload"],
                        ts=now,
                    )
                    for record in previous
                ]
                originals = apply_repair_markers(list(turns.values()), prior_frames)
                entry = SessionLogEntry(
                    session_id=session_id,
                    seq=head + 1,
                    kind=REPAIR_KIND,
                    payload=marker,
                    ts=now,
                )
                replacements = apply_repair_markers(originals, [entry])
                expected = {
                    repair["turn_id"]: repair.get("proof", {}).get("digest") for repair in repairs
                }
                if any(
                    replacement is original
                    or projection_digest(replacement) != expected[original["id"]]
                    for original, replacement in zip(originals, replacements, strict=True)
                ):
                    raise ValueError("Projection repair failed durable content verification")
                await conn.execute(_IMPORT_INSERT_SQL, *self._entry_to_args(entry))
                return entry.seq

    async def append(self, entries: list[SessionLogEntry]) -> int:
        if not entries:
            return 0
        # executemany is atomic: each insert takes the same session-row lock as
        # import_history, held until the batch finishes. Stable session ordering
        # avoids deadlocks between multi-session batches, without a global lock.
        # The COUNT guard preserves orphan-log writes (there is deliberately no FK).
        entries = sorted(entries, key=lambda e: (str(e.session_id), e.seq))
        args = [self._entry_to_args(e) for e in entries]
        has_usage = any(observation(e.payload, seq=e.seq, at=e.ts) is not None for e in entries)
        try:
            await self._append_batch(args, entries, has_usage=has_usage)
        except asyncpg.exceptions.UntranslatableCharacterError:
            # Defensive: a frame still carried a character Postgres can't store
            # past the primary scrub. Force-scrub the serialized payloads and retry
            # once so one bad frame never drops the whole batch (ON CONFLICT keeps
            # this idempotent if some rows already landed).
            logger.warning("session_event_log insert hit untranslatable char; retrying scrubbed")
            scrubbed = [self._force_scrub_args(a) for a in args]
            await self._append_batch(scrubbed, entries, has_usage=has_usage)
        return len(entries)

    async def _append_batch(
        self, args: list[tuple], entries: list[SessionLogEntry], *, has_usage: bool
    ) -> None:
        if not has_usage:
            await self._pool.executemany(_INSERT_SQL, args)
            return
        # The same session lock protects the durable insert and numeric summary.
        # A failed projection rolls the batch back; a retry cannot lose usage or
        # apply it twice. Ordinary frames retain the existing executemany path.
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(_INSERT_SQL, args)
                by_session: dict[UUID, list[int]] = {}
                for entry in entries:
                    by_session.setdefault(entry.session_id, []).append(entry.seq)
                for sid, seqs in by_session.items():
                    await self._project_usage(conn, sid, seqs)

    async def _project_usage(self, conn: asyncpg.Connection, sid: UUID, seqs: list[int]) -> None:
        row = await conn.fetchrow("SELECT session_usage FROM sessions WHERE id=$1 FOR UPDATE", sid)
        if row is None:
            return  # Existing orphan-log behavior: no session row means no projection.
        original = parse_json(row["session_usage"])
        state = original
        # Always project the STORED frames, never a conflicting retry payload.
        rows = await conn.fetch(
            """SELECT seq, payload, ts FROM session_event_log
               WHERE session_id=$1 AND seq=ANY($2::bigint[]) AND usage IS NULL ORDER BY seq""",
            sid,
            seqs,
        )
        for entry in rows:
            event = observation(parse_json(entry["payload"]), seq=entry["seq"], at=entry["ts"])
            if event is None:
                continue
            key = projection_key(event, state)
            prior = await conn.fetchrow(
                """SELECT seq, usage FROM session_event_log
                   WHERE session_id=$1 AND usage_key=$2
                   ORDER BY seq DESC LIMIT 1""",
                sid,
                key,
            )
            previous = parse_json(prior["usage"]) if prior else None
            state, merged = fold_usage(
                state, event, previous, model_windows=self._usage_model_windows
            )
            packed = dumps_jsonb(merged)
            await conn.execute(
                "UPDATE session_event_log SET usage_key=$3, usage=$4::jsonb "
                "WHERE session_id=$1 AND seq=$2",
                sid,
                entry["seq"],
                key,
                packed,
            )
            # Late lower-seq snapshots also update the identity's newest derived
            # checkpoint. Raw payload/ts/seq are immutable; only projection data
            # changes. The next lookup sees all maxima already accounted for.
            if prior and prior["seq"] > entry["seq"]:
                await conn.execute(
                    "UPDATE session_event_log SET usage=$3::jsonb WHERE session_id=$1 AND seq=$2",
                    sid,
                    prior["seq"],
                    packed,
                )
        if state != original:
            await conn.execute(
                "UPDATE sessions SET session_usage=$2::jsonb WHERE id=$1", sid, dumps_jsonb(state)
            )

    async def import_history(
        self, session_id: UUID, source_id: str, entries: list[SessionLogEntry]
    ) -> int:
        """Atomically append one native history to an idle, transcript-empty session.

        Existing connect/init rows retain their seqs. Imported frames receive new
        seqs in caller order, retaining their original timestamps. The final marker
        is committed in the same transaction, making retries idempotent without
        accepting a partial import or merging independent conversations.
        """
        self._validate_history_import(session_id, source_id, entries)
        snapshot = self._history_snapshot(entries)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status FROM sessions WHERE id = $1 FOR UPDATE", session_id
                )
                if row is None:
                    raise HistoryImportSessionNotFoundError(f"Session {session_id} does not exist")
                markers = await conn.fetch(
                    """SELECT payload FROM session_event_log
                       WHERE session_id = $1 AND kind = 'history_import'""",
                    session_id,
                )
                if markers:
                    self._check_history_source(markers, source_id, snapshot)
                    return 0
                if row["status"] not in _HISTORY_IMPORT_IDLE_STATUSES:
                    raise HistoryImportConflictError(
                        f"Session {session_id} must be created, stopped, or failed before import"
                    )
                captured = await conn.fetchrow(
                    """SELECT seq, kind FROM session_event_log
                       WHERE session_id = $1 AND NOT (
                           kind = ANY($2::text[]) OR (
                               kind = 'system' AND (
                                   COALESCE(payload->>'subtype', '') = 'init'
                                   OR payload @> '{"_per_connect_handshake": true}'::jsonb
                               )
                           )
                       ) ORDER BY seq LIMIT 1""",
                    session_id,
                    list(_HISTORY_IMPORT_CHROME_KINDS),
                )
                if captured is not None:
                    raise HistoryImportConflictError(
                        f"Session {session_id} already contains captured history "
                        f"at seq {captured['seq']} ({captured['kind']})"
                    )
                head = await conn.fetchval(
                    "SELECT MAX(seq) FROM session_event_log WHERE session_id = $1", session_id
                )
                head = int(head or 0)
                args = [
                    self._entry_to_args(replace(entry, seq=head + offset))
                    for offset, entry in enumerate(entries, start=1)
                ]
                marker = SessionLogEntry(
                    session_id=session_id,
                    seq=head + len(entries) + 1,
                    kind="history_import",
                    payload={
                        "type": "history_import",
                        "source_id": source_id,
                        **snapshot,
                    },
                    ts=datetime.now(UTC),
                )
                args.append(self._entry_to_args(marker))
                await self._insert_history_batch(conn, args)
        return len(entries)

    @staticmethod
    def _validate_history_import(
        session_id: UUID, source_id: str, entries: list[SessionLogEntry]
    ) -> None:
        if not isinstance(source_id, str) or not source_id.strip():
            raise HistoryImportValidationError("History source_id must be nonempty")
        if scrub_text(source_id) != source_id:
            raise HistoryImportValidationError("History source_id contains unsupported characters")
        if not entries:
            raise HistoryImportValidationError("Native history contains no frames to import")
        for entry in entries:
            if entry.session_id != session_id:
                raise HistoryImportValidationError("Every imported frame must belong to the target")
            if entry.kind == "history_import":
                raise HistoryImportValidationError(
                    "Imported frames cannot contain an import marker"
                )

    @staticmethod
    def _history_snapshot(entries: list[SessionLogEntry]) -> dict:
        """Fingerprint source order and content, independent of destination cursors."""
        digest = hashlib.sha256()
        partial = False
        for entry in entries:
            payload = dict(entry.payload)
            # Some adapters include the destination session in the wire frame;
            # that provisional UUID is not part of native transcript identity.
            if payload.get("session_id") == str(entry.session_id):
                payload.pop("session_id")
            provenance = payload.get("metadata", {}).get("native_import", {})
            partial = partial or bool(provenance.get("partial"))
            record = {
                "kind": entry.kind,
                "role": entry.role,
                "request_id": entry.request_id,
                "payload": payload,
                "ts": entry.ts.astimezone(UTC).isoformat(),
            }
            try:
                encoded = json.dumps(
                    record,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise HistoryImportValidationError("Native history contains invalid JSON") from exc
            digest.update(encoded)
            digest.update(b"\n")
        return {"snapshot_sha256": digest.hexdigest(), "count": len(entries), "partial": partial}

    @staticmethod
    def _check_history_source(markers: list, source_id: str, snapshot: dict) -> None:
        for marker in markers:
            payload = marker["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict) or payload.get("source_id") != source_id:
                raise HistoryImportConflictError(
                    "Session already imported a different history source"
                )
            if any(payload.get(key) != value for key, value in snapshot.items()):
                raise HistoryImportConflictError(
                    "Native history snapshot changed since import; stored history was not modified"
                )

    async def _insert_history_batch(self, conn: asyncpg.Connection, args: list[tuple]) -> None:
        try:
            # Savepoint rollback keeps the surrounding import transaction usable
            # for the defensive scrub retry, without releasing its session lock.
            async with conn.transaction():
                await conn.executemany(_IMPORT_INSERT_SQL, args)
        except asyncpg.exceptions.UntranslatableCharacterError:
            logger.warning("history import hit untranslatable char; retrying scrubbed")
            await conn.executemany(_IMPORT_INSERT_SQL, [self._force_scrub_args(a) for a in args])

    async def read_after(
        self,
        session_id: UUID,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[SessionLogEntry]:
        rows = await self._pool.fetch(
            """SELECT session_id, seq, kind, role, request_id, payload, ts
               FROM session_event_log
               WHERE session_id = $1 AND seq > $2
               ORDER BY seq ASC
               LIMIT $3""",
            session_id,
            after_seq,
            limit,
        )
        return [self._row_to_entry(r) for r in rows]

    async def read_after_excluding(
        self,
        session_id: UUID,
        after_seq: int = 0,
        limit: int = 1000,
        exclude_kinds: frozenset[str] = frozenset(),
    ) -> list[SessionLogEntry]:
        rows = await self._pool.fetch(
            """SELECT session_id, seq, kind, role, request_id, payload, ts
               FROM session_event_log
               WHERE session_id = $1 AND seq > $2 AND kind <> ALL($4::text[])
               ORDER BY seq ASC
               LIMIT $3""",
            session_id,
            after_seq,
            limit,
            sorted(exclude_kinds),
        )
        # A single turn row can be 100+ MB of JSON (a turn full of images); decode the
        # page off the event loop so a cold index fold never stalls other requests.
        return await asyncio.to_thread(lambda: [self._row_to_entry(r) for r in rows])

    async def read_seqs(self, session_id: UUID, seqs: list[int]) -> list[SessionLogEntry]:
        if not seqs:
            return []
        rows = await self._pool.fetch(
            """SELECT session_id, seq, kind, role, request_id, payload, ts
               FROM session_event_log
               WHERE session_id = $1 AND seq = ANY($2::bigint[])
               ORDER BY seq ASC""",
            session_id,
            sorted(set(seqs)),
        )
        return [self._row_to_entry(r) for r in rows]

    async def latest_seq(self, session_id: UUID) -> int:
        value = await self._pool.fetchval(
            "SELECT MAX(seq) FROM session_event_log WHERE session_id = $1",
            session_id,
        )
        if value is None:
            return 0
        return int(value)

    async def detect_conflicts(self, entries: list[SessionLogEntry]) -> list[int]:
        """Surface seqs whose STORED row differs from the candidate (INV-3c).

        Cold path only — never called from ``append``/``_flush``. One bounded,
        parameterized SELECT per session over exactly the candidate seqs (no N+1,
        no scan). ``append`` (ON CONFLICT DO NOTHING) is untouched, so the
        at-least-once retry stays an idempotent same-payload no-op.
        """
        if not entries:
            return []

        by_session: dict[UUID, dict[int, SessionLogEntry]] = {}
        for entry in entries:
            by_session.setdefault(entry.session_id, {})[entry.seq] = entry

        conflicts: list[int] = []
        for session_id, candidates in by_session.items():
            rows = await self._pool.fetch(
                """SELECT session_id, seq, kind, role, request_id, payload, ts
                   FROM session_event_log
                   WHERE session_id = $1 AND seq = ANY($2::bigint[])""",
                session_id,
                list(candidates),
            )
            for row in rows:
                stored = self._row_to_entry(row)
                candidate = candidates.get(stored.seq)
                if candidate is None:
                    continue
                if _log_entries_conflict(candidate, stored):
                    conflicts.append(stored.seq)
        conflicts.sort()
        return conflicts

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _entry_to_args(entry: SessionLogEntry) -> tuple:
        # Scrub the text columns too — kind/role/request_id are plain text and a
        # NUL there 500s the insert just like the JSONB payload does.
        return (
            entry.session_id,
            entry.seq,
            scrub_text(entry.kind),
            scrub_text(entry.role),
            scrub_text(entry.request_id),
            dumps_jsonb(entry.payload),
            entry.ts,
        )

    @staticmethod
    def _force_scrub_args(args: tuple) -> tuple:
        """Belt-and-suspenders rebuild of one row's args for the retry path: the
        payload ($6) is an already-serialized JSON string, force-scrubbed of any
        residual escaped NUL/surrogate; the text columns re-scrubbed."""
        session_id, seq, kind, role, request_id, payload, ts = args
        return (
            session_id,
            seq,
            scrub_text(kind),
            scrub_text(role),
            scrub_text(request_id),
            force_scrub_json(payload),
            ts,
        )

    @staticmethod
    def _row_to_entry(row: asyncpg.Record) -> SessionLogEntry:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return SessionLogEntry(
            session_id=row["session_id"],
            seq=row["seq"],
            kind=row["kind"],
            payload=payload,
            ts=row["ts"],
            role=row["role"],
            request_id=row["request_id"],
        )
