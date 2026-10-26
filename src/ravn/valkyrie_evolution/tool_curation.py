"""Usage evidence, capability lineage, and retirement for learned tools.

Residents built learned tools and mostly did not use them. A failing tool was
answered with a new variant instead of a repair (one resident held twelve
``list_pods_in_namespace*`` tools), nothing removed the variants, debug probes
or never-used adoptions, and every one of them stayed in ``capability_list``
where it made the next search worse.

This module holds the three pieces that stop that:

* :class:`LearnedToolUsageLedger` — per-tool runs, successes and failures,
  first and last use, per version, kept beside the artifacts in
  ``learned_tool_artifacts/_curation/usage.json``.
* :func:`find_lineage` — the installed (or retired) tool that already serves a
  requested capability, so ``build_tool`` repairs or restores it instead of
  adding a sibling.
* :class:`LearnedToolCurator` — the consolidation-dream pass that retires
  superseded, unrunnable, debug-leftover and never-used tools. Retirement moves
  the files under ``_retired/`` and records why; :func:`restore_learned_tool`
  puts them back.

The window for "never used" is not a constant: it is the longest time any tool
on this resident has waited between being installed and its first use. A tool
that has waited longer than every tool that did turn out to be needed is
retired; when it is asked for again it is restored, and its first use widens
the window.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from niuu.observability import get_observability
from ravn.ports.capability_matching import CapabilityDescriptor, CapabilityMatcherPort
from ravn.valkyrie_evolution.learned_tools import (
    CURATION_DIRNAME,
    RETIRED_DIRNAME,
    LearnedToolError,
    LearnedToolUsageError,
    capability_key,
    learned_tool_artifact_path,
    learned_tool_path,
    learned_tool_storage,
    retired_artifact_path,
    retired_tool_path,
)
from ravn.valkyrie_evolution.models import LearnedToolArtifact

logger = logging.getLogger(__name__)

USAGE_FILENAME = "usage.json"
RETIREMENTS_FILENAME = "retirements.json"
VERDICTS_FILENAME = "verdicts.json"

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

# Re-entrant: a write re-reads the file, and the first read seeds it.
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path.resolve(), threading.RLock())


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
        raise LearnedToolError(
            f"{what} at {path} is not valid JSON ({exc}); fix or move the file — it is "
            "never silently replaced, because that would erase the history it holds"
        ) from exc
    if not isinstance(payload, dict):
        raise LearnedToolError(f"{what} at {path} is not a JSON object")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def curation_dir(artifacts_dir: str | Path) -> Path:
    return Path(artifacts_dir) / CURATION_DIRNAME


# ---------------------------------------------------------------------------
# Usage ledger
# ---------------------------------------------------------------------------


@dataclass
class ToolUsage:
    """Everything known about how one learned tool has been used."""

    name: str
    #: When the tool's lineage was first installed here ("" when unknown).
    installed_at: str = ""
    #: The installed version's artifact id, as last recorded.
    artifact_id: str = ""
    runs: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    first_used_at: str = ""
    #: True when ``first_used_at`` was observed, not inferred. Only observed
    #: first uses feed the never-used window.
    first_use_observed: bool = False
    last_used_at: str = ""
    last_success_at: str = ""
    last_failure_at: str = ""
    #: Counters for the version recorded in ``version_artifact_id`` alone, so a
    #: repaired tool is judged on the new version, not on the one it replaced.
    version_artifact_id: str = ""
    version_runs: int = 0
    version_successes: int = 0
    version_failures: int = 0
    repairs: int = 0
    #: Where the pre-ledger history came from ("" for a tool first seen here).
    seeded_from: str = ""
    #: For a tool used before the ledger existed, whose first use was not
    #: recorded: the longest its first use can have taken (install to its last
    #: use before the ledger). An upper bound, frozen once set, so it never
    #: grows with later use.
    first_use_wait_bound_seconds: float | None = None
    bound_frozen: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ToolUsage:
        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def first_use_wait_seconds(self) -> float | None:
        """Seconds between install and first use, when both were observed."""
        if not self.first_use_observed:
            return None
        installed = _parse_time(self.installed_at)
        first = _parse_time(self.first_used_at)
        if installed is None or first is None or first < installed:
            return None
        return (first - installed).total_seconds()

    def first_use_evidence(self) -> tuple[float, str] | None:
        """``(seconds, basis)`` this tool contributes to the never-used window.

        ``observed`` when its first use was recorded, ``bound`` when only an
        upper bound is known. Counting the bound keeps the window honest on a
        resident whose history predates the ledger: one fast first use seen
        today says nothing about how long older tools waited.
        """
        wait = self.first_use_wait_seconds()
        if wait is not None:
            return wait, "observed"
        if self.first_use_wait_bound_seconds is not None:
            return self.first_use_wait_bound_seconds, "bound"
        return None

    def freeze_bound(self) -> None:
        """Record the first-use bound of a tool seeded with unrecorded first use."""
        if self.bound_frozen:
            return
        self.bound_frozen = True
        if self.first_use_observed or not self.runs:
            return
        installed = _parse_time(self.installed_at)
        last = _parse_time(self.last_used_at)
        if installed is None or last is None or last < installed:
            return
        self.first_use_wait_bound_seconds = (last - installed).total_seconds()


@dataclass(frozen=True)
class FirstUseWindow:
    """The never-used window, derived from this resident's own tools."""

    #: Longest install-to-first-use wait known here; ``None`` until there is one.
    seconds: float | None
    #: The tool that set it, so the number can be checked.
    tool: str
    observations: int
    #: ``observed`` or ``bound`` (see :meth:`ToolUsage.first_use_evidence`).
    basis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seconds": self.seconds,
            "tool": self.tool,
            "observations": self.observations,
            "basis": self.basis,
        }


class LearnedToolUsageLedger:
    """Durable per-tool usage evidence, stored beside the learned-tool artifacts.

    Every write re-reads the file first, so several ledgers on the same state
    (the dispatch tool's, build_tool's, the curator's) never undo each other.
    The first load on a resident that predates the ledger seeds it from the
    skill lifecycle registry (``skill_management.json``), which counted runs
    before this existed; without that, every tool would look never-used.
    """

    def __init__(
        self,
        artifacts_dir: str | Path,
        *,
        lifecycle_path: str | Path | None = None,
        persist: bool = True,
    ) -> None:
        self._artifacts_dir = Path(artifacts_dir)
        self._path = curation_dir(self._artifacts_dir) / USAGE_FILENAME
        self._lifecycle_path = (
            Path(lifecycle_path)
            if lifecycle_path is not None
            else self._artifacts_dir.parent / "skill_management.json"
        )
        self._persist = persist
        self._seeded: dict[str, ToolUsage] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def snapshot(self) -> dict[str, ToolUsage]:
        """All recorded usage, keyed by tool name."""
        return self._load()

    def get(self, name: str) -> ToolUsage | None:
        return self._load().get(name)

    def record_install(self, name: str, artifact_id: str, *, repair: bool = False) -> ToolUsage:
        """Note that *artifact_id* is now the installed version of *name*."""

        def apply(entries: dict[str, ToolUsage]) -> ToolUsage:
            now = _now().isoformat()
            usage = entries.get(name) or ToolUsage(name=name, installed_at=now)
            if not usage.installed_at:
                usage.installed_at = now
            if usage.artifact_id != artifact_id:
                usage.artifact_id = artifact_id
                usage.version_artifact_id = artifact_id
                usage.version_runs = usage.version_successes = usage.version_failures = 0
                # A new version starts without the old one's failure streak.
                usage.consecutive_failures = 0
            if repair:
                usage.repairs += 1
            entries[name] = usage
            return usage

        return self._mutate(apply)

    def record_run(self, name: str, artifact_id: str, succeeded: bool) -> None:
        """Record one finished run. Matches :data:`LearnedToolUsageRecorder`."""

        def apply(entries: dict[str, ToolUsage]) -> ToolUsage:
            now = _now().isoformat()
            usage = entries.get(name)
            if usage is None:
                usage = ToolUsage(name=name, installed_at=self._envelope_created_at(name))
            if usage.runs == 0 and not usage.first_used_at:
                usage.first_used_at = now
                usage.first_use_observed = True
            usage.runs += 1
            usage.last_used_at = now
            if succeeded:
                usage.successes += 1
                usage.consecutive_failures = 0
                usage.last_success_at = now
            else:
                usage.failures += 1
                usage.consecutive_failures += 1
                usage.last_failure_at = now
            if artifact_id and usage.version_artifact_id != artifact_id:
                usage.version_artifact_id = artifact_id
                usage.version_runs = usage.version_successes = usage.version_failures = 0
            if artifact_id:
                usage.artifact_id = artifact_id
            usage.version_runs += 1
            if succeeded:
                usage.version_successes += 1
            else:
                usage.version_failures += 1
            entries[name] = usage
            return usage

        self._mutate(apply)

    def first_use_window(self) -> FirstUseWindow:
        longest: float | None = None
        tool = basis = ""
        observations = 0
        for usage in self._load().values():
            evidence = usage.first_use_evidence()
            if evidence is None:
                continue
            observations += 1
            if longest is None or evidence[0] > longest:
                longest, basis = evidence
                tool = usage.name
        return FirstUseWindow(seconds=longest, tool=tool, observations=observations, basis=basis)

    # -- storage ------------------------------------------------------------

    def _mutate(self, apply: Any) -> ToolUsage:
        if not self._persist:
            raise LearnedToolUsageError(
                f"usage ledger at {self._path} was opened read-only; nothing may be recorded"
            )
        with _lock_for(self._path):
            entries = self._load()
            usage = apply(entries)
            try:
                _write_json_atomic(
                    self._path,
                    {"version": 1, "tools": {key: asdict(value) for key, value in entries.items()}},
                )
            except OSError as exc:
                raise LearnedToolUsageError(
                    f"could not write the learned-tool usage ledger at {self._path}: {exc}"
                ) from exc
            return usage

    def _load(self) -> dict[str, ToolUsage]:
        try:
            payload = _read_json_object(self._path, what="learned-tool usage ledger")
        except LearnedToolError as exc:
            raise LearnedToolUsageError(str(exc)) from exc
        if payload is None:
            return self._seed()
        tools = payload.get("tools")
        if payload.get("version") != 1 or not isinstance(tools, dict):
            raise LearnedToolUsageError(
                f"learned-tool usage ledger at {self._path} has an unknown layout; "
                "move it aside to re-seed it from skill_management.json"
            )
        entries = {
            str(name): ToolUsage.from_dict({**value, "name": str(name)})
            for name, value in tools.items()
            if isinstance(value, dict)
        }
        for usage in entries.values():
            # Ledgers seeded before bounds existed get theirs on first load;
            # the next write persists it.
            if usage.seeded_from == "skill_management":
                usage.freeze_bound()
        return entries

    def _seed(self) -> dict[str, ToolUsage]:
        """Build the first ledger from the envelopes and the lifecycle registry."""
        if self._seeded is not None:
            return {name: ToolUsage(**asdict(usage)) for name, usage in self._seeded.items()}
        lifecycle = _read_json_object(self._lifecycle_path, what="skill lifecycle registry") or {}
        entries: dict[str, ToolUsage] = {}
        for envelope in _envelopes(self._artifacts_dir):
            name = str((envelope.get("manifest") or {}).get("name") or "")
            if not name:
                continue
            row = lifecycle.get(name.strip().lower())
            row = row if isinstance(row, dict) else {}
            created = [
                value
                for value in (
                    _parse_time(str(row.get("created_at") or "")),
                    _parse_time(str(envelope.get("created_at") or "")),
                )
                if value is not None
            ]
            runs = int(row.get("run_count") or 0)
            last_used = str(row.get("last_used_at") or "")
            usage = ToolUsage(
                name=name,
                installed_at=min(created).isoformat() if created else "",
                artifact_id=str(envelope.get("artifact_id") or ""),
                runs=runs,
                successes=int(row.get("success_count") or 0),
                failures=int(row.get("failure_count") or 0),
                consecutive_failures=int(row.get("consecutive_failures") or 0),
                last_used_at=last_used,
                seeded_from="skill_management" if row else "artifact",
            )
            if runs == 1 and last_used:
                # One run: its last use is its first, so the wait is known.
                usage.first_used_at = last_used
                usage.first_use_observed = True
            if row:
                usage.freeze_bound()
            entries[name] = usage
        if self._persist:
            with _lock_for(self._path):
                if not self._path.exists():
                    _write_json_atomic(
                        self._path,
                        {
                            "version": 1,
                            "tools": {key: asdict(value) for key, value in entries.items()},
                        },
                    )
                    logger.info(
                        "learned-tool usage ledger seeded with %d tool(s) at %s",
                        len(entries),
                        self._path,
                    )
        else:
            self._seeded = entries
        return {name: ToolUsage(**asdict(usage)) for name, usage in entries.items()}

    def _envelope_created_at(self, name: str) -> str:
        path = learned_tool_artifact_path(self._artifacts_dir, name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(payload.get("created_at") or "") if isinstance(payload, dict) else ""


def _envelopes(artifacts_dir: Path) -> Iterable[dict[str, Any]]:
    """Raw installed and retired envelopes; unreadable ones are not this pass's to fix."""
    for directory in (artifacts_dir, artifacts_dir / RETIRED_DIRNAME):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                yield payload


# ---------------------------------------------------------------------------
# Retirement records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetirementRecord:
    name: str
    reason: str
    detail: str
    artifact_id: str
    version: int
    retired_at: str
    superseded_by: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetirementRecord:
        return cls(
            name=str(data.get("name") or ""),
            reason=str(data.get("reason") or ""),
            detail=str(data.get("detail") or ""),
            artifact_id=str(data.get("artifact_id") or ""),
            version=int(data.get("version") or 1),
            retired_at=str(data.get("retired_at") or ""),
            superseded_by=str(data.get("superseded_by") or ""),
        )


class LearnedToolRetirements:
    """Which tools are retired, why, and how many retirements and restores happened."""

    def __init__(self, artifacts_dir: str | Path) -> None:
        self._path = curation_dir(artifacts_dir) / RETIREMENTS_FILENAME

    def records(self) -> dict[str, RetirementRecord]:
        payload = self._load()
        return {
            name: RetirementRecord.from_dict({**value, "name": name})
            for name, value in dict(payload.get("retired") or {}).items()
            if isinstance(value, dict)
        }

    def get(self, name: str) -> RetirementRecord | None:
        return self.records().get(name)

    def counts(self) -> dict[str, int]:
        return {key: int(value) for key, value in dict(self._load().get("counts") or {}).items()}

    def record_retirement(self, record: RetirementRecord) -> None:
        def apply(payload: dict[str, Any]) -> None:
            payload.setdefault("retired", {})[record.name] = asdict(record)
            counts = payload.setdefault("counts", {})
            counts[f"retired.{record.reason}"] = int(counts.get(f"retired.{record.reason}", 0)) + 1
            payload.setdefault("events", []).append({"event": "retired", **asdict(record)})

        self._mutate(apply)

    def record_restore(self, name: str, *, why: str) -> RetirementRecord | None:
        restored: list[RetirementRecord] = []

        def apply(payload: dict[str, Any]) -> None:
            raw = dict(payload.setdefault("retired", {})).get(name)
            if isinstance(raw, dict):
                restored.append(RetirementRecord.from_dict({**raw, "name": name}))
            payload["retired"].pop(name, None)
            counts = payload.setdefault("counts", {})
            counts["restored"] = int(counts.get("restored", 0)) + 1
            payload.setdefault("events", []).append(
                {"event": "restored", "name": name, "why": why, "at": _now().isoformat()}
            )

        self._mutate(apply)
        return restored[0] if restored else None

    def _load(self) -> dict[str, Any]:
        payload = _read_json_object(self._path, what="learned-tool retirement records")
        return payload or {"version": 1, "retired": {}, "counts": {}, "events": []}

    def _mutate(self, apply: Any) -> None:
        with _lock_for(self._path):
            payload = self._load()
            apply(payload)
            payload["version"] = 1
            _write_json_atomic(self._path, payload)


def retire_learned_tool(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    name: str,
    reason: str,
    detail: str,
    superseded_by: str = "",
) -> RetirementRecord:
    """Move one installed tool under ``_retired/`` and record why. Reversible."""
    if reason not in RETIREMENT_REASONS:
        raise LearnedToolError(f"unknown retirement reason {reason!r}")
    envelope = learned_tool_artifact_path(artifacts_dir, name)
    payload = _read_json_object(envelope, what=f"envelope of learned tool {name!r}")
    if payload is None:
        raise LearnedToolError(f"cannot retire {name!r}: no installed envelope at {envelope}")
    code = learned_tool_path(tools_dir, name)
    retired_envelope = retired_artifact_path(artifacts_dir, name)
    retired_code = retired_tool_path(tools_dir, name)
    if retired_envelope.exists():
        raise LearnedToolError(
            f"cannot retire {name!r}: a retired copy already exists at {retired_envelope}; "
            "restore or remove it first"
        )
    retired_envelope.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(envelope), str(retired_envelope))
    if code.exists():
        retired_code.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(code), str(retired_code))
    record = RetirementRecord(
        name=name,
        reason=reason,
        detail=detail,
        artifact_id=str(payload.get("artifact_id") or ""),
        version=int(payload.get("version") or 1),
        retired_at=_now().isoformat(),
        superseded_by=superseded_by,
    )
    LearnedToolRetirements(artifacts_dir).record_retirement(record)
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
    return record


def restore_learned_tool(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    name: str,
    why: str,
) -> RetirementRecord | None:
    """Put a retired tool back where it is installed and drop its retirement record."""
    retired_envelope = retired_artifact_path(artifacts_dir, name)
    if not retired_envelope.is_file():
        raise LearnedToolError(f"cannot restore {name!r}: it is not retired")
    envelope = learned_tool_artifact_path(artifacts_dir, name)
    if envelope.exists():
        raise LearnedToolError(
            f"cannot restore {name!r}: an installed tool of that name already exists at {envelope}"
        )
    shutil.move(str(retired_envelope), str(envelope))
    retired_code = retired_tool_path(tools_dir, name)
    if retired_code.exists():
        code = learned_tool_path(tools_dir, name)
        code.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(retired_code), str(code))
    record = LearnedToolRetirements(artifacts_dir).record_restore(name, why=why)
    telemetry = get_observability()
    telemetry.event(
        "ravn.learned_tool.curation.restored",
        attributes={"ravn.learned_tool.name": name},
        content={"why": why},
    )
    telemetry.count(
        "ravn.learned_tool.curation.restores",
        description="Retired learned tools put back into use.",
    )
    logger.info("Restored retired learned tool %s: %s", name, why)
    return record


# ---------------------------------------------------------------------------
# Inventory and lineage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolEntry:
    """One installed or retired learned tool, as curation and lineage see it."""

    artifact: LearnedToolArtifact
    retired: RetirementRecord | None = None

    @property
    def name(self) -> str:
        return self.artifact.manifest.name

    @property
    def verified(self) -> bool:
        verification = self.artifact.provenance.get("verification")
        return isinstance(verification, dict) and verification.get("ok") is True

    @property
    def capability(self) -> str:
        return self.artifact.source_gap_id.strip()

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
    include_retired: bool = False,
) -> list[ToolEntry]:
    """Installed tools (code on disk), and optionally retired ones."""
    artifacts_path = Path(artifacts_dir)
    entries: list[ToolEntry] = []
    for path in sorted(artifacts_path.glob("*.json")) if artifacts_path.is_dir() else []:
        artifact = _artifact_at(path)
        if artifact is None or not learned_tool_path(tools_dir, artifact.manifest.name).is_file():
            continue
        entries.append(ToolEntry(artifact=artifact))
    if include_retired:
        records = LearnedToolRetirements(artifacts_path).records()
        retired_dir = artifacts_path / RETIRED_DIRNAME
        for path in sorted(retired_dir.glob("*.json")) if retired_dir.is_dir() else []:
            artifact = _artifact_at(path)
            if artifact is None:
                continue
            record = records.get(artifact.manifest.name) or RetirementRecord(
                name=artifact.manifest.name,
                reason="",
                detail="retired copy without a retirement record",
                artifact_id=artifact.artifact_id,
                version=artifact.version,
                retired_at="",
            )
            entries.append(ToolEntry(artifact=artifact, retired=record))
    return entries


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


@dataclass(frozen=True)
class LineageMatch:
    """The existing tool a build request belongs to."""

    entry: ToolEntry
    #: ``capability_id``, ``name``, ``spelling`` or ``semantic``.
    matched_by: str
    reason: str


async def find_lineage(
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path,
    capability_id: str,
    name: str,
    description: str,
    inputs: Sequence[str] = (),
    matcher: CapabilityMatcherPort | None = None,
) -> LineageMatch | None:
    """Return the installed or retired tool that already serves this request.

    Tried in order of certainty: the declared ``capability_id``, the exact name,
    the same name in another spelling, and — when a matcher is configured — the
    same capability in other words. A retired tool that was superseded resolves
    to the tool that superseded it.
    """
    entries = tool_inventory(tools_dir=tools_dir, artifacts_dir=artifacts_dir, include_retired=True)
    if not entries:
        return None
    by_name = {entry.name: entry for entry in entries if entry.retired is None}
    match = _exact_lineage(entries, capability_id=capability_id.strip(), name=name.strip())
    if match is None and matcher is not None:
        subject = CapabilityDescriptor(
            key=f"request:{name}",
            name=name,
            description=description,
            inputs=tuple(inputs),
        )
        verdicts = await matcher.same_capability(
            subject, [entry.descriptor() for entry in entries], as_revision=True
        )
        same = sorted(
            (verdict for verdict in verdicts if verdict.same),
            key=lambda verdict: verdict.similarity,
            reverse=True,
        )
        keyed = {entry.descriptor().key: entry for entry in entries}
        if same:
            match = LineageMatch(
                entry=keyed[same[0].candidate_key],
                matched_by="semantic",
                reason=same[0].reason,
            )
    if match is None:
        return None
    retired = match.entry.retired
    if retired is not None and retired.reason == RETIRED_SUPERSEDED:
        keeper = by_name.get(retired.superseded_by)
        if keeper is not None:
            return LineageMatch(
                entry=keeper,
                matched_by=match.matched_by,
                reason=(
                    f"{match.reason} {match.entry.name!r} was retired as superseded by "
                    f"{keeper.name!r}".strip()
                ),
            )
    return match


def _exact_lineage(
    entries: Sequence[ToolEntry],
    *,
    capability_id: str,
    name: str,
) -> LineageMatch | None:
    if not capability_id and not name:
        return None
    # At every level the better answer comes first: installed before retired,
    # verified before not. Two installed tools often share a capability id (a
    # variant family), and the one to tie the request to is the one that runs.
    ordered = sorted(entries, key=lambda entry: (entry.retired is not None, not entry.verified))
    if capability_id:
        for entry in ordered:
            if entry.capability == capability_id:
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
            if capability_key(entry.capability or entry.name) == wanted:
                return LineageMatch(
                    entry, "spelling", f"is {capability_id or name!r} spelled differently"
                )
    return None


def lineage_problems(
    entry: ToolEntry,
    usage: ToolUsage | None,
    *,
    lifecycle_status: str | None,
) -> list[str]:
    """Why the existing tool does not already serve the need; empty when healthy."""
    problems: list[str] = []
    verification = entry.artifact.provenance.get("verification")
    if not isinstance(verification, dict):
        problems.append("it has no verification record, so it can never run")
    elif verification.get("ok") is not True:
        problems.append("its last verification failed, so it can never run")
    if lifecycle_status == "archived":
        problems.append("it was rolled back after repeated failures")
    if usage is not None and usage.consecutive_failures:
        problems.append(f"its last {usage.consecutive_failures} run(s) failed")
    return problems


class LifecycleSnapshot:
    """Read-only view of the skill lifecycle registry, for planning outside the daemon.

    The running resident's registry is the only writer of that file; a report
    reads the statuses it needs (pinned, rolled back) and changes nothing.
    """

    def __init__(self, path: str | Path) -> None:
        payload = _read_json_object(Path(path), what="skill lifecycle registry") or {}
        self._rows = {
            str(key): dict(value) for key, value in payload.items() if isinstance(value, dict)
        }

    def status(self, name: str) -> str | None:
        row = self._rows.get(name.strip().lower())
        return str(row.get("status") or "") or None if row is not None else None

    def lifecycle_metadata(self, name: str) -> dict[str, Any] | None:
        row = self._rows.get(name.strip().lower())
        return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Curation
# ---------------------------------------------------------------------------


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
    groups: list[dict[str, Any]] = field(default_factory=list)
    window: FirstUseWindow = field(default_factory=lambda: FirstUseWindow(None, "", 0))
    judged_pairs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "retire": [decision.to_dict() for decision in self.retire],
            "restore": list(self.restore),
            "keep": list(self.keep),
            "groups": list(self.groups),
            "never_used_window": self.window.to_dict(),
            "judged_pairs": self.judged_pairs,
        }


class LearnedToolCurator:
    """Retire learned tools that no longer earn their place in the catalog.

    Runs inside the resident consolidation dream. Decisions, in precedence order:

    * **superseded** — another installed, runnable tool serves the same
      capability (declared capability id, the same name in another spelling, an
      explicit ``supersedes`` link, or the capability judge). The best-evidenced
      member of the group is kept.
    * **debug_leftover** — the name says it is a probe (configured prefixes)
      and it never succeeded at anything.
    * **unrunnable** — no passing verification, or rolled back: it can never
      run, so it only ever costs a catalog entry.
    * **unused** — never run, and installed longer than the longest observed
      install-to-first-use wait (:meth:`LearnedToolUsageLedger.first_use_window`).

    Pinned tools are never retired.
    """

    def __init__(
        self,
        *,
        state_dir: str | Path,
        skills: Any | None = None,
        matcher: CapabilityMatcherPort | None = None,
        leftover_name_prefixes: Sequence[str] = (),
        persist: bool = True,
    ) -> None:
        self._tools_dir, self._artifacts_dir = learned_tool_storage(state_dir)
        self._skills = skills
        self._matcher = matcher
        self._leftover_prefixes = tuple(
            prefix.strip().lower() for prefix in leftover_name_prefixes if prefix.strip()
        )
        self._persist = persist
        self._ledger = LearnedToolUsageLedger(self._artifacts_dir, persist=persist)
        self._verdicts_path = curation_dir(self._artifacts_dir) / VERDICTS_FILENAME

    @property
    def ledger(self) -> LearnedToolUsageLedger:
        return self._ledger

    async def plan(self) -> CurationPlan:
        entries = [
            entry
            for entry in tool_inventory(
                tools_dir=self._tools_dir, artifacts_dir=self._artifacts_dir
            )
            if not self._pinned(entry.name)
        ]
        usage = self._ledger.snapshot()
        plan = CurationPlan(installed=len(entries), window=self._ledger.first_use_window())
        groups, judged = await self._capability_groups(entries)
        plan.judged_pairs = judged
        decided: dict[str, RetirementDecision] = {}

        for group in groups:
            members = [entry for entry in entries if entry.name in group]
            keeper = max(members, key=lambda entry: self._keeper_rank(entry, usage))
            if not self._runnable(keeper):
                # Nothing in the group can run; each member is judged on its own.
                continue
            plan.groups.append(
                {"keep": keeper.name, "retire": sorted(m.name for m in members if m is not keeper)}
            )
            for member in members:
                if member is keeper:
                    continue
                decided[member.name] = RetirementDecision(
                    name=member.name,
                    reason=RETIRED_SUPERSEDED,
                    detail=(
                        f"{keeper.name!r} serves the same capability{self._evidence(keeper, usage)}"
                    ),
                    superseded_by=keeper.name,
                )

        now = _now()
        for entry in entries:
            if entry.name in decided:
                continue
            record = usage.get(entry.name)
            decision = self._decide_alone(entry, record, plan.window, now)
            if decision is not None:
                decided[entry.name] = decision

        plan.retire = sorted(decided.values(), key=lambda decision: decision.name)
        plan.keep = sorted(entry.name for entry in entries if entry.name not in decided)
        plan.restore = self._unjustified_unused(usage, plan.window, now)
        return plan

    def _unjustified_unused(
        self,
        usage: Mapping[str, ToolUsage],
        window: FirstUseWindow,
        now: datetime,
    ) -> list[str]:
        """Tools retired as unused whose wait the window no longer exceeds.

        The window only grows as evidence arrives; a retirement it no longer
        supports is undone, so an early, thin window cannot keep a tool away.
        """
        restore: list[str] = []
        for record in LearnedToolRetirements(self._artifacts_dir).records().values():
            if record.reason != RETIRED_UNUSED:
                continue
            entry_usage = usage.get(record.name)
            installed = _parse_time(entry_usage.installed_at if entry_usage else "") or (
                _parse_time(record.retired_at)
            )
            if window.seconds is None or installed is None:
                restore.append(record.name)
                continue
            if (now - installed).total_seconds() <= window.seconds:
                restore.append(record.name)
        return sorted(restore)

    async def curate(self) -> dict[str, Any]:
        """Plan, then retire what the plan says. Returns the dream summary."""
        plan = await self.plan()
        restored: list[str] = []
        for name in plan.restore:
            restore_learned_tool(
                tools_dir=self._tools_dir,
                artifacts_dir=self._artifacts_dir,
                name=name,
                why="the never-used window no longer supports retiring it",
            )
            if self._skills is not None and self._skills.status(name) == "archived":
                await self._skills.restore(name)
            restored.append(name)
        retired: list[dict[str, str]] = []
        for decision in plan.retire:
            retire_learned_tool(
                tools_dir=self._tools_dir,
                artifacts_dir=self._artifacts_dir,
                name=decision.name,
                reason=decision.reason,
                detail=decision.detail,
                superseded_by=decision.superseded_by,
            )
            await self._archive_lifecycle(decision.name)
            retired.append(decision.to_dict())
        return {
            "installed_before": plan.installed,
            "retired": retired,
            "restored": restored,
            "kept": len(plan.keep),
            "never_used_window_seconds": plan.window.seconds,
            "judged_pairs": plan.judged_pairs,
        }

    # -- decisions ----------------------------------------------------------

    def _decide_alone(
        self,
        entry: ToolEntry,
        usage: ToolUsage | None,
        window: FirstUseWindow,
        now: datetime,
    ) -> RetirementDecision | None:
        successes = usage.successes if usage is not None else 0
        first_token = re.split(r"[_.\-]", entry.name.strip().lower(), maxsplit=1)[0]
        if first_token in self._leftover_prefixes and successes == 0:
            return RetirementDecision(
                name=entry.name,
                reason=RETIRED_DEBUG_LEFTOVER,
                detail=f"named as a {first_token} probe and never succeeded at a real run",
            )
        status = self._skills.status(entry.name) if self._skills is not None else None
        if not entry.verified or status == "archived":
            why = (
                "rolled back after repeated failures"
                if entry.verified
                else "has no passing verification, so learned_tool_run refuses it"
            )
            return RetirementDecision(name=entry.name, reason=RETIRED_UNRUNNABLE, detail=why)
        if usage is not None and usage.runs > 0:
            return None
        if window.seconds is None:
            return None
        installed = _parse_time(usage.installed_at if usage is not None else "") or _parse_time(
            entry.artifact.created_at
        )
        if installed is None:
            return None
        age = (now - installed).total_seconds()
        if age <= window.seconds:
            return None
        waited = (
            "waited before its first use"
            if window.basis == "observed"
            else "can have waited before its first use (install to last use before the ledger)"
        )
        return RetirementDecision(
            name=entry.name,
            reason=RETIRED_UNUSED,
            detail=(
                f"never run in {_days(age)} since install; the longest any tool here "
                f"{waited} is {_days(window.seconds)} ({window.tool})"
            ),
        )

    def _keeper_rank(self, entry: ToolEntry, usage: Mapping[str, ToolUsage]) -> tuple[Any, ...]:
        record = usage.get(entry.name)
        return (
            self._runnable(entry),
            bool(record and record.successes),
            (record.last_success_at or record.last_used_at) if record else "",
            record.successes if record else 0,
            entry.artifact.version,
            entry.artifact.created_at,
        )

    def _runnable(self, entry: ToolEntry) -> bool:
        status = self._skills.status(entry.name) if self._skills is not None else None
        return entry.verified and status != "archived"

    def _pinned(self, name: str) -> bool:
        if self._skills is None:
            return False
        metadata = self._skills.lifecycle_metadata(name)
        return bool(metadata and metadata.get("pinned"))

    @staticmethod
    def _evidence(keeper: ToolEntry, usage: Mapping[str, ToolUsage]) -> str:
        record = usage.get(keeper.name)
        if record is None or not record.runs:
            return ""
        last = record.last_success_at or record.last_used_at
        return f" ({record.successes}/{record.runs} successful runs, last {last[:10]})"

    async def _archive_lifecycle(self, name: str) -> None:
        if self._skills is None or self._skills.status(name) in (None, "archived"):
            return
        await self._skills.archive(name)

    # -- grouping -----------------------------------------------------------

    async def _capability_groups(self, entries: Sequence[ToolEntry]) -> tuple[list[set[str]], int]:
        parent = {entry.name: entry.name for entry in entries}

        def find(name: str) -> str:
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        def union(left: str, right: str) -> None:
            parent[find(left)] = find(right)

        by_capability: dict[str, str] = {}
        by_key: dict[str, str] = {}
        by_artifact = {entry.artifact.artifact_id: entry.name for entry in entries}
        for entry in entries:
            if entry.capability:
                union(entry.name, by_capability.setdefault(entry.capability, entry.name))
            key = capability_key(entry.capability or entry.name)
            if key:
                union(entry.name, by_key.setdefault(key, entry.name))
            predecessor = by_artifact.get(entry.artifact.supersedes)
            if predecessor is not None and predecessor != entry.name:
                union(entry.name, predecessor)

        judged = 0
        if self._matcher is not None:
            verdicts = self._load_verdicts()
            descriptors = [entry.descriptor() for entry in entries]
            keys = [item.key for item in descriptors]
            for subject in descriptors:
                others = [item for item in descriptors if item.key != subject.key]
                if all(_pair_key(subject.key, other.key) in verdicts for other in others):
                    continue
                answers = {
                    verdict.candidate_key: verdict
                    for verdict in await self._matcher.same_capability(subject, others)
                }
                judged += len(answers)
                for other in others:
                    verdict = answers.get(other.key)
                    pair = _pair_key(subject.key, other.key)
                    previous = verdicts.get(pair, {})
                    same = bool(verdict and verdict.same) or bool(previous.get("same"))
                    verdicts[pair] = {
                        "same": same,
                        "reason": (verdict.reason if verdict else "below the similarity shortlist"),
                        "similarity": verdict.similarity if verdict else None,
                    }
                # Saved after every judged tool, not once at the end: a first
                # pass is tens of judge calls, and a resident restarted part
                # way (a rollout) would otherwise start the whole pass again,
                # every time, and never finish it.
                self._save_verdicts(verdicts, live=set(keys))
            names = {entry.descriptor().key: entry.name for entry in entries}
            for pair, verdict in verdicts.items():
                left, _, right = pair.partition("|")
                if verdict.get("same") and left in names and right in names:
                    union(names[left], names[right])
            self._save_verdicts(verdicts, live=set(names))

        groups: dict[str, set[str]] = {}
        for entry in entries:
            groups.setdefault(find(entry.name), set()).add(entry.name)
        return [members for members in groups.values() if len(members) > 1], judged

    def _save_verdicts(self, verdicts: Mapping[str, Mapping[str, Any]], *, live: set[str]) -> None:
        """Persist the verdicts between tools that are still installed."""
        if not self._persist:
            return
        _write_json_atomic(
            self._verdicts_path,
            {
                "version": 1,
                "pairs": {
                    pair: dict(value)
                    for pair, value in verdicts.items()
                    if set(pair.split("|")) <= live
                },
            },
        )

    def _load_verdicts(self) -> dict[str, dict[str, Any]]:
        payload = _read_json_object(self._verdicts_path, what="capability verdict cache")
        if payload is None:
            return {}
        pairs = payload.get("pairs")
        return {
            str(key): dict(value)
            for key, value in (pairs.items() if isinstance(pairs, dict) else [])
            if isinstance(value, dict)
        }


def _pair_key(left: str, right: str) -> str:
    return "|".join(sorted((left, right)))


def _days(seconds: float) -> str:
    days = seconds / 86400
    if days >= 1:
        return f"{days:.1f} days"
    return f"{seconds / 3600:.1f} hours"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def learned_tool_health(state_dir: str | Path) -> dict[str, int]:
    """Counts for the resident health snapshot."""
    tools_dir, artifacts_dir = learned_tool_storage(state_dir)
    installed = tool_inventory(tools_dir=tools_dir, artifacts_dir=artifacts_dir)
    usage = LearnedToolUsageLedger(artifacts_dir, persist=False).snapshot()
    retirements = LearnedToolRetirements(artifacts_dir)
    counts = retirements.counts()
    installed_usage = [usage.get(entry.name) for entry in installed]
    snapshot = {
        "learned_tools_installed": len(installed),
        "learned_tools_never_used": sum(1 for item in installed_usage if not item or not item.runs),
        "learned_tools_retired": len(retirements.records()),
        "learned_tool_runs_total": sum(item.runs for item in usage.values()),
        "learned_tool_repairs_total": sum(item.repairs for item in usage.values()),
        "learned_tool_retirements_total": sum(
            value for key, value in counts.items() if key.startswith("retired.")
        ),
        "learned_tool_restores_total": counts.get("restored", 0),
    }
    for reason in RETIREMENT_REASONS:
        snapshot[f"learned_tool_retirements.{reason}"] = counts.get(f"retired.{reason}", 0)
    return snapshot
