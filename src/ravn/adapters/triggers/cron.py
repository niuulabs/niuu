"""CronTrigger — fires AgentTasks on a schedule.

Supports:
- Standard cron expressions: ``"0 8 * * *"``
- Natural language: ``"every 30m"``, ``"daily at 08:00"``
- One-shot ISO timestamps: ``"2026-04-07T08:00:00"``
- Interval shorthand: ``"30m"``, ``"2h"``

Config-defined jobs are passed directly as ``CronJob`` objects.
Runtime jobs are stored in ``CronJobStore`` (``~/.ravn/cron/jobs.json``, 0600)
and written by the ``cron_create`` / ``cron_delete`` tools.

State is persisted to ``~/.ravn/daemon/cron_state.json`` so last-fired
timestamps survive restarts.  A file lock at ``~/.ravn/daemon/cron.lock``
prevents duplicate firings when multiple daemon instances are (accidentally)
running.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_STATE_PATH = Path.home() / ".ravn" / "daemon" / "cron_state.json"
_DEFAULT_LOCK_PATH = Path.home() / ".ravn" / "daemon" / "cron.lock"
_DEFAULT_JOBS_PATH = Path.home() / ".ravn" / "cron" / "jobs.json"
_OUTPUT_BASE = Path.home() / ".ravn" / "cron" / "output"

_TICK_SECONDS = 30
_SILENT_MARKER = "[SILENT]"

_NATURAL_EVERY_RE = re.compile(
    r"every\s+(\d+)\s*(s(?:ec(?:ond)?s?)?|m(?:in(?:ute)?s?)?|h(?:our)?s?)",
    re.IGNORECASE,
)
_DAILY_AT_RE = re.compile(r"daily\s+at\s+(\d{1,2}):(\d{2})", re.IGNORECASE)
# Bare interval: "30m", "2h", "45s"
_BARE_INTERVAL_RE = re.compile(
    r"^(\d+)\s*(s(?:ec(?:ond)?s?)?|m(?:in(?:ute)?s?)?|h(?:our)?s?)$",
    re.IGNORECASE,
)

_DELIVERY_TO_OUTPUT_MODE: dict[str, OutputMode] = {
    "local": OutputMode.SILENT,
    "sleipnir": OutputMode.AMBIENT,
    "platform": OutputMode.SURFACE,
}


def _task_deadline(canonical: str, now: datetime) -> datetime | None:
    """Expire interval work when its next occurrence becomes due."""
    if canonical.startswith("every:"):
        return now + timedelta(seconds=int(canonical[6:]))
    return None


# ---------------------------------------------------------------------------
# Cron expression parser (minute-level precision)
# ---------------------------------------------------------------------------


def _field_matches(value: int, spec: str) -> bool:
    """Return True if *value* matches the cron field *spec*.

    :raises ValueError: *spec* has a step of zero (``*/0``, ``1-5/0``) —
        never meaningful, and ``value % 0``/``(value - start) % 0`` would
        otherwise raise ``ZeroDivisionError`` instead of a clean, callable
        error ``trigger_validation.validate_trigger_spec`` can turn into a
        422 at trigger-creation time.
    """
    if spec == "*":
        return True
    for part in spec.split(","):
        part = part.strip()
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"cron field step must be positive, got {part!r}")
            if base == "*":
                if value % step == 0:
                    return True
                continue
            if "-" in base:
                # Range-with-step, e.g. "1-5/2": every `step`th value in
                # [lo, hi] starting at lo — standard cron syntax. Must be
                # checked before the bare `int(base)` parse below, which
                # would otherwise raise ValueError on the "1-5" literal.
                lo_str, hi_str = base.split("-", 1)
                lo, hi = int(lo_str), int(hi_str)
                if lo <= value <= hi and (value - lo) % step == 0:
                    return True
                continue
            start = int(base)
            if value >= start and (value - start) % step == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        else:
            if value == int(part):
                return True
    return False


def _cron_matches(expr: str, dt: datetime) -> bool:
    """Return True if cron expression *expr* matches *dt* (minute-level)."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    return (
        _field_matches(dt.minute, minute)
        and _field_matches(dt.hour, hour)
        and _field_matches(dt.day, dom)
        and _field_matches(dt.month, month)
        and _field_matches((dt.weekday() + 1) % 7, dow)  # convert to cron: 0=Sun
    )


class CronExpressionError(ValueError):
    """A 5-field cron expression has a field ``_field_matches`` cannot
    evaluate, an out-of-range value, a non-positive step, or otherwise
    cannot be scheduled at all."""


#: One regex per cron field position, capturing the numeric base/range/step
#: parts so out-of-range values ("99 * * * *") are rejected, not just
#: malformed ones ("abc * * * *"). ``_field_matches`` only supports digits,
#: "*", ranges, and steps — no day-of-week/month names like "MON" or "JAN".
_CRON_FIELD_RE = re.compile(r"^(\*|\d+)(?:-(\d+))?(?:/(\d+))?$")
_CRON_FIELD_RANGES = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 6),
)
CRON_FIELD_COUNT = len(_CRON_FIELD_RANGES)


def validate_cron_fields(canonical: str, *, spec: str | None = None) -> None:
    """Raise :class:`CronExpressionError` when a 5-field cron expression
    (already canonicalised by :func:`parse_schedule` — do not pass ``every:``
    / ``once:`` forms here, they need no field validation) has a field this
    module's own matcher cannot evaluate, an out-of-range value, or a
    non-positive step — then exercises the real matcher (:func:`_cron_matches`)
    so any future divergence between this check and the matcher is caught
    here too, not at the first tick the scheduler evaluates it.

    Shared by ``ravn.api.trigger_validation`` (``POST /api/v1/ravn/triggers``)
    and ``ravn.adapters.tools.cron_tools`` (the agent's own ``cron_create``
    tool) so a job persisted through EITHER path is guaranteed schedulable —
    one that is not raises inside :func:`_field_matches` every tick
    :meth:`CronTrigger._is_due_canonical` evaluates it (e.g. a day-of-week
    name like ``MON-FRI``, which this parser does not support).

    ``spec`` is the original, pre-canonicalisation string to quote in error
    messages when the caller has one (natural-language forms canonicalise to
    something less recognisable); it defaults to ``canonical``.
    """
    display = spec if spec is not None else canonical
    fields = canonical.split()
    if len(fields) != CRON_FIELD_COUNT:
        raise CronExpressionError(
            f"cron expression {display!r} does not have {CRON_FIELD_COUNT} fields"
        )
    for cron_field, (label, lo, hi) in zip(fields, _CRON_FIELD_RANGES, strict=True):
        for part in cron_field.split(","):
            match = _CRON_FIELD_RE.fullmatch(part)
            if match is None:
                raise CronExpressionError(
                    f"cron expression {display!r} has an invalid field {cron_field!r}"
                )
            base, range_end, step = match.groups()
            if step is not None and int(step) <= 0:
                raise CronExpressionError(
                    f"cron expression {display!r} has a non-positive step {step!r} in field "
                    f"{cron_field!r} — a step of 0 never matches and divides by zero in "
                    "the scheduler"
                )
            for value in (base, range_end):
                if value is None or value == "*":
                    continue
                if not lo <= int(value) <= hi:
                    raise CronExpressionError(
                        f"cron expression {display!r} has {label} value {value!r} outside "
                        f"{lo}-{hi} in field {cron_field!r}"
                    )
    try:
        _cron_matches(canonical, datetime.now(UTC))
    except Exception as exc:
        raise CronExpressionError(f"cron expression {display!r} is not schedulable: {exc}") from exc


# ---------------------------------------------------------------------------
# Schedule type detection
# ---------------------------------------------------------------------------


def parse_schedule(schedule: str) -> str:
    """Normalise *schedule* to a canonical cron expression or special form.

    Returns one of:
    - A 5-field cron string (``"0 8 * * *"``)
    - ``"every:{seconds}"`` for fixed-interval schedules
    - ``"once:{iso}"`` for one-shot timestamps
    """
    schedule = schedule.strip()

    # Natural: "every 30m", "every 5s", "every 2h"
    m = _NATURAL_EVERY_RE.fullmatch(schedule)
    if m:
        count = int(m.group(1))
        unit = m.group(2).lower()[0]
        multipliers = {"s": 1, "m": 60, "h": 3600}
        seconds = count * multipliers.get(unit, 60)
        return f"every:{seconds}"

    # Bare interval: "30m", "2h", "45s"
    m = _BARE_INTERVAL_RE.fullmatch(schedule)
    if m:
        count = int(m.group(1))
        unit = m.group(2).lower()[0]
        multipliers = {"s": 1, "m": 60, "h": 3600}
        seconds = count * multipliers.get(unit, 60)
        return f"every:{seconds}"

    # Natural: "daily at HH:MM"
    m = _DAILY_AT_RE.fullmatch(schedule)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        return f"{mm} {hh} * * *"

    # ISO timestamp (one-shot)
    try:
        dt = datetime.fromisoformat(schedule)
        return f"once:{dt.isoformat()}"
    except ValueError:
        pass

    # Assume it's already a cron expression
    return schedule


# ---------------------------------------------------------------------------
# CronJob descriptor (config-defined)
# ---------------------------------------------------------------------------


class CronJob:
    """A single scheduled job within a CronTrigger."""

    def __init__(
        self,
        name: str,
        schedule: str,
        context: str,
        output_mode: OutputMode = OutputMode.SILENT,
        persona: str | None = None,
        priority: int = 10,
    ) -> None:
        self.name = name
        self.raw_schedule = schedule
        self.canonical = parse_schedule(schedule)
        self.context = context
        self.output_mode = output_mode
        self.persona = persona
        self.priority = priority


# ---------------------------------------------------------------------------
# CronJobRecord + CronJobStore (runtime-defined, persisted to disk)
# ---------------------------------------------------------------------------


@dataclass
class CronJobRecord:
    """A runtime-defined cron job persisted to ``~/.ravn/cron/jobs.json``.

    Created and managed by the ``cron_create`` / ``cron_list`` / ``cron_delete``
    tools.  The ``job_id`` is a UUID hex string, unique across all store jobs.

    ``delivery`` controls how task output is routed:
    - ``"local"``    — output saved to disk only (silent)
    - ``"sleipnir"`` — published to the ODIN event backbone (ambient)
    - ``"platform"`` — delivered via the configured surface channel (surface)

    Prefixing ``context`` with ``[SILENT]`` overrides ``delivery`` to local-only
    regardless of the ``delivery`` field value.
    """

    job_id: str
    name: str
    schedule: str
    context: str
    delivery: str = "local"  # "local" | "sleipnir" | "platform"
    persona: str | None = None
    priority: int = 10
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CronJobRecord:
        return cls(
            job_id=d["job_id"],
            name=d["name"],
            schedule=d["schedule"],
            context=d["context"],
            delivery=d.get("delivery", "local"),
            persona=d.get("persona"),
            priority=int(d.get("priority", 10)),
            created_at=d.get("created_at", datetime.now(UTC).isoformat()),
            enabled=bool(d.get("enabled", True)),
        )


class CronJobStore:
    """Persistent store for runtime cron jobs.

    Stores jobs as a JSON list at ``jobs_path`` (default:
    ``~/.ravn/cron/jobs.json``).  The file is created with 0600 permissions
    so that secrets in ``context`` are not world-readable.

    All operations are synchronous (file I/O is small; no async needed).
    """

    def __init__(self, jobs_path: Path = _DEFAULT_JOBS_PATH) -> None:
        self._path = jobs_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list(self) -> list[CronJobRecord]:
        """Return all job records (enabled and disabled)."""
        return [CronJobRecord.from_dict(d) for d in self._load()]

    def get(self, job_id: str) -> CronJobRecord | None:
        """Return the record for *job_id*, or None if not found."""
        for d in self._load():
            if d.get("job_id") == job_id:
                return CronJobRecord.from_dict(d)
        return None

    def create(self, record: CronJobRecord) -> None:
        """Persist *record*.  Raises ``ValueError`` on duplicate job_id."""
        records = self._load()
        if any(r.get("job_id") == record.job_id for r in records):
            raise ValueError(f"cron_store: job_id {record.job_id!r} already exists")
        records.append(record.to_dict())
        self._save(records)

    def delete(self, job_id: str) -> bool:
        """Remove the job with *job_id*.  Returns True if found and removed."""
        records = self._load()
        filtered = [r for r in records if r.get("job_id") != job_id]
        if len(filtered) == len(records):
            return False
        self._save(filtered)
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return []

    def _save(self, records: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(records, indent=2)
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)
        except Exception:
            with suppress(OSError):
                os.unlink(tmp_path)
            raise


# ---------------------------------------------------------------------------
# CronTrigger
# ---------------------------------------------------------------------------


class CronTrigger(TriggerPort):
    """Trigger that fires tasks on a cron schedule.

    Combines two job sources:
    - Config-defined ``CronJob`` objects passed at construction time.
    - Runtime jobs from ``CronJobStore`` (written by cron tools).

    All jobs share the same state file so that last-fired timestamps
    survive daemon restarts.  A file lock at ``lock_path`` prevents
    duplicate firings when multiple daemon instances are (accidentally)
    running.
    """

    def __init__(
        self,
        jobs: list[CronJob],
        state_path: Path = _DEFAULT_STATE_PATH,
        lock_path: Path = _DEFAULT_LOCK_PATH,
        tick_seconds: float = _TICK_SECONDS,
        store: CronJobStore | None = None,
    ) -> None:
        self._jobs = jobs
        self._state_path = state_path
        self._lock_path = lock_path
        self._tick = tick_seconds
        self._store = store
        self._counter = 0
        # Job names/ids already logged as quarantined — evaluating a bad
        # schedule fails identically every tick, so without this the same
        # error would flood the log every _tick_seconds forever instead of
        # once per process lifetime. Cleared implicitly on restart, so a
        # fixed or deleted job stops being quarantined and a still-broken
        # one is reported again (not silently forgotten).
        self._quarantined: set[str] = set()

    @property
    def name(self) -> str:
        return "cron"

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, str]:
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text())
        except Exception:
            return {}

    def _save_state(self, state: dict[str, str]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(state))

    # ------------------------------------------------------------------
    # Lock
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> IO[str] | None:
        """Try to acquire the cron lock.  Returns fd on success, None on failure."""
        fd = None
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = open(self._lock_path, "w")  # noqa: SIM115
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (OSError, BlockingIOError):
            if fd is not None:
                fd.close()
            return None

    # ------------------------------------------------------------------
    # Task ID generation
    # ------------------------------------------------------------------

    def _make_task_id(self) -> str:
        self._counter += 1
        hex_ts = hex(int(time.time() * 1000))[2:]
        return f"task_{hex_ts}_{self._counter:04d}"

    # ------------------------------------------------------------------
    # Due detection
    # ------------------------------------------------------------------

    def _is_due_canonical(
        self, canonical: str, state_key: str, now: datetime, state: dict[str, str]
    ) -> bool:
        """Return True if the canonical schedule form is due to fire."""
        if canonical.startswith("once:"):
            iso = canonical[5:]
            try:
                fire_at = datetime.fromisoformat(iso)
            except ValueError:
                return False
            if now < fire_at:
                return False
            # Only fire once — marked by presence of key in state
            return state_key not in state

        if canonical.startswith("every:"):
            interval = int(canonical[6:])
            last_str = state.get(state_key)
            if last_str is None:
                return True
            last = datetime.fromisoformat(last_str)
            return (now - last).total_seconds() >= interval

        # Standard cron expression
        last_str = state.get(state_key)
        if last_str is not None:
            last = datetime.fromisoformat(last_str)
            # Don't fire more than once per minute
            if (now - last).total_seconds() < 60:
                return False
        return _cron_matches(canonical, now)

    def _is_due(self, job: CronJob, now: datetime, state: dict[str, str]) -> bool:
        return self._is_due_canonical(job.canonical, job.name, now, state)

    def _quarantine(self, job_id: str, name: str, schedule: str, exc: Exception) -> None:
        """Log a job whose schedule cannot be evaluated and skip it for this
        tick, instead of letting the exception propagate out of ``run()``.

        A malformed schedule (e.g. a day-of-week name like ``MON-FRI``,
        which ``_field_matches`` does not support) fails identically on
        every tick and every restart — validated out at creation time going
        forward (``ravn.api.trigger_validation`` / ``cron_tools
        .CronCreateTool``), but an already-persisted job predating that
        check must not crash-loop the whole resident process (``_trigger_
        watcher`` deliberately does not catch trigger-source failures — see
        its docstring — so an uncaught error here would end the process).
        Logged once per job per process lifetime, not every tick.
        """
        if job_id in self._quarantined:
            return
        self._quarantined.add(job_id)
        logger.error(
            "cron: quarantining job %r (%s) — schedule %r is not schedulable: %s. "
            "It will not fire until fixed or removed (cron_delete for a "
            "runtime job; the config for a config-defined one).",
            name,
            job_id,
            schedule,
            exc,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        lock_fd = self._acquire_lock()
        if lock_fd is None:
            logger.warning("cron: another instance holds the lock — skipping")
            return

        with lock_fd:
            try:
                while True:
                    now = datetime.now(UTC)
                    state = self._load_state()
                    changed = False

                    # -- Config-defined jobs --
                    for job in self._jobs:
                        try:
                            is_due = self._is_due(job, now, state)
                        except Exception as exc:
                            self._quarantine("config", job.name, job.raw_schedule, exc)
                            continue
                        if not is_due:
                            continue

                        task_id = self._make_task_id()
                        task = AgentTask(
                            task_id=task_id,
                            title=job.name,
                            initiative_context=job.context,
                            triggered_by=f"cron:{job.name}",
                            output_mode=job.output_mode,
                            persona=job.persona,
                            priority=job.priority,
                            deadline=_task_deadline(job.canonical, now),
                        )
                        logger.info("cron: firing job %r (task_id=%s)", job.name, task_id)
                        await enqueue(task)
                        state[job.name] = now.isoformat()
                        changed = True

                    # -- Store-defined (runtime) jobs --
                    if self._store is not None:
                        for record in self._store.list():
                            if not record.enabled:
                                continue
                            canonical = parse_schedule(record.schedule)
                            try:
                                is_due = self._is_due_canonical(
                                    canonical, record.job_id, now, state
                                )
                            except Exception as exc:
                                self._quarantine(record.job_id, record.name, record.schedule, exc)
                                continue
                            if not is_due:
                                continue

                            context = record.context
                            output_mode = _DELIVERY_TO_OUTPUT_MODE.get(
                                record.delivery, OutputMode.SILENT
                            )
                            # [SILENT] marker overrides delivery to local-only
                            if context.startswith(_SILENT_MARKER):
                                context = context[len(_SILENT_MARKER) :].strip()
                                output_mode = OutputMode.SILENT

                            timestamp_str = now.strftime("%Y%m%dT%H%M%S")
                            output_path = _OUTPUT_BASE / record.job_id / f"{timestamp_str}.md"

                            task_id = self._make_task_id()
                            task = AgentTask(
                                task_id=task_id,
                                title=record.name,
                                initiative_context=context,
                                triggered_by=f"cron:{record.job_id}",
                                output_mode=output_mode,
                                persona=record.persona or None,
                                priority=record.priority,
                                deadline=_task_deadline(canonical, now),
                                output_path=output_path,
                            )
                            logger.info(
                                "cron: firing store job %r/%s (task_id=%s)",
                                record.name,
                                record.job_id,
                                task_id,
                            )
                            await enqueue(task)
                            state[record.job_id] = now.isoformat()
                            changed = True

                    if changed:
                        self._save_state(state)

                    await asyncio.sleep(self._tick)
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_cron_trigger(
    jobs: list[CronJob] | None = None,
    jobs_path: Path = _DEFAULT_JOBS_PATH,
    state_path: Path = _DEFAULT_STATE_PATH,
    lock_path: Path = _DEFAULT_LOCK_PATH,
    tick_seconds: float = _TICK_SECONDS,
) -> tuple[CronTrigger, CronJobStore]:
    """Build a ``CronTrigger`` + ``CronJobStore`` pair wired together.

    Returns both so callers can pass the store to ``build_cron_tools()``.
    """
    store = CronJobStore(jobs_path=jobs_path)
    trigger = CronTrigger(
        jobs=jobs or [],
        state_path=state_path,
        lock_path=lock_path,
        tick_seconds=tick_seconds,
        store=store,
    )
    return trigger, store
