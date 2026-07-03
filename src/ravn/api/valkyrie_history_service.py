"""Durable Valkyrie history: ingest, lineage, inbox escalation, briefs.

This service sits between the telemetry stream and the
:class:`ravn.ports.valkyrie_history.ValkyrieHistoryStore`:

* every judgment / action / signal event is persisted so decisions survive
  restarts and can be browsed with full rationale and evidence;
* judgments whose ``action_authority`` demands a human land in the ODIN
  review inbox as ``court_escalation`` items (the decide → apply loop the
  residents already implement);
* action results are stamped back onto the originating decision so the
  dashboard can show what a decision actually led to;
* a periodic morning brief per environment summarises what the resident
  saw, decided, and learned, filed as an acknowledgeable inbox item.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ravn.domain.valkyrie_history import (
    action_record_from_event,
    decision_record_from_event,
    decision_requires_review,
    signal_record_from_event,
)
from ravn.odin.review import ReviewItem, ReviewKind
from ravn.ports.valkyrie_history import ValkyrieHistoryStore
from sleipnir.domain.events import SleipnirEvent

logger = logging.getLogger(__name__)

#: operational_state values a resident emits when exercising a learned skill.
LEARNED_SKILL_STATES = (
    "using_adopted_learning",
    "adopted_learning_failed",
    "adopted_learning_regressed",
)
_SKILL_STATS_SCAN_LIMIT = 1_000
_BRIEF_SCAN_LIMIT = 500
_LINEAGE_REVIEW_SCAN_LIMIT = 500


def _now() -> datetime:
    return datetime.now(UTC)


def _event_dict(event: SleipnirEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, SleipnirEvent):
        return event.to_dict()
    return dict(event)


def review_item_for_judgment(record: dict[str, Any]) -> ReviewItem:
    """Project a review-required judgment onto the unified review envelope.

    The evidence carries the same ``action`` contract the ODIN court files,
    so the resident-side dispatcher can execute the approved action without
    any new apply path.
    """
    decision_id = str(record["decisionId"])
    tier = str(record.get("tier") or "ambient")
    capability = str(record.get("actionCapability") or "").strip()
    recommended = str(record.get("recommendedAction") or "").strip()
    title_action = recommended or capability or str(record.get("operationalState") or "judgment")
    return ReviewItem(
        item_id=f"review:{ReviewKind.COURT_ESCALATION.value}:{decision_id}",
        kind=ReviewKind.COURT_ESCALATION.value,
        requested_action="execute_action",
        environment_id=str(record.get("environmentId") or "unknown"),
        valkyrie_id=str(record.get("valkyrieId") or ""),
        title=f"Decision needs review: {title_action}",
        summary=str(record.get("rationale") or record.get("summary") or title_action),
        audience="valkyrie" if record.get("valkyrieId") else "environment",
        risk_class="high" if tier == "urgent" else "medium",
        urgency={"urgent": 0.9, "present": 0.7}.get(tier, 0.5),
        dedupe_key=f"judgment:{decision_id}",
        evidence={
            "judgment_event_id": decision_id,
            "tier": tier,
            "confidence": float(record.get("confidence") or 0.0),
            "operational_state": str(record.get("operationalState") or ""),
            "rationale": str(record.get("rationale") or ""),
            "recommended_action": recommended,
            "action_authority": str(record.get("actionAuthority") or ""),
            "signal_refs": list(record.get("signalRefs") or []),
            "judgment_evidence": list(record.get("evidence") or []),
            "action": {
                "action_id": decision_id,
                "capability": capability or recommended or "unknown",
                "valkyrie_id": str(record.get("valkyrieId") or ""),
                "target": {},
                "dry_run": True,
            },
        },
        requested_by=str(record.get("source") or "ravn:valkyrie"),
        correlation_id=str(record.get("correlationId") or decision_id),
    )


class ValkyrieHistoryService:
    """Persist Valkyrie activity and close the decision → review → outcome loop."""

    def __init__(
        self,
        store: ValkyrieHistoryStore,
        *,
        review_service: Any | None = None,
    ) -> None:
        self._store = store
        self._review_service = review_service

    @property
    def store(self) -> ValkyrieHistoryStore:
        return self._store

    async def ingest_event(self, event: SleipnirEvent | dict[str, Any]) -> None:
        """Route one bus/telemetry event into durable history."""
        data = _event_dict(event)
        signal = signal_record_from_event(data)
        if signal is not None:
            await self._store.record_signal(signal)
            return
        decision = decision_record_from_event(data)
        if decision is not None:
            await self._ingest_decision(decision)
            return
        action = action_record_from_event(data)
        if action is not None:
            await self._ingest_action(action)

    async def _ingest_decision(self, record: dict[str, Any]) -> None:
        await self._store.record_decision(record)
        if not decision_requires_review(record):
            return
        item = review_item_for_judgment(record)
        filed = await self._file_pending_review(item)
        if filed:
            await self._store.link_review_item(str(record["decisionId"]), item.item_id)

    async def _ingest_action(self, record: dict[str, Any]) -> None:
        await self._store.record_action(record)
        status = str(record.get("status") or "")
        if status not in {"executed", "failed"}:
            return
        detail = str(record.get("outcome") or record.get("summary") or status)
        updated = await self._store.record_decision_outcome(
            correlation_id=str(record.get("correlationId") or ""),
            outcome=status,
            detail=detail,
            outcome_at=str(record.get("observedAt") or ""),
        )
        if updated:
            logger.info(
                "valkyrie history: stamped %s outcome onto %d decision(s) via %s",
                status,
                updated,
                record.get("correlationId"),
            )

    async def _file_pending_review(self, item: ReviewItem) -> bool:
        if self._review_service is None:
            return False
        existing = await self._review_service.get(item.item_id)
        if existing is not None:
            return False
        await self._review_service.store.upsert(item)
        logger.info("valkyrie history: filed review item %s", item.item_id)
        return True

    async def decision_detail(self, decision_id: str) -> dict[str, Any] | None:
        """One decision plus its full lineage: signals, actions, review item."""
        decision = await self._store.get_decision(decision_id)
        if decision is None:
            return None
        signals = await self._store.signals_by_ids(list(decision.get("signalRefs") or []))
        actions = await self._store.actions_for_correlation(
            str(decision.get("correlationId") or "")
        )
        review = None
        review_item_id = str(decision.get("reviewItemId") or "")
        if review_item_id and self._review_service is not None:
            item = await self._review_service.get(review_item_id)
            if item is not None:
                review = item.to_payload()
        return {
            "decision": decision,
            "lineage": {
                "signals": signals,
                "actions": actions,
                "review": review,
            },
        }

    async def skill_stats(self, *, environment_id: str = "") -> list[dict[str, Any]]:
        """Aggregate learned-skill usage from stored judgments (C16).

        Every learned-skill use is a judgment with a well-known
        ``operational_state``; the skill name travels in the judgment
        evidence written by the resident learning runtime.
        """
        stats: dict[str, dict[str, Any]] = {}
        for state in LEARNED_SKILL_STATES:
            rows, _total = await self._store.list_decisions(
                environment_id=environment_id,
                operational_state=state,
                limit=_SKILL_STATS_SCAN_LIMIT,
            )
            for row in rows:
                skill_name, capability = _skill_from_evidence(row)
                if not skill_name:
                    continue
                entry = stats.setdefault(
                    skill_name,
                    {
                        "skillName": skill_name,
                        "capability": capability,
                        "environmentId": str(row.get("environmentId") or ""),
                        "uses": 0,
                        "successes": 0,
                        "failures": 0,
                        "lastUsedAt": "",
                        "lastOutcome": "",
                        "rolledBackAt": "",
                    },
                )
                entry["uses"] += 1
                decided_at = str(row.get("decidedAt") or "")
                if state == "using_adopted_learning":
                    entry["successes"] += 1
                else:
                    entry["failures"] += 1
                if state == "adopted_learning_regressed" and decided_at > str(
                    entry["rolledBackAt"]
                ):
                    entry["rolledBackAt"] = decided_at
                if decided_at > str(entry["lastUsedAt"]):
                    entry["lastUsedAt"] = decided_at
                    entry["lastOutcome"] = state
        return sorted(stats.values(), key=lambda entry: str(entry["lastUsedAt"]), reverse=True)

    async def file_morning_briefs(self, *, window: timedelta) -> int:
        """File one acknowledgeable brief per active environment (C15).

        Returns the number of briefs filed. Brief items are deduped per
        environment per UTC day, so restarts and short intervals never
        flood the inbox.
        """
        if self._review_service is None:
            return 0
        since = (_now() - window).isoformat()
        decisions, _total = await self._store.list_decisions(limit=_BRIEF_SCAN_LIMIT)
        signals, _signal_total = await self._store.list_signals(limit=_BRIEF_SCAN_LIMIT)
        recent_decisions = [row for row in decisions if str(row.get("decidedAt") or "") >= since]
        recent_signals = [row for row in signals if str(row.get("receivedAt") or "") >= since]
        environment_ids = sorted(
            {str(row.get("environmentId") or "") for row in recent_decisions}
            | {str(row.get("environmentId") or "") for row in recent_signals}
        )
        filed = 0
        day = _now().strftime("%Y-%m-%d")
        for environment_id in environment_ids:
            if not environment_id or environment_id == "unknown":
                continue
            item = _brief_item(
                environment_id=environment_id,
                day=day,
                decisions=[
                    row for row in recent_decisions if row.get("environmentId") == environment_id
                ],
                signals=[
                    row for row in recent_signals if row.get("environmentId") == environment_id
                ],
            )
            if await self._file_pending_review(item):
                filed += 1
        return filed


def _skill_from_evidence(record: dict[str, Any]) -> tuple[str, str]:
    for entry in record.get("evidence") or []:
        if not isinstance(entry, dict):
            continue
        skill_name = str(entry.get("skill_name") or "")
        if skill_name:
            return skill_name, str(entry.get("capability_name") or "")
    return "", ""


def _brief_item(
    *,
    environment_id: str,
    day: str,
    decisions: list[dict[str, Any]],
    signals: list[dict[str, Any]],
) -> ReviewItem:
    by_state: dict[str, int] = {}
    for row in decisions:
        state = str(row.get("operationalState") or "watching")
        by_state[state] = by_state.get(state, 0) + 1
    by_severity: dict[str, int] = {}
    for row in signals:
        severity = str(row.get("severity") or "info")
        by_severity[severity] = by_severity.get(severity, 0) + 1
    highlights = [
        f"- {row.get('operationalState') or 'watching'}: "
        f"{row.get('rationale') or row.get('summary') or 'no rationale recorded'}"
        for row in decisions[:3]
    ]
    lines = [
        f"# Morning brief — {environment_id} — {day}",
        "",
        f"Signals observed: {len(signals)} "
        f"({', '.join(f'{count} {sev}' for sev, count in sorted(by_severity.items())) or 'none'})",
        f"Decisions taken: {len(decisions)} "
        f"({', '.join(f'{count} {state}' for state, count in sorted(by_state.items())) or 'none'})",
    ]
    if highlights:
        lines += ["", "Latest reasoning:", *highlights]
    return ReviewItem(
        item_id=f"review:{ReviewKind.MORNING_BRIEF.value}:{environment_id}:{day}",
        kind=ReviewKind.MORNING_BRIEF.value,
        requested_action="acknowledge",
        environment_id=environment_id,
        valkyrie_id="",
        title=f"Morning brief — {environment_id}",
        summary=(
            f"{len(signals)} signal(s) observed, {len(decisions)} decision(s) taken "
            f"in the last brief window."
        ),
        audience="environment",
        risk_class="low",
        safety_class="read_only",
        urgency=0.2,
        dedupe_key=f"{ReviewKind.MORNING_BRIEF.value}:{environment_id}:{day}",
        evidence={
            "brief_markdown": "\n".join(lines),
            "decision_count": len(decisions),
            "signal_count": len(signals),
            "decisions_by_state": by_state,
            "signals_by_severity": by_severity,
        },
        requested_by="ravn:valkyrie-brief",
        correlation_id=f"valkyrie-brief:{environment_id}:{day}",
    )
