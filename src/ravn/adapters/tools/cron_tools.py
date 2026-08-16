"""Cron scheduling tools — create, list, and delete recurring tasks (NIU-437).

These tools are registered when the drive loop is running with a ``CronJobStore``.
They let the agent manage its own recurring tasks at runtime without restarting
the daemon.

Tools:
- ``cron_create`` — Schedule a new recurring task
- ``cron_list``   — List scheduled tasks
- ``cron_delete`` — Remove a scheduled task

Delivery targets
----------------
- ``"local"``    — output saved to ``~/.ravn/cron/output/{job_id}/`` only
- ``"sleipnir"`` — published to the ODIN event backbone (ambient routing)
- ``"platform"`` — delivered via the configured surface channel (Telegram etc.)

Silent marker
-------------
Prefix ``context`` with ``[SILENT]`` to suppress all delivery regardless of
the ``delivery`` field.  Output is still saved locally.
"""

from __future__ import annotations

import logging
import re
from uuid import uuid4

from niuu.observability import get_observability
from ravn.adapters.triggers.cron import CronJobRecord, CronJobStore, parse_schedule
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort
from ravn.resident_text import texts_overlap, texts_similar

logger = logging.getLogger(__name__)


def _count_cron_refusal(reason: str) -> None:
    """A refused cron create is a health signal, not just a tool error —
    a resident repeatedly hitting the cap or restating jobs is thrashing."""
    get_observability().count(
        "ravn.resident.cron_refusals",
        attributes={"ravn.cron.refusal_reason": reason},
        description="Cron job creations refused by the backlog guard.",
    )


_PERMISSION = "cron:manage"

_SILENT_MARKER = "[SILENT]"

#: Case and task identifiers that appear verbatim in a job's context. They are
#: bookkeeping, not intent, and two jobs watching the same case for different
#: reasons should still be allowed.
_CASE_REF = re.compile(r"\b(?:task|case|resident-case|resident-home)[-_][0-9a-z_-]+", re.I)


def _comparable_context(context: str) -> str:
    """Reduce a job context to the part that expresses what it wants done."""
    text = context.strip()
    if text.startswith(_SILENT_MARKER):
        text = text[len(_SILENT_MARKER) :]
    return _CASE_REF.sub(" ", text)


_VALID_DELIVERIES = frozenset({"local", "sleipnir", "platform"})

_SCHEDULE_HELP = (
    "Cron expression (e.g. '0 9 * * *'), "
    "natural language (e.g. 'every 30m', 'daily at 09:00'), "
    "bare interval (e.g. '30m', '2h'), "
    "or ISO timestamp for one-shot execution."
)


def _format_job(record: CronJobRecord) -> str:
    status = "enabled" if record.enabled else "disabled"
    return (
        f"[{record.job_id}] {record.name!r}  ({status})\n"
        f"  schedule:  {record.schedule}\n"
        f"  delivery:  {record.delivery}\n"
        f"  priority:  {record.priority}\n"
        f"  persona:   {record.persona or '(default)'}\n"
        f"  context:   {record.context[:120]}{'…' if len(record.context) > 120 else ''}\n"
        f"  created:   {record.created_at}"
    )


# ---------------------------------------------------------------------------
# cron_create
# ---------------------------------------------------------------------------


class CronCreateTool(ToolPort):
    """Schedule a new recurring task.

    The task fires on the given schedule and runs autonomously in the drive
    loop.  Output is saved to ``~/.ravn/cron/output/{job_id}/`` and optionally
    delivered via the configured channel.

    Two guards bound what an agent can accumulate here, because self-scheduling
    is the one tool whose output is more work for the same agent. A resident
    that cannot close a case reaches for it every turn, and the jobs it creates
    outlive the reasoning that asked for them.
    """

    def __init__(
        self,
        store: CronJobStore,
        *,
        max_jobs: int = 0,
        duplicate_similarity: float = 0.0,
    ) -> None:
        self._store = store
        self._max_jobs = max(0, max_jobs)
        self._duplicate_similarity = min(1.0, max(0.0, duplicate_similarity))

    @property
    def name(self) -> str:
        return "cron_create"

    @property
    def description(self) -> str:
        return (
            "Schedule a recurring task. "
            "The task runs autonomously on the given schedule. "
            "Output is always saved locally; use delivery='sleipnir' or 'platform' "
            "to also route it through the event backbone or surface channel. "
            "Prefix context with [SILENT] to suppress all delivery."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short human-readable name for the job (e.g. 'daily-standup').",
                },
                "schedule": {
                    "type": "string",
                    "description": _SCHEDULE_HELP,
                },
                "context": {
                    "type": "string",
                    "description": (
                        "The task prompt given to the agent when the job fires. "
                        "Prefix with [SILENT] to suppress delivery."
                    ),
                },
                "delivery": {
                    "type": "string",
                    "enum": ["local", "sleipnir", "platform"],
                    "description": (
                        "Where to deliver the output. "
                        "'local' = save to disk only (default). "
                        "'sleipnir' = publish to ODIN event backbone. "
                        "'platform' = deliver via configured surface channel."
                    ),
                },
                "persona": {
                    "type": "string",
                    "description": "Persona for this job (uses daemon default if omitted).",
                },
                "priority": {
                    "type": "integer",
                    "description": "Task priority — lower value = higher priority (default: 10).",
                },
            },
            "required": ["name", "schedule", "context"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        name = input.get("name", "").strip()
        if not name:
            return ToolResult(tool_call_id="", content="'name' is required.", is_error=True)

        schedule = input.get("schedule", "").strip()
        if not schedule:
            return ToolResult(tool_call_id="", content="'schedule' is required.", is_error=True)

        context = input.get("context", "").strip()
        if not context:
            return ToolResult(tool_call_id="", content="'context' is required.", is_error=True)

        delivery = input.get("delivery", "local")
        if delivery not in _VALID_DELIVERIES:
            return ToolResult(
                tool_call_id="",
                content=f"Invalid delivery {delivery!r}. Valid: {sorted(_VALID_DELIVERIES)}",
                is_error=True,
            )

        # Validate schedule by parsing it
        canonical = parse_schedule(schedule)
        if not (
            canonical.startswith("every:")
            or canonical.startswith("once:")
            or len(canonical.split()) == 5
        ):
            return ToolResult(
                tool_call_id="",
                content=f"Could not parse schedule {schedule!r}. {_SCHEDULE_HELP}",
                is_error=True,
            )

        persona = input.get("persona") or None
        priority = int(input.get("priority", 10))
        normalized_name = name.casefold()
        normalized_context = " ".join(context.split()).casefold()
        for existing in self._store.list():
            same_name = existing.name.strip().casefold() == normalized_name
            same_context = " ".join(existing.context.split()).casefold() == normalized_context
            if not (same_name or same_context):
                continue
            same_job = (
                parse_schedule(existing.schedule) == canonical
                and same_context
                and existing.delivery == delivery
                and existing.persona == persona
                and existing.priority == priority
                and existing.enabled
            )
            if same_job:
                return ToolResult(
                    tool_call_id="",
                    content=(
                        "An equivalent cron job already exists; no new job was created.\n\n"
                        f"{_format_job(existing)}\n\nCanonical schedule form: {canonical}"
                    ),
                )
            conflict = "name" if same_name else "context"
            return ToolResult(
                tool_call_id="",
                content=(
                    f"Cron job conflicts with existing {conflict} on {existing.job_id!r}. "
                    "Delete that job explicitly before replacing its schedule or delivery."
                ),
                is_error=True,
            )

        restated = self._find_restatement(name, context)
        if restated is not None:
            _count_cron_refusal("duplicate")
            return ToolResult(
                tool_call_id="",
                content=(
                    "This job restates an existing one in different words; no new job "
                    "was created. Scheduling a second check does not advance a question "
                    "the first one is already asking — read that job's output, and if it "
                    "is not answering the question, delete it and create one job that "
                    f"does.\n\n{_format_job(restated)}"
                ),
                is_error=True,
            )

        enabled_jobs = [record for record in self._store.list() if record.enabled]
        if self._max_jobs and len(enabled_jobs) >= self._max_jobs:
            _count_cron_refusal("max_jobs")
            listing = "\n".join(
                f"  [{record.job_id}] {record.name!r} — {record.schedule}"
                for record in enabled_jobs
            )
            return ToolResult(
                tool_call_id="",
                content=(
                    f"Cron job limit reached: {len(enabled_jobs)} of {self._max_jobs} "
                    "enabled jobs. Delete one with cron_delete before scheduling "
                    f"another.\n\n{listing}"
                ),
                is_error=True,
            )

        job_id = uuid4().hex

        record = CronJobRecord(
            job_id=job_id,
            name=name,
            schedule=schedule,
            context=context,
            delivery=delivery,
            persona=persona,
            priority=priority,
        )

        try:
            self._store.create(record)
        except Exception as exc:
            logger.warning("cron_create: store error: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Failed to save job: {exc}",
                is_error=True,
            )

        logger.info("cron_create: created job %r (%s) schedule=%r", name, job_id, schedule)
        return ToolResult(
            tool_call_id="",
            content=(
                f"Created cron job {job_id!r}.\n\n{_format_job(record)}\n\n"
                f"Canonical schedule form: {canonical}"
            ),
        )

    def _find_restatement(self, name: str, context: str) -> CronJobRecord | None:
        """Return an enabled job that asks the same question in different words.

        Compares the intent, not the string. Contexts are matched by overlap
        coefficient because one is often an elaboration of another ("check etcd
        pod logs for latency warnings" inside "check etcd pod logs for latency
        warnings and apiserver logs for connection errors"); names by Jaccard
        because both sides are short labels of the same kind.

        The delivery marker and the case identifier are stripped first. Both
        recur across unrelated jobs, and a shared ``[SILENT]`` prefix or case ID
        was enough to pull genuinely different jobs over the threshold.
        """
        if self._duplicate_similarity <= 0.0:
            return None
        new_context = _comparable_context(context)
        for existing in self._store.list():
            if not existing.enabled:
                continue
            if texts_overlap(
                new_context,
                _comparable_context(existing.context),
                threshold=self._duplicate_similarity,
            ):
                return existing
            if texts_similar(name, existing.name, threshold=self._duplicate_similarity):
                return existing
        return None


# ---------------------------------------------------------------------------
# cron_list
# ---------------------------------------------------------------------------


class CronListTool(ToolPort):
    """List all scheduled cron jobs."""

    def __init__(self, store: CronJobStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "cron_list"

    @property
    def description(self) -> str:
        return (
            "List all scheduled cron jobs managed by this agent. "
            "Shows job ID, name, schedule, delivery target, and context preview."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "enabled_only": {
                    "type": "boolean",
                    "description": "When true, only return enabled jobs (default: false).",
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:
        enabled_only = bool(input.get("enabled_only", False))
        jobs = self._store.list()

        if enabled_only:
            jobs = [j for j in jobs if j.enabled]

        if not jobs:
            return ToolResult(tool_call_id="", content="No cron jobs scheduled.")

        lines = [f"Cron jobs ({len(jobs)} total):\n"]
        for record in sorted(jobs, key=lambda r: r.created_at):
            lines.append(_format_job(record))
            lines.append("")

        return ToolResult(tool_call_id="", content="\n".join(lines).strip())


# ---------------------------------------------------------------------------
# cron_delete
# ---------------------------------------------------------------------------


class CronDeleteTool(ToolPort):
    """Remove a scheduled cron job."""

    def __init__(self, store: CronJobStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "cron_delete"

    @property
    def description(self) -> str:
        return "Remove a scheduled cron job by its job ID. Use cron_list to find job IDs."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The job ID to remove (from cron_list output).",
                },
            },
            "required": ["job_id"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        job_id = input.get("job_id", "").strip()
        if not job_id:
            return ToolResult(tool_call_id="", content="'job_id' is required.", is_error=True)

        record = self._store.get(job_id)
        if record is None:
            return ToolResult(
                tool_call_id="",
                content=f"Job {job_id!r} not found. Use cron_list to see available jobs.",
                is_error=True,
            )

        removed = self._store.delete(job_id)
        if not removed:
            return ToolResult(
                tool_call_id="",
                content=f"Failed to remove job {job_id!r}.",
                is_error=True,
            )

        logger.info("cron_delete: removed job %r (%s)", record.name, job_id)
        return ToolResult(
            tool_call_id="",
            content=f"Removed cron job {job_id!r} ({record.name!r}).",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_cron_tools(
    store: CronJobStore,
    *,
    max_jobs: int = 0,
    duplicate_similarity: float = 0.0,
) -> list[ToolPort]:
    """Build the list of cron management tools backed by *store*."""
    return [
        CronCreateTool(
            store,
            max_jobs=max_jobs,
            duplicate_similarity=duplicate_similarity,
        ),
        CronListTool(store),
        CronDeleteTool(store),
    ]
