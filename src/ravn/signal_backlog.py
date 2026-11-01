"""Fold a restored backlog of per-observation signal tasks into one window.

Before Environment signals went through the coalescing resident inbox, a
Kubernetes resident queued one full agent turn per warning event. A journal
written then can hold hundreds of them — the same pod recurring dozens of
times — each describing a cluster state that was hours old when it would have
run. Replaying them one turn each would keep the resident busy for days.

On restore they become one window instead: every task is grouped by the
object and severity its title names, with how often and over what span it was
queued, and the original journal records are written beside the journal so
nothing leaves the queue without a durable record. The window makes no claim
about what the observations mean; the resident judges that.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from niuu.observability import get_observability
from ravn.domain.models import (
    SIGNAL_BACKLOG_TRIGGER,
    SIGNAL_TRIGGER_PREFIX,
    SIGNAL_WINDOW_TRIGGERS,
    AgentTask,
)
from ravn.domain.valkyrie_contracts import (
    RESIDENT_ESCALATION_GUIDANCE,
    resident_outcome_template,
)


def is_per_signal_task(task: AgentTask) -> bool:
    """True for a task that carries exactly one Environment observation."""
    return (
        task.triggered_by.startswith(SIGNAL_TRIGGER_PREFIX)
        and task.triggered_by not in SIGNAL_WINDOW_TRIGGERS
    )


@dataclass(frozen=True)
class _Group:
    title: str
    count: int
    first_queued: datetime
    last_queued: datetime


def _groups(tasks: Sequence[AgentTask]) -> list[_Group]:
    by_title: dict[str, list[AgentTask]] = {}
    for task in tasks:
        by_title.setdefault(task.title, []).append(task)
    groups = [
        _Group(
            title=title,
            count=len(members),
            first_queued=min(member.created_at for member in members),
            last_queued=max(member.created_at for member in members),
        )
        for title, members in by_title.items()
    ]
    # Most repeated first, then oldest: the shown sample covers the bulk of the
    # backlog, and the overflow line accounts for the rest by count.
    groups.sort(key=lambda group: (-group.count, group.first_queued, group.title))
    return groups


def fold_signal_backlog(
    tasks: Sequence[AgentTask],
    *,
    record_ref: str,
    sample_groups: int,
    max_signal_refs: int,
) -> AgentTask:
    """Return one window task standing for every per-observation task given.

    ``record_ref`` names the durable copy of the original journal records.
    ``sample_groups`` bounds how many object lines the prompt renders, and
    ``max_signal_refs`` how many refs the outcome template lists; the counts
    always cover the whole backlog.
    """
    if not tasks:
        raise ValueError("a signal backlog window needs at least one task")
    groups = _groups(tasks)
    shown = groups[: max(0, sample_groups)]
    lines = [
        f"- `{group.title}` — {group.count} task(s), queued "
        f"{group.first_queued.isoformat()} to {group.last_queued.isoformat()}"
        for group in shown
    ]
    hidden = groups[len(shown) :]
    if hidden:
        lines.append(
            f"- …and {len(hidden)} more object(s) covering "
            f"{sum(group.count for group in hidden)} task(s), not shown; "
            f"`{record_ref}` holds every one."
        )
    objects = "\n".join(lines)
    first = min(task.created_at for task in tasks)
    last = max(task.created_at for task in tasks)
    signal_refs = [
        task.workflow_parent_event_id or task.task_id for task in tasks[: max(0, max_signal_refs)]
    ]
    outcome_template = resident_outcome_template(
        signal_refs=signal_refs,
        evidence_lines=["  - <evidence reference>"],
    )
    context = (
        f"# Restored signal backlog — {len(tasks)} observation(s) across "
        f"{len(groups)} object(s)\n\n"
        "These per-observation signal tasks were waiting in the queue when the resident "
        "restarted. They are folded into this one window instead of being replayed as one "
        "turn each. Every original task, with its full observation, is preserved in "
        f"`{record_ref}`.\n\n"
        f"They were queued between {first.isoformat()} and {last.isoformat()}. The objects "
        "may have changed since; the observations are presented without a predetermined "
        "interpretation.\n\n"
        "## Objects (most repeated first)\n\n"
        f"{objects}\n\n"
        "## Your task\n\n"
        "Decide what, if anything, these observations mean in this environment now. Gather "
        "current evidence with the available capabilities when that would improve the "
        "judgment, and ask the operator when their knowledge, intent, or authority is the "
        "best available way to progress. It is valid to conclude that no action is needed; "
        "ground whichever conclusion you reach in what you observe.\n\n"
        "## Required outcome\n\n"
        "Finish with exactly one `valkyrie.judgment.proposed` block:\n\n"
        "```text\n"
        f"{outcome_template}\n"
        "```\n\n"
        f"{RESIDENT_ESCALATION_GUIDANCE}\n"
    )
    task_ids = "\n".join(sorted(task.task_id for task in tasks))
    digest = hashlib.sha256(task_ids.encode()).hexdigest()[:16]
    head = min(tasks, key=lambda task: (task.priority, task.created_at))
    return AgentTask(
        task_id=f"task_{int(datetime.now(UTC).timestamp() * 1000):x}_{uuid.uuid4().hex[:8]}",
        title=f"Restored signal backlog: {len(tasks)} observation(s)",
        initiative_context=context,
        triggered_by=SIGNAL_BACKLOG_TRIGGER,
        output_mode=head.output_mode,
        persona=head.persona,
        # A critical observation in the backlog keeps the window at its priority.
        priority=head.priority,
        root_correlation_id=f"signal-backlog:{digest}",
        trace_context=get_observability().inject(),
    )
