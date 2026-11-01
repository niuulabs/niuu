"""Capability lineage and retirement for learned tools, on the existing records.

Residents built learned tools and mostly did not use them. The mechanisms to
prevent that existed — reuse before commissioning, version chaining on
``capability_id``, lifecycle usage counts — but did not work live:

* reuse matched only an exact ``capability_id`` or name, and a build without a
  ``capability_id`` got one derived from its own name, so every rename
  (``list_pods_in_namespace_working``, ``_safe``, ``_fixed``…) was a new
  capability, and 19 of 40 artifacts on one resident carried none at all;
* supersession only linked a new version to the old one, which stayed
  installed;
* ``run_count`` / ``last_used_at`` were recorded and nothing read them.

This module repairs those paths instead of adding new stores:

* :func:`find_installed_capability` also matches by meaning, through the
  lesson embedding shortlist and judge, and considers retired tools.
* :func:`retire_learned_tool` archives the envelope exactly where superseded
  versions already go (``learned_tool_artifacts/_archive``), uninstalls the
  code, and archives the lifecycle record with the reason;
  :func:`restore_learned_tool` reverses it.
* :func:`retire_superseded_predecessor` retires the tool a newly installed
  version supersedes.
* :class:`LearnedToolCurator` (the consolidation-dream pass) backfills each
  artifact's capability id through the same matching, then retires superseded,
  unrunnable, debug-leftover and never-used tools, reading usage from the
  lifecycle registry. A tool the execution backend refuses to load is
  unrunnable like one that fails verification: it is retired with the
  loader's reason, and ``build_tool`` restores it as a repair of that tool.

The window for "never used" is not a constant: it is the longest time any tool
on this resident waited between install and first use (observed, or bounded
by its history before ``first_used_at`` was recorded). A retirement the
window stops supporting is undone by the next pass.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from niuu.observability import get_observability
from ravn.ports.capability_matching import CapabilityDescriptor, CapabilityMatcherPort
from ravn.skills.management import SkillLifecycle
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolError,
    capability_key,
    learned_tool_artifact_path,
    learned_tool_path,
    learned_tool_storage,
    superseded_artifact_path,
    write_learned_tool,
)
from ravn.valkyrie_evolution.models import LearnedToolArtifact

logger = logging.getLogger(__name__)

#: Why a tool was retired. Order is precedence: a tool that is both superseded
#: and never used is recorded as superseded, which names what replaces it.
RETIRED_SUPERSEDED = "superseded"
RETIRED_DEBUG_LEFTOVER = "debug_leftover"
RETIRED_UNRUNNABLE = "unrunnable"
RETIRED_UNUSED = "unused"
RETIREMENT_REASONS = (
    RETIRED_SUPERSEDED,
    RETIRED_DEBUG_LEFTOVER,
    RETIRED_UNRUNNABLE,
    RETIRED_UNUSED,
)

#: Retirements a peer's re-proposal of the same tool must not undo: the
#: resident already holds the replacement, or the tool was never an instrument.
RETIREMENTS_PEERS_CANNOT_UNDO = frozenset({RETIRED_SUPERSEDED, RETIRED_DEBUG_LEFTOVER})

#: Provenance key recording that an artifact's capability id was decided by
#: matching (build_tool lineage or curation), so it is not judged again.
CAPABILITY_MATCHED_KEY = "capability_matched"


class LifecycleView(Protocol):
    """What curation reads from the lifecycle registry."""

    def status(self, name: str) -> str | None: ...

    def lifecycle_metadata(self, name: str) -> dict[str, Any] | None: ...

    def lifecycle_records(self) -> dict[str, SkillLifecycle]: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_json_object(path: Path, *, what: str) -> dict[str, Any] | None:
    """Return the JSON object at *path*, ``None`` when absent; raise when corrupt."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LearnedToolError(f"{what} at {path} is not valid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise LearnedToolError(f"{what} at {path} is not a JSON object")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _lifecycle_from(data: Mapping[str, Any]) -> SkillLifecycle:
    known = {item.name for item in fields(SkillLifecycle)}
    return SkillLifecycle(**{key: value for key, value in data.items() if key in known})


class LifecycleSnapshot:
    """Read-only view of the lifecycle registry file, for planning outside the daemon.

    The running resident's registry is the only writer of that file; a report
    reads what it needs and changes nothing.
    """

    def __init__(self, path: str | Path) -> None:
        payload = _read_json_object(Path(path), what="skill lifecycle registry") or {}
        self._records = {
            str(key): _lifecycle_from(value)
            for key, value in payload.items()
            if isinstance(value, dict)
        }

    def status(self, name: str) -> str | None:
        record = self._records.get(_key(name))
        return record.status if record is not None else None

    def lifecycle_metadata(self, name: str) -> dict[str, Any] | None:
        record = self._records.get(_key(name))
        return asdict(record) if record is not None else None

    def lifecycle_records(self) -> dict[str, SkillLifecycle]:
        return dict(self._records)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolEntry:
    """One installed or retired learned tool, with its lifecycle record."""

    artifact: LearnedToolArtifact
    lifecycle: SkillLifecycle | None = None
    retired: bool = False

    @property
    def name(self) -> str:
        return self.artifact.manifest.name

    @property
    def verified(self) -> bool:
        verification = self.artifact.provenance.get("verification")
        return isinstance(verification, dict) and verification.get("ok") is True

    @property
    def capability(self) -> str:
        return self.artifact.source_gap_id.strip() or capability_key(self.name)

    @property
    def matched(self) -> bool:
        marker = self.artifact.provenance.get(CAPABILITY_MATCHED_KEY)
        # The dev.369 migration marked tools from folded verdicts, which split
        # variant families its single pass could not join; those are judged
        # once more by the dream.
        return isinstance(marker, dict) and marker.get("by") != "migration"

    @property
    def retired_reason(self) -> str:
        return self.lifecycle.archived_reason if self.lifecycle is not None else ""

    def descriptor(self) -> CapabilityDescriptor:
        properties = self.artifact.manifest.input_schema.get("properties")
        inputs = tuple(sorted(properties)) if isinstance(properties, dict) else ()
        return CapabilityDescriptor(
            key=self.artifact.artifact_id or self.name,
            name=self.name,
            description=self.artifact.manifest.description,
            inputs=inputs,
        )


def tool_inventory(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    skills: LifecycleView | None,
    include_retired: bool = False,
) -> list[ToolEntry]:
    """Installed tools (code on disk) and, optionally, retired ones.

    A retired tool is a lifecycle record archived with a retirement reason
    whose envelope is in the archive.
    """
    artifacts_path = Path(artifacts_dir)
    records = skills.lifecycle_records() if skills is not None else {}
    entries: list[ToolEntry] = []
    installed: set[str] = set()
    for path in sorted(artifacts_path.glob("*.json")) if artifacts_path.is_dir() else []:
        artifact = _artifact_at(path)
        if artifact is None or not learned_tool_path(tools_dir, artifact.manifest.name).is_file():
            continue
        installed.add(artifact.manifest.name)
        entries.append(
            ToolEntry(artifact=artifact, lifecycle=records.get(_key(artifact.manifest.name)))
        )
    if not include_retired:
        return entries
    for record in records.values():
        if record.status != "archived" or record.archived_reason not in RETIREMENT_REASONS:
            continue
        if record.name in installed or not record.archived_artifact_id:
            continue
        artifact = _artifact_at(
            superseded_artifact_path(artifacts_path, record.name, record.archived_artifact_id)
        )
        if artifact is not None:
            entries.append(ToolEntry(artifact=artifact, lifecycle=record, retired=True))
    return entries


def _key(name: str) -> str:
    return name.strip().lower()


def _artifact_at(path: Path) -> LearnedToolArtifact | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not (payload.get("manifest") or {}).get("name"):
        return None
    try:
        return LearnedToolArtifact.from_dict(payload)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Retire and restore
# ---------------------------------------------------------------------------


async def retire_learned_tool(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    skills: Any,
    name: str,
    reason: str,
    detail: str,
    superseded_by: str = "",
) -> None:
    """Archive one installed tool and uninstall it. Reversible.

    The envelope goes where superseded versions already go
    (:func:`superseded_artifact_path`), the code file is removed (the envelope
    keeps the code), and the lifecycle record is archived with the reason.
    """
    if reason not in RETIREMENT_REASONS:
        raise LearnedToolError(f"unknown retirement reason {reason!r}")
    if skills.status(name) is None:
        raise LearnedToolError(
            f"cannot retire {name!r}: it has no lifecycle record to hold the retirement"
        )
    envelope = learned_tool_artifact_path(artifacts_dir, name)
    payload = _read_json_object(envelope, what=f"envelope of learned tool {name!r}")
    if payload is None:
        raise LearnedToolError(f"cannot retire {name!r}: no installed envelope at {envelope}")
    artifact_id = str(payload.get("artifact_id") or "")
    _write_json(superseded_artifact_path(artifacts_dir, name, artifact_id), payload)
    envelope.unlink()
    learned_tool_path(tools_dir, name).unlink(missing_ok=True)
    await skills.archive(
        name,
        reason=reason,
        detail=detail,
        superseded_by=superseded_by,
        artifact_id=artifact_id,
    )
    telemetry = get_observability()
    telemetry.event(
        "ravn.learned_tool.curation.retired",
        attributes={
            "ravn.learned_tool.name": name,
            "ravn.learned_tool.retirement.reason": reason,
            "ravn.learned_tool.retirement.superseded_by": superseded_by,
        },
        content={"detail": detail},
    )
    telemetry.count(
        "ravn.learned_tool.curation.retirements",
        attributes={"ravn.learned_tool.retirement.reason": reason},
        description="Learned tools retired by curation, by reason.",
    )
    logger.info("Retired learned tool %s (%s): %s", name, reason, detail)


async def restore_learned_tool(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    skills: Any,
    name: str,
    why: str,
) -> SkillLifecycle:
    """Reinstall a retired tool from the archive. Returns its record as retired."""
    metadata = skills.lifecycle_metadata(name)
    if not metadata or metadata.get("archived_reason") not in RETIREMENT_REASONS:
        raise LearnedToolError(f"cannot restore {name!r}: it is not retired")
    retired = _lifecycle_from(metadata)
    envelope = learned_tool_artifact_path(artifacts_dir, name)
    if envelope.exists():
        raise LearnedToolError(
            f"cannot restore {name!r}: an installed tool of that name already exists at {envelope}"
        )
    archived = superseded_artifact_path(artifacts_dir, name, retired.archived_artifact_id)
    payload = _read_json_object(archived, what=f"archived envelope of {name!r}")
    if payload is None:
        raise LearnedToolError(f"cannot restore {name!r}: no archived envelope at {archived}")
    write_learned_tool(tools_dir=tools_dir, artifact=LearnedToolArtifact.from_dict(payload))
    _write_json(envelope, payload)
    await skills.restore(name)
    telemetry = get_observability()
    telemetry.event(
        "ravn.learned_tool.curation.restored",
        attributes={"ravn.learned_tool.name": name},
        content={"why": why, "retired_reason": retired.archived_reason},
    )
    telemetry.count(
        "ravn.learned_tool.curation.restores",
        description="Retired learned tools put back into use.",
    )
    logger.info("Restored retired learned tool %s: %s", name, why)
    return retired


async def retire_superseded_predecessor(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    skills: Any,
    artifact: LearnedToolArtifact,
) -> str:
    """Retire the differently named tool a just-installed version supersedes.

    Version chaining used to leave the predecessor installed beside its
    replacement ("keeps its own envelope and stays installed"), so a renamed
    rebuild was a second tool forever. Returns the retired name, or "".
    """
    if not artifact.supersedes or skills is None:
        return ""
    for entry in tool_inventory(tools_dir=tools_dir, artifacts_dir=artifacts_dir, skills=skills):
        if entry.artifact.artifact_id != artifact.supersedes:
            continue
        if entry.name == artifact.manifest.name or skills.status(entry.name) is None:
            return ""
        await retire_learned_tool(
            tools_dir=tools_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name=entry.name,
            reason=RETIRED_SUPERSEDED,
            detail=f"version {artifact.version} was installed as {artifact.manifest.name!r}",
            superseded_by=artifact.manifest.name,
        )
        return entry.name
    return ""


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageMatch:
    """The existing tool a build request belongs to."""

    entry: ToolEntry
    #: ``capability_id``, ``name``, ``spelling`` or ``semantic``.
    matched_by: str
    reason: str


async def find_installed_capability(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    skills: LifecycleView | None,
    capability_id: str,
    name: str,
    description: str = "",
    inputs: Sequence[str] = (),
    matcher: CapabilityMatcherPort | None = None,
) -> LineageMatch | None:
    """Return the installed or retired tool that already serves this request.

    Tried in order of certainty: the declared ``capability_id``, the exact name,
    the same name in another spelling, and — when a matcher is configured — the
    same capability in other words, asked as "is this request the next version
    of that tool". A tool retired as superseded resolves to its successor.
    """
    entries = tool_inventory(
        tools_dir=tools_dir, artifacts_dir=artifacts_dir, skills=skills, include_retired=True
    )
    if not entries:
        return None
    match = _exact_lineage(entries, capability_id=capability_id.strip(), name=name.strip())
    if match is None and matcher is not None:
        subject = CapabilityDescriptor(
            key=f"request:{name}", name=name, description=description, inputs=tuple(inputs)
        )
        keyed = _by_key(entries)
        verdicts = await matcher.same_capability(
            subject, [entry.descriptor() for entry in entries], as_revision=True
        )
        same = sorted(
            (verdict for verdict in verdicts if verdict.same),
            key=lambda verdict: (_preference(keyed[verdict.candidate_key]), verdict.similarity),
            reverse=True,
        )
        if same:
            match = LineageMatch(
                entry=keyed[same[0].candidate_key], matched_by="semantic", reason=same[0].reason
            )
    if match is None:
        return None
    if match.entry.retired and match.entry.retired_reason == RETIRED_SUPERSEDED:
        successor_name = match.entry.lifecycle.superseded_by if match.entry.lifecycle else ""
        successor = next(
            (entry for entry in entries if not entry.retired and entry.name == successor_name),
            None,
        )
        if successor is not None:
            return LineageMatch(
                entry=successor,
                matched_by=match.matched_by,
                reason=(
                    f"{match.reason} {match.entry.name!r} was retired as superseded by "
                    f"{successor.name!r}".strip()
                ),
            )
    return match


def _by_key(entries: Sequence[ToolEntry]) -> dict[str, ToolEntry]:
    return {entry.descriptor().key: entry for entry in entries}


def _preference(entry: ToolEntry) -> tuple[bool, bool]:
    """Installed before retired, verified before not."""
    return (not entry.retired, entry.verified)


def _exact_lineage(
    entries: Sequence[ToolEntry],
    *,
    capability_id: str,
    name: str,
) -> LineageMatch | None:
    if not capability_id and not name:
        return None
    # At every level the better answer comes first: a variant family often
    # shares one capability id, and the request belongs to the one that runs.
    ordered = sorted(entries, key=_preference, reverse=True)
    if capability_id:
        for entry in ordered:
            if entry.artifact.source_gap_id.strip() == capability_id:
                return LineageMatch(
                    entry, "capability_id", f"declares capability {capability_id!r}"
                )
    if name:
        for entry in ordered:
            if entry.name == name:
                return LineageMatch(entry, "name", f"is named {name!r}")
    wanted = capability_key(capability_id or name)
    if wanted:
        for entry in ordered:
            if wanted in {capability_key(entry.capability), capability_key(entry.name)}:
                return LineageMatch(
                    entry, "spelling", f"is {capability_id or name!r} spelled differently"
                )
    return None


def lineage_problems(entry: ToolEntry, *, load_refusal: str | None = None) -> list[str]:
    """Why the existing tool does not already serve the need; empty when healthy.

    *load_refusal* is the execution backend's reason for refusing to load the
    tool (:func:`ravn.valkyrie_evolution.learned_tools.backend_load_refusal`),
    when it does: a tool this resident cannot run does not serve the need,
    whatever its verification says, and the way to serve it is a repair.
    """
    problems: list[str] = []
    if load_refusal:
        problems.append(f"this resident's execution backend refuses to load it: {load_refusal}")
    verification = entry.artifact.provenance.get("verification")
    if not isinstance(verification, dict):
        problems.append("it has no verification record, so it can never run")
    elif verification.get("ok") is not True:
        problems.append("its last verification failed, so it can never run")
    lifecycle = entry.lifecycle
    if lifecycle is not None and lifecycle.status == "archived" and not entry.retired:
        problems.append("it was rolled back after repeated failures")
    if lifecycle is not None and lifecycle.consecutive_failures:
        problems.append(f"its last {lifecycle.consecutive_failures} run(s) failed")
    return problems


def capability_matched_marker(*, by: str, same_as: str = "", reason: str = "") -> dict[str, str]:
    """Provenance value recording how an artifact's capability id was decided."""
    return {"by": by, "same_as": same_as, "reason": reason, "at": _now().isoformat()}


# ---------------------------------------------------------------------------
# Curation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FirstUseWindow:
    """The never-used window, derived from this resident's own tools."""

    #: Longest install-to-first-use wait known here; ``None`` until there is one.
    seconds: float | None
    #: The tool that set it, so the number can be checked.
    tool: str
    observations: int
    #: ``observed`` or ``bound`` (see :meth:`SkillLifecycle.first_use_evidence`).
    basis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def first_use_window(entries: Sequence[ToolEntry]) -> FirstUseWindow:
    longest: float | None = None
    tool = basis = ""
    observations = 0
    for entry in entries:
        evidence = entry.lifecycle.first_use_evidence() if entry.lifecycle else None
        if evidence is None:
            continue
        observations += 1
        if longest is None or evidence[0] > longest:
            longest, basis = evidence
            tool = entry.name
    return FirstUseWindow(seconds=longest, tool=tool, observations=observations, basis=basis)


@dataclass(frozen=True)
class RetirementDecision:
    name: str
    reason: str
    detail: str
    superseded_by: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class CurationPlan:
    """What a curation pass would retire, and the evidence behind it."""

    installed: int
    retire: list[RetirementDecision] = field(default_factory=list)
    #: Tools retired as unused that the current window no longer justifies.
    restore: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    #: Installed tools with no lifecycle record: nothing can hold a retirement.
    unmanaged: list[str] = field(default_factory=list)
    #: Tools the execution backend refuses to load, name -> the loader's reason.
    #: Most are also in ``retire``; a pinned or unmanaged one cannot be retired
    #: and is listed here instead, never counted in ``keep``.
    refused: dict[str, str] = field(default_factory=dict)
    groups: list[dict[str, Any]] = field(default_factory=list)
    window: FirstUseWindow = field(default_factory=lambda: FirstUseWindow(None, "", 0))
    #: Capability ids taken over by matching in this pass: name -> capability id.
    backfilled: dict[str, str] = field(default_factory=dict)
    judged: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "retire": [decision.to_dict() for decision in self.retire],
            "restore": list(self.restore),
            "keep": list(self.keep),
            "unmanaged": list(self.unmanaged),
            "refused": dict(self.refused),
            "groups": list(self.groups),
            "never_used_window": self.window.to_dict(),
            "backfilled": dict(self.backfilled),
            "judged": self.judged,
        }


class LearnedToolCurator:
    """Keep one learned tool per capability, reading the lifecycle registry.

    Runs inside the resident consolidation dream. First it backfills each
    installed artifact's capability id: an artifact whose id was never decided
    by matching is compared with the others by the capability judge and takes
    the id of the tool it duplicates, and the decision is written onto its
    envelope so it is not judged again. Then, in precedence order:

    * **superseded** — another installed, runnable tool has the same capability
      id, spelling, or an explicit ``supersedes`` link; the best-evidenced
      member of the group is kept.
    * **debug_leftover** — the name starts with a configured probe prefix and
      the tool never succeeded.
    * **unrunnable** — no passing verification, rolled back, or refused by the
      execution backend (``load_refusal``): the tool exists but this resident
      cannot load it, so it is not kept, and ``build_tool`` repairs it when it
      restores the retirement.
    * **unused** — never run, and installed longer than the longest
      install-to-first-use wait known here.

    Pinned tools, and tools with no lifecycle record, are never retired.
    """

    def __init__(
        self,
        *,
        state_dir: str | Path,
        skills: Any,
        matcher: CapabilityMatcherPort | None = None,
        leftover_name_prefixes: Sequence[str] = (),
        persist: bool = True,
        load_refusal: Callable[[LearnedToolArtifact], str | None] | None = None,
    ) -> None:
        self._tools_dir, self._artifacts_dir = learned_tool_storage(state_dir)
        self._skills = skills
        self._matcher = matcher
        self._leftover_prefixes = tuple(
            prefix.strip().lower() for prefix in leftover_name_prefixes if prefix.strip()
        )
        self._persist = persist
        # The execution backend's reason for refusing a tool (the resolver's
        # `load_refusal`), so curation judges "runnable" by the same loader
        # check that decides whether the tool is offered to the model.
        self._load_refusal = load_refusal
        self._refusals: dict[str, str] = {}

    async def plan(self) -> CurationPlan:
        everything = tool_inventory(
            tools_dir=self._tools_dir,
            artifacts_dir=self._artifacts_dir,
            skills=self._skills,
            include_retired=True,
        )
        installed = [entry for entry in everything if not entry.retired]
        plan = CurationPlan(installed=len(installed), window=first_use_window(everything))
        installed = await self._backfill_capabilities(installed, plan)
        self._refusals = self._backend_refusals(installed)
        candidates = []
        for entry in installed:
            if entry.lifecycle is None:
                plan.unmanaged.append(entry.name)
            elif not entry.lifecycle.pinned:
                candidates.append(entry)

        decided: dict[str, RetirementDecision] = {}
        for group in self._groups(candidates):
            keeper = max(group, key=self._keeper_rank)
            if not self._runnable(keeper):
                # Nothing in the group can run; each member is judged on its own.
                continue
            plan.groups.append(
                {"keep": keeper.name, "retire": sorted(m.name for m in group if m is not keeper)}
            )
            for member in group:
                if member is keeper:
                    continue
                decided[member.name] = RetirementDecision(
                    name=member.name,
                    reason=RETIRED_SUPERSEDED,
                    detail=f"{keeper.name!r} serves the same capability{_evidence(keeper)}",
                    superseded_by=keeper.name,
                )

        now = _now()
        for entry in candidates:
            if entry.name in decided:
                continue
            decision = self._decide_alone(entry, plan.window, now)
            if decision is not None:
                decided[entry.name] = decision

        plan.retire = sorted(decided.values(), key=lambda decision: decision.name)
        plan.refused = {
            name: reason for name, reason in sorted(self._refusals.items()) if name not in decided
        }
        plan.keep = sorted(
            entry.name
            for entry in installed
            if entry.name not in decided and entry.name not in self._refusals
        )
        plan.restore = sorted(
            entry.name
            for entry in everything
            if entry.retired
            and entry.retired_reason == RETIRED_UNUSED
            and not self._exceeds_window(entry, plan.window, now)
        )
        return plan

    async def curate(self) -> dict[str, Any]:
        """Plan, then restore and retire what the plan says. Returns the dream summary."""
        migrated = await migrate_legacy_curation_state(
            tools_dir=self._tools_dir, artifacts_dir=self._artifacts_dir, skills=self._skills
        )
        plan = await self.plan()
        for name in plan.restore:
            await restore_learned_tool(
                tools_dir=self._tools_dir,
                artifacts_dir=self._artifacts_dir,
                skills=self._skills,
                name=name,
                why="the never-used window no longer supports retiring it",
            )
        for decision in plan.retire:
            await retire_learned_tool(
                tools_dir=self._tools_dir,
                artifacts_dir=self._artifacts_dir,
                skills=self._skills,
                name=decision.name,
                reason=decision.reason,
                detail=decision.detail,
                superseded_by=decision.superseded_by,
            )
        return {
            "installed_before": plan.installed,
            "retired": [decision.to_dict() for decision in plan.retire],
            "restored": list(plan.restore),
            "backfilled": dict(plan.backfilled),
            "kept": len(plan.keep),
            "unmanaged": list(plan.unmanaged),
            "refused": dict(plan.refused),
            "never_used_window_seconds": plan.window.seconds,
            "judged": plan.judged,
            "migrated_legacy_retirements": migrated,
        }

    # -- capability backfill --------------------------------------------------

    async def _backfill_capabilities(
        self, installed: list[ToolEntry], plan: CurationPlan
    ) -> list[ToolEntry]:
        """Decide a capability id for every artifact that has none, or was never matched."""
        current = list(installed)
        for index, entry in enumerate(current):
            if not entry.artifact.source_gap_id.strip():
                current[index] = self._with_capability(entry, entry.capability, marker=None)
        if self._matcher is None:
            return current
        for name in sorted(entry.name for entry in current):
            index = next(i for i, item in enumerate(current) if item.name == name)
            entry = current[index]
            if entry.matched:
                continue
            others = [item.descriptor() for item in current if item is not entry]
            verdicts = await self._matcher.same_capability(entry.descriptor(), others)
            plan.judged += len(verdicts)
            keyed = _by_key(current)
            same = sorted(
                (verdict for verdict in verdicts if verdict.same),
                key=lambda verdict: verdict.similarity,
                reverse=True,
            )
            target = keyed[same[0].candidate_key] if same else None
            capability = target.capability if target is not None else entry.capability
            current[index] = self._with_capability(
                entry,
                capability,
                marker=capability_matched_marker(
                    by="curation",
                    same_as=target.name if target is not None else "",
                    reason=same[0].reason if same else "",
                ),
            )
            if target is not None:
                plan.backfilled[entry.name] = capability
        return current

    def _with_capability(
        self,
        entry: ToolEntry,
        capability: str,
        *,
        marker: dict[str, str] | None,
    ) -> ToolEntry:
        provenance = dict(entry.artifact.provenance)
        if marker is not None:
            provenance[CAPABILITY_MATCHED_KEY] = marker
        artifact = replace(entry.artifact, source_gap_id=capability, provenance=provenance)
        if self._persist:
            # Same artifact id, written in place: the decision travels with the
            # envelope, and a pass cut short by a restart resumes from it.
            _write_json(
                learned_tool_artifact_path(self._artifacts_dir, artifact.manifest.name),
                artifact.to_dict(),
            )
        return replace(entry, artifact=artifact)

    # -- grouping and decisions -----------------------------------------------

    @staticmethod
    def _groups(entries: Sequence[ToolEntry]) -> list[list[ToolEntry]]:
        parent = {entry.name: entry.name for entry in entries}

        def find(name: str) -> str:
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        def union(left: str, right: str) -> None:
            parent[find(left)] = find(right)

        by_capability: dict[str, str] = {}
        by_spelling: dict[str, str] = {}
        by_artifact = {entry.artifact.artifact_id: entry.name for entry in entries}
        for entry in entries:
            union(entry.name, by_capability.setdefault(entry.capability, entry.name))
            union(entry.name, by_spelling.setdefault(capability_key(entry.name), entry.name))
            predecessor = by_artifact.get(entry.artifact.supersedes)
            if predecessor is not None and predecessor != entry.name:
                union(entry.name, predecessor)
        groups: dict[str, list[ToolEntry]] = {}
        for entry in entries:
            groups.setdefault(find(entry.name), []).append(entry)
        return [members for members in groups.values() if len(members) > 1]

    def _decide_alone(
        self,
        entry: ToolEntry,
        window: FirstUseWindow,
        now: datetime,
    ) -> RetirementDecision | None:
        lifecycle = entry.lifecycle
        if lifecycle is None:
            return None
        first_token = re.split(r"[_.\-]", entry.name.strip().lower(), maxsplit=1)[0]
        if first_token in self._leftover_prefixes and lifecycle.success_count == 0:
            return RetirementDecision(
                name=entry.name,
                reason=RETIRED_DEBUG_LEFTOVER,
                detail=f"named as a {first_token} probe and never succeeded at a real run",
            )
        unrunnable = self._unrunnable_reason(entry)
        if unrunnable is not None:
            return RetirementDecision(name=entry.name, reason=RETIRED_UNRUNNABLE, detail=unrunnable)
        if lifecycle.run_count > 0 or window.seconds is None:
            return None
        if not self._exceeds_window(entry, window, now):
            return None
        waited = (
            "waited before its first use"
            if window.basis == "observed"
            else "can have waited before its first use (created to its last use on record)"
        )
        return RetirementDecision(
            name=entry.name,
            reason=RETIRED_UNUSED,
            detail=(
                f"never run in {_days(self._age(entry, now))} since install; the longest any "
                f"tool here {waited} is {_days(window.seconds)} ({window.tool})"
            ),
        )

    def _exceeds_window(self, entry: ToolEntry, window: FirstUseWindow, now: datetime) -> bool:
        if window.seconds is None:
            return False
        return self._age(entry, now) > window.seconds

    @staticmethod
    def _age(entry: ToolEntry, now: datetime) -> float:
        stamps = [
            stamp
            for stamp in (
                _parse_time(entry.lifecycle.created_at if entry.lifecycle else ""),
                _parse_time(entry.artifact.created_at),
            )
            if stamp is not None
        ]
        return (now - min(stamps)).total_seconds() if stamps else 0.0

    def _keeper_rank(self, entry: ToolEntry) -> tuple[Any, ...]:
        lifecycle = entry.lifecycle
        return (
            self._runnable(entry),
            bool(lifecycle and lifecycle.success_count),
            lifecycle.last_used_at if lifecycle else "",
            lifecycle.success_count if lifecycle else 0,
            entry.artifact.version,
            entry.artifact.created_at,
        )

    def _backend_refusals(self, installed: Sequence[ToolEntry]) -> dict[str, str]:
        """Installed, verified tools the execution backend refuses to load.

        An unverified tool is already unrunnable on its own, with its own
        reason; only a tool that would otherwise run is asked of the backend.
        """
        if self._load_refusal is None:
            return {}
        refusals: dict[str, str] = {}
        for entry in installed:
            if not entry.verified:
                continue
            reason = self._load_refusal(entry.artifact)
            if reason is not None:
                refusals[entry.name] = reason
        return refusals

    def _unrunnable_reason(self, entry: ToolEntry) -> str | None:
        """Why *entry* cannot run on this resident, or ``None`` when it can."""
        if not entry.verified:
            return "has no passing verification, so learned_tool_run refuses it"
        if entry.lifecycle is not None and entry.lifecycle.status == "archived":
            return "rolled back after repeated failures"
        refusal = self._refusals.get(entry.name)
        if refusal is not None:
            return (
                "this resident's execution backend refuses to load it, so learned_tool_run "
                f"cannot run it and capability_list does not offer it: {refusal}"
            )
        return None

    def _runnable(self, entry: ToolEntry) -> bool:
        return self._unrunnable_reason(entry) is None


def _evidence(keeper: ToolEntry) -> str:
    lifecycle = keeper.lifecycle
    if lifecycle is None or not lifecycle.run_count:
        return ""
    return (
        f" ({lifecycle.success_count}/{lifecycle.run_count} successful runs, "
        f"last {lifecycle.last_used_at[:10]})"
    )


def _days(seconds: float) -> str:
    days = seconds / 86400
    if days >= 1:
        return f"{days:.1f} days"
    return f"{seconds / 3600:.1f} hours"


# ---------------------------------------------------------------------------
# One-time migration of the first deployment's separate stores
# ---------------------------------------------------------------------------

_LEGACY_RETIRED_DIRNAME = "_retired"
_LEGACY_CURATION_DIRNAME = "_curation"


async def migrate_legacy_curation_state(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    skills: Any,
) -> int:
    """Fold dev.352–dev.366 curation state into the archive and lifecycle records.

    Those builds kept retired tools under ``_retired/`` with reasons in
    ``_curation/retirements.json``, and capability verdicts in
    ``_curation/verdicts.json``. Retired envelopes move to the archive with
    their reason on the lifecycle record; verdicts become capability ids on the
    installed envelopes, so nothing is judged twice. Returns how many retired
    tools were moved. Idempotent: with nothing left to fold, it does nothing.
    """
    tools_path, artifacts_path = Path(tools_dir), Path(artifacts_dir)
    curation = artifacts_path / _LEGACY_CURATION_DIRNAME
    retired_dir = artifacts_path / _LEGACY_RETIRED_DIRNAME
    if not curation.exists() and not retired_dir.exists():
        return 0
    legacy = _read_json_object(curation / "retirements.json", what="legacy retirements") or {}
    records = legacy.get("retired") if isinstance(legacy.get("retired"), dict) else {}
    moved = 0
    for path in sorted(retired_dir.glob("*.json")) if retired_dir.is_dir() else []:
        payload = _read_json_object(path, what="legacy retired envelope") or {}
        name = str((payload.get("manifest") or {}).get("name") or "")
        artifact_id = str(payload.get("artifact_id") or "")
        if not name:
            continue
        _write_json(superseded_artifact_path(artifacts_path, name, artifact_id), payload)
        path.unlink()
        (tools_path / _LEGACY_RETIRED_DIRNAME / f"{path.stem}.py").unlink(missing_ok=True)
        record = records.get(name)
        record = record if isinstance(record, dict) else {}
        if skills.status(name) is not None:
            await skills.archive(
                name,
                reason=str(record.get("reason") or RETIRED_SUPERSEDED),
                detail=str(record.get("detail") or ""),
                superseded_by=str(record.get("superseded_by") or ""),
                artifact_id=artifact_id,
            )
        moved += 1
    _fold_legacy_verdicts(tools_path, artifacts_path, curation / "verdicts.json", skills)
    for directory in (curation, retired_dir, tools_path / _LEGACY_RETIRED_DIRNAME):
        shutil.rmtree(directory, ignore_errors=True)
    if moved:
        logger.info("Moved %d legacy retired learned tool(s) into the archive", moved)
    return moved


def _fold_legacy_verdicts(tools_dir: Path, artifacts_dir: Path, path: Path, skills: Any) -> None:
    """Turn legacy "same capability" verdicts into shared capability ids.

    Verdicts join tools transitively (A~B and B~C put A, B and C in one
    capability), so they are folded as connected groups. The tools are not
    marked as matched: the dream's backfill judges them against the current
    library, which the verdicts predate.
    """
    payload = _read_json_object(path, what="legacy capability verdicts") or {}
    pairs = payload.get("pairs") if isinstance(payload.get("pairs"), dict) else {}
    if not pairs:
        return
    installed = {
        entry.artifact.artifact_id: entry
        for entry in tool_inventory(tools_dir=tools_dir, artifacts_dir=artifacts_dir, skills=skills)
    }
    parent = {key: key for key in installed}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for pair, verdict in pairs.items():
        left, _, right = pair.partition("|")
        if isinstance(verdict, dict) and verdict.get("same"):
            if left in installed and right in installed:
                parent[find(left)] = find(right)
    groups: dict[str, list[str]] = {}
    for key in installed:
        groups.setdefault(find(key), []).append(key)
    for members in groups.values():
        if len(members) < 2:
            continue
        capability = installed[sorted(members)[0]].capability
        for key in members:
            entry = installed[key]
            artifact = replace(entry.artifact, source_gap_id=capability)
            _write_json(
                learned_tool_artifact_path(artifacts_dir, artifact.manifest.name),
                artifact.to_dict(),
            )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def learned_tool_health(
    state_dir: str | Path,
    skills: LifecycleView,
    *,
    refused: Callable[[], Sequence[Any]] | None = None,
) -> dict[str, int]:
    """Counts for the resident health snapshot, from the lifecycle registry.

    *refused* lists the installed tools the execution backend refuses to load
    (the resolver's ``list_refused``); their count is
    ``learned_tools_refused``. It is a standing condition, recounted on every
    snapshot, so it reads zero only once each refused tool has been repaired.
    """
    tools_dir, artifacts_dir = learned_tool_storage(state_dir)
    entries = tool_inventory(
        tools_dir=tools_dir, artifacts_dir=artifacts_dir, skills=skills, include_retired=True
    )
    installed = [entry for entry in entries if not entry.retired]
    retired = [entry for entry in entries if entry.retired]
    snapshot = {
        "learned_tools_installed": len(installed),
        "learned_tools_never_used": sum(
            1 for entry in installed if not entry.lifecycle or not entry.lifecycle.run_count
        ),
        "learned_tools_retired": len(retired),
        "learned_tool_runs_total": sum(
            entry.lifecycle.run_count for entry in entries if entry.lifecycle
        ),
        "learned_tool_restores_total": sum(
            entry.lifecycle.restore_count for entry in entries if entry.lifecycle
        ),
    }
    if refused is not None:
        snapshot["learned_tools_refused"] = len(refused())
    for reason in RETIREMENT_REASONS:
        snapshot[f"learned_tools_retired.{reason}"] = sum(
            1 for entry in retired if entry.retired_reason == reason
        )
    return snapshot
