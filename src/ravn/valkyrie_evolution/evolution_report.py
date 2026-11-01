"""What a resident's learned tools did lately, in terms an operator can read.

The consolidation dream produces this report and carries it in its outcome, so
the Resident console needs no store of its own. Every number comes from the
learned-tool inventory.

The headline is chosen, not generated. Code writes one sentence per thing that
really happened, so each candidate is true by construction, and the decision
model picks the one that best sums the period up. A decision model answers with
typed judgments, so it cannot write the sentence, and it cannot invent a claim
either.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from ravn.ports.decision import ChoiceAnswer, ChoiceQuestion, DecisionPort
from ravn.valkyrie_evolution.tool_curation import (
    RETIRED_DEBUG_LEFTOVER,
    RETIRED_SUPERSEDED,
    ToolEntry,
)

_HEADLINE_PURPOSE = "resident.evolution_headline"
_HEADLINE_QUESTION = (
    "Which sentence best sums up how this resident's tools changed in the period described by "
    "`period`, for the person responsible for it? What needs that person comes before good news, "
    "and a large change before a small one."
)

KIND_BUILT = "built"
KIND_REPAIRED = "repaired"
KIND_PICKED_UP = "picked_up"
KIND_RETIRED = "retired"


def _stamp(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class EvolutionReporter:
    """Summarise a resident's tool library, and have the headline picked when asked."""

    def __init__(
        self,
        *,
        inventory: Callable[[], Sequence[ToolEntry]],
        valkyrie_id: str,
        window_seconds: float,
        decisions: DecisionPort | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._inventory = inventory
        self._valkyrie_id = valkyrie_id
        self._window = timedelta(seconds=window_seconds)
        self._decisions = decisions
        self._now = now

    async def report(self) -> dict[str, Any]:
        entries = list(self._inventory())
        since = self._now() - self._window
        active = [entry for entry in entries if not entry.retired]
        changes = sorted(
            (change for entry in entries for change in self._changes(entry, since)),
            key=lambda change: change["at"],
            reverse=True,
        )
        attention = [
            {
                "tool": entry.name,
                "runs": entry.lifecycle.run_count,
                "last_used_at": entry.lifecycle.last_used_at,
            }
            for entry in active
            if entry.lifecycle is not None
            and entry.lifecycle.run_count > 0
            and entry.lifecycle.success_count == 0
        ]
        most_used = sorted(
            (
                {
                    "tool": entry.name,
                    "runs": entry.lifecycle.run_count,
                    "successes": entry.lifecycle.success_count,
                    "last_used_at": entry.lifecycle.last_used_at,
                }
                for entry in active
                if entry.lifecycle is not None and entry.lifecycle.run_count > 0
            ),
            key=lambda tool: tool["runs"],
            reverse=True,
        )
        runs = sum(entry.lifecycle.run_count for entry in active if entry.lifecycle)
        successes = sum(entry.lifecycle.success_count for entry in active if entry.lifecycle)
        report: dict[str, Any] = {
            "window_seconds": self._window.total_seconds(),
            "tools": len(active),
            "retired": len(entries) - len(active),
            "from_peers": sum(1 for entry in active if self._peer(entry)),
            "runs": runs,
            "successes": successes,
            "most_used": most_used,
            "changes": changes,
            "attention": attention,
        }
        report["headline"] = await self._headline(report)
        return report

    def _peer(self, entry: ToolEntry) -> str:
        """The resident this tool came from; empty for one built here."""
        source = str(entry.artifact.provenance.get("source_valkyrie_id") or "").strip()
        return "" if source == self._valkyrie_id else source

    def _changes(self, entry: ToolEntry, since: datetime) -> list[dict[str, Any]]:
        lifecycle = entry.lifecycle
        runs = lifecycle.run_count if lifecycle else 0
        successes = lifecycle.success_count if lifecycle else 0
        common = {"tool": entry.name, "runs": runs, "successes": successes}
        changes: list[dict[str, Any]] = []
        created = _stamp(entry.artifact.created_at)
        if created is not None and created >= since:
            changes.append({**common, "at": entry.artifact.created_at, **self._arrival(entry)})
        archived = _stamp(lifecycle.archived_at) if lifecycle else None
        if (
            entry.retired
            and archived is not None
            and archived >= since
            and entry.retired_reason != RETIRED_DEBUG_LEFTOVER
        ):
            changes.append(
                {
                    **common,
                    "kind": KIND_RETIRED,
                    "at": lifecycle.archived_at,
                    "reason": entry.retired_reason,
                    "replaced_by": lifecycle.superseded_by,
                }
            )
        return changes

    def _arrival(self, entry: ToolEntry) -> dict[str, Any]:
        peer = self._peer(entry)
        if peer:
            return {"kind": KIND_PICKED_UP, "peer": peer}
        if entry.artifact.version > 1:
            return {"kind": KIND_REPAIRED, "version": entry.artifact.version}
        return {"kind": KIND_BUILT}

    async def _headline(self, report: Mapping[str, Any]) -> str:
        candidates = _candidates(report)
        if len(candidates) == 1 or self._decisions is None:
            return next(iter(candidates.values()))
        result = await self._decisions.decide(
            {
                "period": {
                    **{key: report[key] for key in ("window_seconds", "tools", "runs")},
                    "changes": Counter(change["kind"] for change in report["changes"]),
                }
            },
            {
                "headline": ChoiceQuestion(
                    instructions=_HEADLINE_QUESTION,
                    options=candidates,
                )
            },
            purpose=_HEADLINE_PURPOSE,
        )
        answer = result.answers["headline"]
        if not isinstance(answer, ChoiceAnswer):
            raise TypeError(f"decision port answered the headline with {answer!r}")
        return candidates[answer.choice]


def _candidates(report: Mapping[str, Any]) -> dict[str, str]:
    """One true sentence per thing that happened, keyed for the decision model."""
    changes = report["changes"]
    counts = {
        kind: sum(1 for change in changes if change["kind"] == kind)
        for kind in (KIND_BUILT, KIND_REPAIRED, KIND_PICKED_UP)
    }
    duplicates = sum(
        1
        for change in changes
        if change["kind"] == KIND_RETIRED and change["reason"] == RETIRED_SUPERSEDED
    )
    candidates: dict[str, str] = {}
    attention = report["attention"]
    if attention:
        worst = max(attention, key=lambda item: item["runs"])
        candidates["failing"] = (
            f"{worst['tool']} failed all {worst['runs']} of its runs and is still installed."
        )
    if duplicates:
        candidates["tidied"] = f"Simpler now: {_plural(duplicates, 'duplicate tool')} retired."
    if counts[KIND_PICKED_UP]:
        candidates["shared"] = (
            f"Picked up {_plural(counts[KIND_PICKED_UP], 'tool')} from other residents."
        )
    if counts[KIND_BUILT]:
        candidates["grew"] = f"Built {_plural(counts[KIND_BUILT], 'new tool')}."
    if counts[KIND_REPAIRED]:
        candidates["repaired"] = f"Repaired {_plural(counts[KIND_REPAIRED], 'tool')}."
    if candidates:
        return candidates
    if report["runs"]:
        return {
            "steady": (
                f"No changes to its tools: {report['successes']:,} of "
                f"{report['runs']:,} runs worked."
            )
        }
    return {"quiet": "No changes to its tools, and none have run yet."}
