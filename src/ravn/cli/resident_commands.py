"""Operator inspection of a resident's durable state.

A deployed resident carries its whole continuation in a state store: open
cases, the working state it believes, the questions it is waiting on, the
wakes it scheduled, and the streak counting how many turns in a row reached
the same conclusion. Until now the only way to look at any of it was to scale
the deployment to zero and mount its volume in a helper pod, which is a
diagnosis and an archaeology expedition at the same time.

These commands read that state through the *configured* ``ResidentStatePort``
— the same adapter, built by the same wiring, rooted at the same place as the
daemon. That is the whole point: no path guessing, and no second answer to
"what does this resident actually believe" that can drift from the first.

Read-only. Nothing here mutates resident state.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import typer

resident_app = typer.Typer(
    name="resident",
    help="Inspect a resident's durable state (cases, beliefs, questions, wakes).",
    add_completion=False,
)


@dataclass(frozen=True)
class _CaseSummary:
    """What one durable case looks like from the outside."""

    case_id: str
    refs: tuple[str, ...]
    has_pending_wake: bool
    has_pending_question: bool

    @property
    def resumable(self) -> bool:
        """Whether anything can still bring this case back.

        Exactly two mechanisms resume a case — a pending scheduled wake and an
        unanswered operator question — so a case with neither is inert no
        matter how recent it looks.
        """
        return self.has_pending_wake or self.has_pending_question

    @property
    def resume_reason(self) -> str:
        """What would resume this case, or "" when nothing can."""
        reasons = []
        if self.has_pending_wake:
            reasons.append("scheduled wake")
        if self.has_pending_question:
            reasons.append("operator question")
        return " + ".join(reasons)


def _run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _load_settings(config: str) -> Any:
    """Load settings exactly as the daemon does, honouring ``--config``."""
    import os  # noqa: PLC0415

    from ravn.config import Settings  # noqa: PLC0415

    if config:
        os.environ["RAVN_CONFIG"] = config
    return Settings()


async def _open_state(settings: Any) -> tuple[Any, str]:
    """Build the configured resident state adapter and the resident's id.

    Deliberately reuses the daemon's builders rather than re-deriving a root:
    an adapter that the daemon can build and this cannot (or vice versa) is
    exactly the drift that makes an inspection tool untrustworthy.
    """
    from ravn.cli.commands import (  # noqa: PLC0415
        _build_mimir,
        _build_resident_state,
        _resolve_workspace,
    )

    workspace = _resolve_workspace(settings)
    mimir = _build_mimir(settings)
    state = await _build_resident_state(settings, workspace=workspace, mimir=mimir)
    resident_id = (
        settings.mesh.own_peer_id
        or settings.environment.resident_name
        or settings.initiative.default_persona
        or "resident"
    )
    return state, resident_id


def _case_id_from_ref(ref: str, *, prefix: str) -> str:
    """Return the case slug a state ref belongs to, or "" if it is not a case.

    Refs are ``<prefix>/cases/<slug>/<leaf>``; both shipped adapters build them
    through the same helper, so parsing here works for either store.
    """
    parts = PurePosixPath(ref.replace("\\", "/")).parts
    prefix_parts = PurePosixPath(prefix.strip("/")).parts if prefix else ()
    if prefix_parts:
        if parts[: len(prefix_parts)] != prefix_parts:
            return ""
        parts = parts[len(prefix_parts) :]
    if len(parts) < 3 or parts[0] != "cases":
        return ""
    return parts[1]


def _leaf_from_ref(ref: str, case_id: str) -> str:
    """Return the part of ``ref`` below ``cases/<case_id>/``."""
    parts = PurePosixPath(ref.replace("\\", "/")).parts
    if case_id not in parts:
        return ref
    index = parts.index(case_id)
    return "/".join(parts[index + 1 :])


async def _collect_cases(state: Any, *, prefix: str) -> list[_CaseSummary]:
    """Group every case ref into one summary per case."""
    refs = await state.list_refs()
    pending_wakes = {entry.path for entry in await state.list_scheduled_wakes()}
    pending_questions = {entry.path for entry in await state.list_operator_needed()}

    grouped: dict[str, list[str]] = {}
    for ref in refs:
        case_id = _case_id_from_ref(ref, prefix=prefix)
        if case_id:
            grouped.setdefault(case_id, []).append(ref)

    summaries = [
        _CaseSummary(
            case_id=case_id,
            refs=tuple(sorted(case_refs)),
            has_pending_wake=any(ref in pending_wakes for ref in case_refs),
            has_pending_question=any(ref in pending_questions for ref in case_refs),
        )
        for case_id, case_refs in grouped.items()
    ]
    summaries.sort(key=lambda case: case.case_id)
    return summaries


async def _case_counts(state: Any, *, prefix: str) -> dict[str, Any]:
    """Return the live/total case counts, preferring the store's own answer.

    The resident's health scorecard publishes ``ravn.resident.cases.live`` and
    ``.total`` from ``count_cases()``. Deriving the same two numbers here from
    a ref walk would give the operator a second answer that can drift from the
    dashboard, so ask the store first and only walk when it declines — which
    the Mimir adapter does deliberately, rather than pay a remote walk per
    health refresh. ``countedBy`` says which happened, because a number whose
    provenance is unclear is the thing that wasted the diagnosis last time.
    """
    counts = await state.count_cases()
    if counts is not None:
        live, total = counts
        return {"total": total, "live": live, "inert": total - live, "countedBy": "store"}

    cases = await _collect_cases(state, prefix=prefix)
    live = sum(1 for case in cases if case.resumable)
    return {
        "total": len(cases),
        "live": live,
        "inert": len(cases) - live,
        "countedBy": "walk",
    }


def _state_prefix(state: Any) -> str:
    """The continuation prefix the adapter writes under.

    The two shipped adapters disagree on type — the local store keeps a
    ``Path``, the Mimir one a ``str`` — so normalise to posix text here rather
    than at each call site.
    """
    prefix = getattr(state, "_prefix", "") or "resident/continuation"
    return PurePosixPath(str(prefix)).as_posix()


def _emit(payload: Any, *, json_output: bool, render: Callable[[], None]) -> None:
    """Print JSON or the human rendering, so every command supports both."""
    if json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    render()


def _entry_payload(entries: Iterable[Any]) -> list[dict[str, str]]:
    return [{"ref": entry.path, "summary": entry.summary} for entry in entries]


def _echo_entries(entries: Sequence[dict[str, str]], *, empty: str) -> None:
    if not entries:
        typer.echo(empty)
        return
    for entry in entries:
        typer.echo(f"  {entry['ref']}")
        if entry["summary"]:
            typer.echo(f"      {entry['summary']}")


@resident_app.command("status")
def resident_status(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show one screen of what this resident is currently doing and believing."""
    settings = _load_settings(config)

    async def _gather() -> dict[str, Any]:
        state, resident_id = await _open_state(settings)
        prefix = _state_prefix(state)
        cases = await _case_counts(state, prefix=prefix)
        streak = await state.read_decision_streak(resident_id)
        working_state = await state.read_working_state(resident_id)
        questions = await state.list_operator_needed()
        wakes = await state.list_scheduled_wakes()
        a2a_tasks = await state.list_a2a_tasks()
        escalate_after = settings.resident_state.repeated_decision_escalate_after
        return {
            "resident": resident_id,
            "environment": settings.environment.id,
            "adapter": type(state).__name__,
            "cases": cases,
            "decisionStreak": (
                None
                if streak is None
                else {
                    "count": streak.count,
                    "escalateAfter": escalate_after,
                    "stuck": escalate_after > 0 and streak.count >= escalate_after,
                    "decision": streak.decision,
                    "rationale": streak.rationale,
                    "fingerprint": streak.fingerprint,
                    "caseId": streak.case_id,
                    "firstSeenAt": streak.first_seen_at,
                    "updatedAt": streak.updated_at,
                }
            ),
            "pendingQuestions": _entry_payload(questions),
            "scheduledWakes": _entry_payload(wakes),
            "openA2ATasks": _entry_payload(a2a_tasks),
            "workingState": (
                None
                if working_state is None
                else {"ref": working_state.path, "summary": working_state.summary}
            ),
        }

    payload = _run(_gather())

    def _render() -> None:
        typer.echo(f"resident:    {payload['resident']}")
        typer.echo(f"environment: {payload['environment'] or '-'}")
        typer.echo(f"state store: {payload['adapter']}")
        counts = payload["cases"]
        typer.echo(
            f"cases:       {counts['total']} "
            f"({counts['live']} live, {counts['inert']} inert) "
            f"[counted by {counts['countedBy']}]"
        )

        streak = payload["decisionStreak"]
        if streak is None:
            typer.echo("streak:      none recorded")
        else:
            marker = "  ** STUCK **" if streak["stuck"] else ""
            typer.echo(
                f"streak:      {streak['count']}x (escalates at {streak['escalateAfter']}){marker}"
            )
            if streak["decision"]:
                typer.echo(f"             decision: {streak['decision']}")
            if streak["rationale"]:
                typer.echo(f"             because:  {streak['rationale']}")

        typer.echo("")
        typer.echo(f"pending questions ({len(payload['pendingQuestions'])}):")
        _echo_entries(payload["pendingQuestions"], empty="  none")
        typer.echo(f"scheduled wakes ({len(payload['scheduledWakes'])}):")
        _echo_entries(payload["scheduledWakes"], empty="  none")
        typer.echo(f"open peer tasks ({len(payload['openA2ATasks'])}):")
        _echo_entries(payload["openA2ATasks"], empty="  none")

        working_state = payload["workingState"]
        typer.echo("")
        if working_state is None:
            typer.echo("working state: none written yet")
            return
        typer.echo(f"working state: {working_state['ref']}")
        if working_state["summary"]:
            typer.echo(f"               {working_state['summary']}")

    _emit(payload, json_output=json_output, render=_render)


@resident_app.command("cases")
def resident_cases(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    resumable: bool = typer.Option(
        False, "--resumable", help="Only cases something can still bring back."
    ),
    inert: bool = typer.Option(False, "--inert", help="Only cases nothing can resume."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """List durable cases and say which of them can still resume."""
    if resumable and inert:
        raise typer.BadParameter("--resumable and --inert are mutually exclusive")

    settings = _load_settings(config)

    async def _gather() -> list[_CaseSummary]:
        state, _resident_id = await _open_state(settings)
        return await _collect_cases(state, prefix=_state_prefix(state))

    cases = _run(_gather())
    if resumable:
        cases = [case for case in cases if case.resumable]
    if inert:
        cases = [case for case in cases if not case.resumable]

    payload = [
        {
            "caseId": case.case_id,
            "resumable": case.resumable,
            "resumeReason": case.resume_reason,
            "refs": list(case.refs),
        }
        for case in cases
    ]

    def _render() -> None:
        if not cases:
            typer.echo("No cases found.")
            return
        for case in cases:
            flag = f"resumable: {case.resume_reason}" if case.resumable else "inert"
            typer.echo(f"{case.case_id}  [{flag}]  {len(case.refs)} ref(s)")

    _emit(payload, json_output=json_output, render=_render)


@resident_app.command("case")
def resident_case(
    case_id: str = typer.Argument(help="Case id (slug) to show."),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    content: bool = typer.Option(False, "--content", help="Print each ref's full content."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show one case: every ref it holds, and what resumes it."""
    settings = _load_settings(config)

    async def _gather() -> dict[str, Any] | None:
        state, _resident_id = await _open_state(settings)
        prefix = _state_prefix(state)
        cases = await _collect_cases(state, prefix=prefix)
        case = next((item for item in cases if item.case_id == case_id), None)
        if case is None:
            return None
        refs: list[dict[str, str]] = []
        for ref in case.refs:
            entry = await state.read(ref)
            refs.append(
                {
                    "ref": ref,
                    "leaf": _leaf_from_ref(ref, case.case_id),
                    "summary": "" if entry is None else entry.summary,
                    "content": "" if entry is None or not content else entry.content,
                }
            )
        return {
            "caseId": case.case_id,
            "resumable": case.resumable,
            "resumeReason": case.resume_reason,
            "refs": refs,
        }

    payload = _run(_gather())
    if payload is None:
        typer.echo(f"Case not found: {case_id}", err=True)
        raise typer.Exit(1)

    def _render() -> None:
        typer.echo(f"case:      {payload['caseId']}")
        reason = payload["resumeReason"] or "nothing can resume it"
        typer.echo(f"resumable: {payload['resumable']} ({reason})")
        typer.echo("")
        for ref in payload["refs"]:
            typer.echo(f"{ref['leaf']}")
            if ref["summary"]:
                typer.echo(f"    {ref['summary']}")
            if ref["content"]:
                typer.echo("")
                typer.echo(ref["content"])
                typer.echo("")

    _emit(payload, json_output=json_output, render=_render)


@resident_app.command("streak")
def resident_streak(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show the repeated-decision streak — how long this resident has been stuck."""
    settings = _load_settings(config)

    async def _gather() -> dict[str, Any] | None:
        state, resident_id = await _open_state(settings)
        streak = await state.read_decision_streak(resident_id)
        if streak is None:
            return None
        escalate_after = settings.resident_state.repeated_decision_escalate_after
        return {
            "resident": streak.resident_id,
            "count": streak.count,
            "escalateAfter": escalate_after,
            "stuck": escalate_after > 0 and streak.count >= escalate_after,
            "decision": streak.decision,
            "rationale": streak.rationale,
            "fingerprint": streak.fingerprint,
            "caseId": streak.case_id,
            "firstSeenAt": streak.first_seen_at,
            "updatedAt": streak.updated_at,
        }

    payload = _run(_gather())
    if payload is None:
        typer.echo("No decision streak recorded.")
        return

    def _render() -> None:
        typer.echo(f"resident:    {payload['resident']}")
        typer.echo(f"count:       {payload['count']} (escalates at {payload['escalateAfter']})")
        typer.echo(f"stuck:       {payload['stuck']}")
        typer.echo(f"decision:    {payload['decision'] or '-'}")
        typer.echo(f"rationale:   {payload['rationale'] or '-'}")
        typer.echo(f"fingerprint: {payload['fingerprint']}")
        typer.echo(f"case:        {payload['caseId'] or '-'}")
        typer.echo(f"first seen:  {payload['firstSeenAt']}")
        typer.echo(f"updated:     {payload['updatedAt']}")

    _emit(payload, json_output=json_output, render=_render)


@resident_app.command("questions")
def resident_questions(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    answers: bool = typer.Option(
        False, "--answers", help="Show unconsumed operator answers instead."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """List the operator questions this resident is blocked on."""
    settings = _load_settings(config)

    async def _gather() -> list[Any]:
        state, _resident_id = await _open_state(settings)
        if answers:
            return await state.list_operator_answers()
        return await state.list_operator_needed()

    entries = _run(_gather())
    payload = _entry_payload(entries)
    label = "answers" if answers else "questions"

    def _render() -> None:
        _echo_entries(payload, empty=f"No pending {label}.")

    _emit(payload, json_output=json_output, render=_render)


@resident_app.command("wakes")
def resident_wakes(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """List the wakes this resident scheduled for itself."""
    settings = _load_settings(config)

    async def _gather() -> list[Any]:
        state, _resident_id = await _open_state(settings)
        return await state.list_scheduled_wakes()

    entries = _run(_gather())
    payload = _entry_payload(entries)

    def _render() -> None:
        _echo_entries(payload, empty="No scheduled wakes.")

    _emit(payload, json_output=json_output, render=_render)


@resident_app.command("working-state")
def resident_working_state(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Print the resident's own model of its current reality."""
    settings = _load_settings(config)

    async def _gather() -> dict[str, Any] | None:
        state, resident_id = await _open_state(settings)
        entry = await state.read_working_state(resident_id)
        if entry is None:
            return None
        return {"ref": entry.path, "summary": entry.summary, "content": entry.content}

    payload = _run(_gather())
    if payload is None:
        typer.echo("No working state written yet.")
        return

    def _render() -> None:
        typer.echo(f"# {payload['ref']}")
        typer.echo("")
        typer.echo(payload["content"])

    _emit(payload, json_output=json_output, render=_render)
