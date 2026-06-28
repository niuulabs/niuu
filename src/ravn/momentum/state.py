"""Current Momentum resident state helpers."""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from ravn.momentum.models import (
    MomentumResidentState,
    MomentumStatePatch,
    MomentumStatePatchDraft,
    MomentumStateTension,
    MomentumStateTensionPatch,
)
from ravn.resident_continuation import _slug

CURRENT_MOMENTUM_STATE_REF = "resident/continuation/momentum/state/current.md"
T = TypeVar("T")
MAX_BELIEFS = 12
MAX_CONSTRAINTS = 12
MAX_CORRECTIONS = 12
MAX_OPEN_TENSIONS = 8
MAX_STALE_ASSUMPTIONS = 8
MAX_RECENT_LESSONS = 12
MAX_CANDIDATE_REFLEXES = 8
MAX_CANDIDATE_CAPABILITY_GAPS = 8
MAX_SOURCE_REFS = 24


def empty_momentum_state(*, updated_at: datetime) -> MomentumResidentState:
    return MomentumResidentState(updated_at=updated_at)


def extraction_state_patch(
    *,
    patch_id: str,
    created_at: datetime,
    belief_refs: list[str],
    judgment_title: str,
    tension: str,
    evidence_refs: list[str],
    source_refs: list[str],
    beliefs: list[str],
    constraints: list[str],
    corrections: list[str],
) -> MomentumStatePatch:
    tension_id = f"tension-{_slug(judgment_title) or _slug(tension) or patch_id}"
    return MomentumStatePatch(
        patch_id=patch_id,
        created_at=created_at,
        source_refs=_unique([*source_refs, *belief_refs]),
        beliefs=beliefs,
        constraints=constraints,
        corrections=corrections,
        open_tensions=[
            MomentumStateTension(
                tension_id=tension_id,
                title=judgment_title,
                summary=tension,
                status="pending",
                evidence_refs=_unique(evidence_refs),
                source_refs=_unique(source_refs),
                updated_at=created_at,
            )
        ],
    )


def reflection_state_patch(
    *,
    patch_id: str,
    created_at: datetime,
    source_refs: list[str],
    draft: MomentumStatePatchDraft,
    lesson_learned: str,
    remember_next_time: list[str],
    corrections: list[str],
    candidate_reflexes: list[str],
    candidate_capability_gaps: list[str],
) -> MomentumStatePatch:
    data = draft.model_dump()
    data.pop("corrections", None)
    data.pop("recent_lessons", None)
    data.pop("candidate_reflexes", None)
    data.pop("candidate_capability_gaps", None)
    return MomentumStatePatch(
        **data,
        patch_id=patch_id,
        created_at=created_at,
        source_refs=_unique(source_refs),
        corrections=_unique([*draft.corrections, *corrections]),
        recent_lessons=_unique([*draft.recent_lessons, lesson_learned, *remember_next_time]),
        candidate_reflexes=_unique([*draft.candidate_reflexes, *candidate_reflexes]),
        candidate_capability_gaps=_unique(
            [*draft.candidate_capability_gaps, *candidate_capability_gaps]
        ),
    )


def apply_state_patch(
    current: MomentumResidentState,
    patch: MomentumStatePatch,
) -> MomentumResidentState:
    tensions = {item.tension_id: item for item in current.open_tensions}
    for item in patch.open_tensions:
        tensions[item.tension_id] = item.model_copy(
            update={"source_refs": _unique([*item.source_refs, *patch.source_refs])}
        )
    for item in patch.changed_tensions:
        existing = tensions.get(item.tension_id)
        if existing is None and (item.title is None or item.summary is None):
            continue
        title = item.title or (existing.title if existing else item.tension_id)
        summary = item.summary or (existing.summary if existing else "-")
        tensions[item.tension_id] = MomentumStateTension(
            tension_id=item.tension_id,
            title=title,
            summary=summary,
            status=item.status or "changed",
            evidence_refs=_unique(
                [*(existing.evidence_refs if existing else []), *item.evidence_refs]
            ),
            source_refs=_unique(
                [
                    *(existing.source_refs if existing else []),
                    *item.source_refs,
                    *patch.source_refs,
                ]
            ),
            updated_at=patch.created_at,
        )
    for tension_id in patch.confirmed_tension_ids:
        if tension_id in tensions:
            tensions[tension_id] = tensions[tension_id].model_copy(
                update={"status": "confirmed", "updated_at": patch.created_at}
            )
    for tension_id in patch.resolved_tension_ids:
        if tension_id in tensions:
            tensions[tension_id] = tensions[tension_id].model_copy(
                update={"status": "resolved", "updated_at": patch.created_at}
            )

    state = MomentumResidentState(
        beliefs=_unique_recent([*current.beliefs, *patch.beliefs]),
        constraints=_unique_recent([*current.constraints, *patch.constraints]),
        corrections=_unique_recent([*current.corrections, *patch.corrections]),
        open_tensions=list(tensions.values()),
        stale_assumptions=_unique_recent(
            [*current.stale_assumptions, *patch.stale_assumptions]
        ),
        recent_lessons=_unique_recent([*current.recent_lessons, *patch.recent_lessons]),
        candidate_reflexes=_unique_recent(
            [*current.candidate_reflexes, *patch.candidate_reflexes]
        ),
        candidate_capability_gaps=_unique_recent(
            [*current.candidate_capability_gaps, *patch.candidate_capability_gaps]
        ),
        source_refs=_unique_recent([*current.source_refs, *patch.source_refs]),
        compaction=current.compaction,
        updated_at=patch.created_at,
    )
    return _compact_state(state)


def parse_momentum_state(content: str) -> MomentumResidentState:
    marker = "## State Data"
    if marker not in content:
        raise ValueError("Momentum state artifact is missing State Data")
    payload = content.split(marker, 1)[1].split("```json", 1)[1].split("```", 1)[0]
    return MomentumResidentState.model_validate_json(payload)


def render_momentum_state(state: MomentumResidentState) -> str:
    return (
        "# Current Momentum Resident State\n\n"
        f"- updated_at: {state.updated_at.isoformat()}\n\n"
        "## Current Beliefs\n\n"
        f"{_bullets_text(state.beliefs)}\n\n"
        "## Constraints\n\n"
        f"{_bullets_text(state.constraints)}\n\n"
        "## Corrections\n\n"
        f"{_bullets_text(state.corrections)}\n\n"
        "## Open Tensions\n\n"
        f"{_tension_text(state.open_tensions)}\n\n"
        "## Stale Assumptions Or Unknowns\n\n"
        f"{_bullets_text(state.stale_assumptions)}\n\n"
        "## Recent Lessons\n\n"
        f"{_bullets_text(state.recent_lessons)}\n\n"
        "## Candidate Reflexes (candidate-only)\n\n"
        f"{_bullets_text(state.candidate_reflexes)}\n\n"
        "## Candidate Capability Gaps (candidate-only)\n\n"
        f"{_bullets_text(state.candidate_capability_gaps)}\n\n"
        f"{_compaction_section(state)}"
        "## Source Refs\n\n"
        f"{_bullets_text(state.source_refs)}\n\n"
        "## State Data\n\n"
        f"```json\n{state.model_dump_json(indent=2)}\n```\n"
    )


def render_state_patch(patch: MomentumStatePatch) -> str:
    return (
        f"# Momentum State Patch {patch.patch_id}\n\n"
        f"- patch_id: {patch.patch_id}\n"
        f"- created_at: {patch.created_at.isoformat()}\n\n"
        "## Source Refs\n\n"
        f"{_bullets_text(patch.source_refs)}\n\n"
        "## Added Beliefs\n\n"
        f"{_bullets_text(patch.beliefs)}\n\n"
        "## Added Constraints\n\n"
        f"{_bullets_text(patch.constraints)}\n\n"
        "## Added Corrections\n\n"
        f"{_bullets_text(patch.corrections)}\n\n"
        "## Open Or Changed Tensions\n\n"
        f"{_tension_text([*patch.open_tensions, *patch.changed_tensions])}\n\n"
        "## Resolved Tensions\n\n"
        f"{_bullets_text(patch.resolved_tension_ids)}\n\n"
        "## Confirmed Tensions\n\n"
        f"{_bullets_text(patch.confirmed_tension_ids)}\n\n"
        "## Recent Lessons\n\n"
        f"{_bullets_text(patch.recent_lessons)}\n\n"
        "## Candidate Reflexes (candidate-only)\n\n"
        f"{_bullets_text(patch.candidate_reflexes)}\n\n"
        "## Candidate Capability Gaps (candidate-only)\n\n"
        f"{_bullets_text(patch.candidate_capability_gaps)}\n\n"
        "## Patch Data\n\n"
        f"```json\n{patch.model_dump_json(indent=2)}\n```\n"
    )


def _tension_text(tensions: list[MomentumStateTension | MomentumStateTensionPatch]) -> str:
    if not tensions:
        return "- none"
    lines: list[str] = []
    for tension in tensions:
        title = tension.title or tension.tension_id
        summary = tension.summary or "-"
        status = tension.status or "changed"
        lines.extend(
            [
                f"- {title}",
                f"  - id: {tension.tension_id}",
                f"  - status: {status}",
                f"  - summary: {summary}",
            ]
        )
    return "\n".join(lines)


def _bullets_text(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item) or "- none"


def _compact_state(state: MomentumResidentState) -> MomentumResidentState:
    compaction = dict(state.compaction)
    beliefs, count = _keep_newest(state.beliefs, MAX_BELIEFS)
    _record_compaction(compaction, "beliefs_truncated", count)
    constraints, count = _keep_newest(state.constraints, MAX_CONSTRAINTS)
    _record_compaction(compaction, "constraints_truncated", count)
    corrections, count = _keep_newest(state.corrections, MAX_CORRECTIONS)
    _record_compaction(compaction, "corrections_truncated", count)
    stale_assumptions, count = _keep_newest(
        state.stale_assumptions, MAX_STALE_ASSUMPTIONS
    )
    _record_compaction(compaction, "stale_assumptions_truncated", count)
    recent_lessons, count = _keep_newest(state.recent_lessons, MAX_RECENT_LESSONS)
    _record_compaction(compaction, "recent_lessons_truncated", count)
    candidate_reflexes, count = _keep_newest(
        state.candidate_reflexes, MAX_CANDIDATE_REFLEXES
    )
    _record_compaction(compaction, "candidate_reflexes_truncated", count)
    candidate_capability_gaps, count = _keep_newest(
        state.candidate_capability_gaps, MAX_CANDIDATE_CAPABILITY_GAPS
    )
    _record_compaction(compaction, "candidate_capability_gaps_truncated", count)
    source_refs, count = _keep_newest(state.source_refs, MAX_SOURCE_REFS)
    _record_compaction(compaction, "source_refs_truncated", count)
    open_tensions = sorted(state.open_tensions, key=lambda item: item.updated_at)
    open_tensions, count = _keep_newest(open_tensions, MAX_OPEN_TENSIONS)
    _record_compaction(compaction, "open_tensions_truncated", count)
    return state.model_copy(
        update={
            "beliefs": beliefs,
            "constraints": constraints,
            "corrections": corrections,
            "open_tensions": open_tensions,
            "stale_assumptions": stale_assumptions,
            "recent_lessons": recent_lessons,
            "candidate_reflexes": candidate_reflexes,
            "candidate_capability_gaps": candidate_capability_gaps,
            "source_refs": source_refs,
            "compaction": compaction,
        }
    )


def _keep_newest(items: list[T], limit: int) -> tuple[list[T], int]:
    if len(items) <= limit:
        return items, 0
    return items[-limit:], len(items) - limit


def _record_compaction(compaction: dict[str, int], key: str, count: int) -> None:
    if count:
        compaction[key] = compaction.get(key, 0) + count


def _compaction_section(state: MomentumResidentState) -> str:
    if not state.compaction:
        return ""
    lines = ["## State Compaction", ""]
    for key, count in state.compaction.items():
        entry_word = "entry" if count == 1 else "entries"
        lines.append(f"- {key}: {count} older {entry_word} omitted")
    return "\n".join(lines) + "\n\n"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _unique_recent(items: list[str]) -> list[str]:
    return list(reversed(_unique(list(reversed(items)))))
