"""Mímir doctor — one-command health check with safe auto-remediation.

Runs a fixed battery of checks (D01–D08) against a Mímir knowledge root and
returns a scored :class:`DoctorReport`.  ``run_fixes()`` applies the *safe*
remediation subset only: lint auto-fix (L05/L11/L12) and a full search-index
rebuild.  Destructive operations (deleting pages or raw sources) are never
performed.

Usage::

    from mimir.adapters.markdown import MarkdownMimirAdapter
    from mimir.doctor import run_doctor, run_fixes

    adapter = MarkdownMimirAdapter(root="~/.ravn/mimir")
    report = await run_doctor(adapter, Path("~/.ravn/mimir").expanduser())
    if report.exit_code != 0:
        await run_fixes(adapter)
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from mimir.registry import MimirRegistryStore
from niuu.domain.mimir import MimirLintReport
from niuu.ports.mimir import MimirPort

logger = logging.getLogger(__name__)

# Status ordering and rendering.
_STATUS_RANK: dict[str, int] = {"pass": 0, "warn": 1, "fail": 2}
_STATUS_GLYPH: dict[str, str] = {"pass": "✓", "warn": "!", "fail": "✗"}

# Short timeout for registry /health probes — doctor must stay snappy.
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 3.0

# Query used for the smoke-search check.  Deliberately a stopword: the
# built-in keyword search drops it (empty result, no exception) and FTS
# treats it as a plain literal — either way the call must not raise.
_SMOKE_SEARCH_QUERY = "the"

_FIX_HINT = "run `python -m mimir doctor --fix`"


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass
class DoctorCheck:
    """Outcome of a single doctor check."""

    id: str
    title: str
    status: str  # "pass" | "warn" | "fail"
    detail: str = ""
    remediation: str = ""
    fixable: bool = False


@dataclass
class DoctorReport:
    """Aggregate result of a doctor run."""

    checks: list[DoctorCheck] = field(default_factory=list)

    @property
    def worst_status(self) -> str:
        if not self.checks:
            return "pass"
        return max((check.status for check in self.checks), key=lambda s: _STATUS_RANK[s])

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check.status == "pass")

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def score(self) -> str:
        return f"{self.passed}/{self.total}"

    @property
    def exit_code(self) -> int:
        """0 when all checks pass, 1 on warnings, 2 on failures."""
        return _STATUS_RANK[self.worst_status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.worst_status,
            "score": self.score,
            "passed": self.passed,
            "total": self.total,
            "exit_code": self.exit_code,
            "checks": [asdict(check) for check in self.checks],
        }

    def format_text(self) -> str:
        lines = [f"Mímir doctor — {self.score} checks passed ({self.worst_status})", ""]
        for check in self.checks:
            glyph = _STATUS_GLYPH[check.status]
            suffix = " [fixable]" if check.fixable and check.status != "pass" else ""
            lines.append(f"  {glyph} {check.id} {check.title:<20} {check.detail}{suffix}")
            if check.remediation and check.status != "pass":
                lines.append(f"        → {check.remediation}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Doctor entry points
# ---------------------------------------------------------------------------


async def run_doctor(
    adapter: MimirPort,
    root: Path,
    registry_store: MimirRegistryStore | None = None,
    search_db: Path | None = None,
    *,
    embedding_model: str | None = None,
    health_timeout_seconds: float = _DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> DoctorReport:
    """Run all doctor checks (D01–D08) against a Mímir knowledge root."""
    root = root.expanduser()
    resolved_search_db = search_db if search_db is not None else root / "search.db"

    lint_report, lint_error = await _safe_lint(adapter)
    page_count = await _safe_page_count(adapter)

    checks = [
        _check_wiki_root(root),
        _check_index_sync(lint_report, lint_error),
        _check_search_db(resolved_search_db, page_count),
        _check_embedding_stack(embedding_model),
        await _check_registry_mounts(registry_store, health_timeout_seconds),
        _check_lint_summary(lint_report, lint_error),
        _check_orphaned_raw_sources(root),
        await _check_smoke_search(adapter),
    ]
    return DoctorReport(checks=checks)


async def run_fixes(adapter: MimirPort) -> list[str]:
    """Apply the SAFE remediation subset and return a description of each fix.

    Safe fixes are: lint auto-fix (L05 broken wikilinks, L11 stale index,
    L12 missing frontmatter type) and a full search-index rebuild.  Nothing
    is ever deleted.
    """
    applied: list[str] = []

    pre_report = await adapter.lint()
    fixable = [issue for issue in pre_report.issues if issue.auto_fixable]
    if fixable:
        await adapter.lint(fix=True)
        fixed_ids = ", ".join(sorted({issue.id for issue in fixable}))
        applied.append(f"lint auto-fix applied to {len(fixable)} issue(s) ({fixed_ids})")

    rebuild = getattr(adapter, "rebuild_search_index", None)
    if rebuild is not None:
        indexed = await rebuild()
        if indexed:
            applied.append(f"search index rebuilt ({indexed} pages)")

    return applied


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_wiki_root(root: Path) -> DoctorCheck:
    """D01 — wiki root exists, is writable, and MIMIR.md is present."""
    check_id, title = "D01", "wiki root"
    if not root.is_dir():
        return DoctorCheck(
            id=check_id,
            title=title,
            status="fail",
            detail=f"root directory does not exist: {root}",
            remediation="create the root or point --path at an existing Mímir store",
        )

    if not os.access(root, os.W_OK):
        return DoctorCheck(
            id=check_id,
            title=title,
            status="fail",
            detail=f"root directory is not writable: {root}",
            remediation="fix filesystem permissions on the Mímir root",
        )

    if not (root / "MIMIR.md").is_file():
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail="MIMIR.md schema file is missing",
            remediation="any adapter write re-seeds MIMIR.md, or restore it from git",
        )

    return DoctorCheck(
        id=check_id,
        title=title,
        status="pass",
        detail="root exists, is writable, MIMIR.md present",
    )


def _check_index_sync(report: MimirLintReport | None, lint_error: str) -> DoctorCheck:
    """D02 — index.md is in sync with the wiki page set (lint L11)."""
    check_id, title = "D02", "index sync"
    if report is None:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="fail",
            detail=f"lint did not run: {lint_error}",
            remediation="fix the underlying lint error, then re-run doctor",
        )

    l11 = [issue for issue in report.issues if issue.id == "L11"]
    if l11:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail=l11[0].message,
            remediation=_FIX_HINT,
            fixable=True,
        )

    return DoctorCheck(
        id=check_id,
        title=title,
        status="pass",
        detail="index.md is in sync with the wiki page set",
    )


def _check_search_db(search_db: Path, page_count: int) -> DoctorCheck:
    """D03 — search.db exists and indexed chunks are consistent with the wiki."""
    check_id, title = "D03", "search index"
    if not search_db.is_file():
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail=f"search database not found: {search_db}",
            remediation=f"{_FIX_HINT} to build the search index",
            fixable=True,
        )

    chunk_count = _count_indexed_chunks(search_db)
    if chunk_count is None:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail="search database exists but the search_index table is unreadable",
            remediation=f"{_FIX_HINT} to rebuild the search index",
            fixable=True,
        )

    if page_count > 0 and chunk_count == 0:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail=f"wiki has {page_count} page(s) but 0 chunks are indexed",
            remediation=f"{_FIX_HINT} to rebuild the search index",
            fixable=True,
        )

    return DoctorCheck(
        id=check_id,
        title=title,
        status="pass",
        detail=f"{chunk_count} chunk(s) indexed for {page_count} wiki page(s)",
    )


def _check_embedding_stack(embedding_model: str | None) -> DoctorCheck:
    """D04 — embedding dependencies importable when an embedding model is set."""
    check_id, title = "D04", "embedding stack"
    st_available = _module_available("sentence_transformers")
    vec_available = _module_available("sqlite_vec")

    if embedding_model is None:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="pass",
            detail="FTS-only mode (no embedding model configured)",
        )

    problems: list[str] = []
    if not st_available:
        problems.append("sentence-transformers is not importable (falling back to FTS-only)")
    if not vec_available:
        problems.append("sqlite-vec is not importable (optional)")

    if problems:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail="; ".join(problems),
            remediation="pip install sentence-transformers sqlite-vec",
        )

    return DoctorCheck(
        id=check_id,
        title=title,
        status="pass",
        detail=f"embedding stack ready for model '{embedding_model}'",
    )


async def _check_registry_mounts(
    registry_store: MimirRegistryStore | None,
    health_timeout_seconds: float,
) -> DoctorCheck:
    """D05 — every enabled registry mount is reachable (remote) or present (local)."""
    check_id, title = "D05", "registry mounts"
    entries = registry_store.list_entries() if registry_store is not None else []
    enabled = [entry for entry in entries if entry.enabled]
    if not enabled:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="pass",
            detail="no enabled registry mounts to probe",
        )

    problems: list[str] = []
    for entry in enabled:
        if entry.kind == "local":
            if entry.path and not Path(entry.path).expanduser().exists():
                problems.append(f"local mount '{entry.name}': path missing ({entry.path})")
            continue
        if not entry.url:
            problems.append(f"remote mount '{entry.name}': no URL configured")
            continue
        error = await _probe_remote_health(entry.url, health_timeout_seconds)
        if error:
            problems.append(f"remote mount '{entry.name}': {error}")

    if problems:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail="; ".join(problems),
            remediation="check mount URLs/paths in .mimir-registry.json or disable dead mounts",
        )

    return DoctorCheck(
        id=check_id,
        title=title,
        status="pass",
        detail=f"{len(enabled)} enabled mount(s) healthy",
    )


def _check_lint_summary(report: MimirLintReport | None, lint_error: str) -> DoctorCheck:
    """D06 — lint severity summary: fail on errors, warn on warnings."""
    check_id, title = "D06", "lint summary"
    if report is None:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="fail",
            detail=f"lint did not run: {lint_error}",
            remediation="fix the underlying lint error, then re-run doctor",
        )

    summary = report.summary
    fixable = any(issue.auto_fixable for issue in report.issues)
    detail = (
        f"errors={summary['error']} warnings={summary['warning']} "
        f"info={summary['info']} ({report.pages_checked} pages checked)"
    )
    if summary["error"] > 0:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="fail",
            detail=detail,
            remediation="resolve error-severity lint issues (see GET /mimir/lint)",
            fixable=fixable,
        )

    if summary["warning"] > 0:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail=detail,
            remediation=f"{_FIX_HINT} for auto-fixable issues" if fixable else "review lint report",
            fixable=fixable,
        )

    return DoctorCheck(id=check_id, title=title, status="pass", detail=detail)


def _check_orphaned_raw_sources(root: Path) -> DoctorCheck:
    """D07 — raw/*.json sources whose source_id no wiki page references."""
    check_id, title = "D07", "raw sources"
    raw_dir = root / "raw"
    raw_files = sorted(raw_dir.glob("*.json")) if raw_dir.is_dir() else []
    if not raw_files:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="pass",
            detail="no raw sources stored",
        )

    wiki_text = _all_wiki_text(root / "wiki")
    orphans: list[str] = []
    for raw_file in raw_files:
        source_id = _read_source_id(raw_file)
        if source_id and source_id not in wiki_text:
            orphans.append(source_id)

    if orphans:
        return DoctorCheck(
            id=check_id,
            title=title,
            status="warn",
            detail=(
                f"{len(orphans)} of {len(raw_files)} raw source(s) referenced by no "
                f"wiki page: {', '.join(orphans[:5])}"
            ),
            remediation="synthesise the unprocessed sources into wiki pages",
        )

    return DoctorCheck(
        id=check_id,
        title=title,
        status="pass",
        detail=f"all {len(raw_files)} raw source(s) referenced by wiki pages",
    )


async def _check_smoke_search(adapter: MimirPort) -> DoctorCheck:
    """D08 — search executes without raising."""
    check_id, title = "D08", "smoke search"
    try:
        results = await adapter.search(_SMOKE_SEARCH_QUERY)
    except Exception as exc:  # noqa: BLE001 — any search failure is the finding
        return DoctorCheck(
            id=check_id,
            title=title,
            status="fail",
            detail=f"search raised {type(exc).__name__}: {exc}",
            remediation=f"{_FIX_HINT} to rebuild the search index",
            fixable=True,
        )

    return DoctorCheck(
        id=check_id,
        title=title,
        status="pass",
        detail=f"search executed without error ({len(results)} result(s))",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_available(module_name: str) -> bool:
    """Return True when *module_name* can be imported."""
    return importlib.util.find_spec(module_name) is not None


async def _safe_lint(adapter: MimirPort) -> tuple[MimirLintReport | None, str]:
    """Run lint, returning (report, "") or (None, error_message)."""
    try:
        return await adapter.lint(), ""
    except Exception as exc:  # noqa: BLE001 — surfaced as a failed check
        logger.warning("mimir doctor: lint pass failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


async def _safe_page_count(adapter: MimirPort) -> int:
    """Return the wiki page count, or 0 when listing fails."""
    try:
        return len(await adapter.list_pages())
    except Exception as exc:  # noqa: BLE001 — count feeds a warn-level check only
        logger.warning("mimir doctor: list_pages failed: %s", exc)
        return 0


def _count_indexed_chunks(search_db: Path) -> int | None:
    """Count search_index rows via a read-only connection; None on error."""
    try:
        conn = sqlite3.connect(f"file:{search_db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()
        return int(row[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


async def _probe_remote_health(url: str, timeout_seconds: float) -> str:
    """GET <url>/health; return "" when healthy, or an error description."""
    health_url = f"{url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(health_url)
    except httpx.HTTPError as exc:
        return f"unreachable ({type(exc).__name__})"
    if response.status_code != httpx.codes.OK:
        return f"unhealthy (HTTP {response.status_code})"
    return ""


def _all_wiki_text(wiki_dir: Path) -> str:
    """Concatenate all wiki page content (used for source_id reference lookup).

    Skips ``index.md`` and ``log.md`` — the activity log records every ingest's
    source_id, which would mask genuinely orphaned sources.
    """
    if not wiki_dir.is_dir():
        return ""
    parts: list[str] = []
    for md_path in wiki_dir.rglob("*.md"):
        if md_path.name in {"index.md", "log.md"}:
            continue
        try:
            parts.append(md_path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(parts)


def _read_source_id(raw_file: Path) -> str:
    """Return the source_id stored in a raw/*.json file, or "" when unreadable."""
    try:
        data = json.loads(raw_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    source_id = data.get("source_id", "") if isinstance(data, dict) else ""
    return str(source_id)
