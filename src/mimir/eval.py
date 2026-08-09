"""Retrieval-quality evaluation harness for Mímir (NIU-1055).

Three capabilities, all built on the same metric core:

1. **Golden-set eval** — :func:`run_eval` builds a hermetic Mímir from a
   committed fixture corpus, runs a set of golden queries against it, and
   reports P@5, MRR, and recall@10 overall and per query category.
2. **Comparison** — :func:`compare_reports` diffs two eval reports so a
   retrieval change can prove it did not regress (``mimir eval --against``).
3. **Capture & replay** — :func:`append_capture` records production queries
   as JSONL (gated by ``MimirServiceConfig.eval_capture``);
   :func:`replay_capture` re-runs them against the current code and reports
   how much the rankings shifted.

The eval runner only ever writes inside a temporary directory; replay builds
its search index in a temporary SQLite file and never touches the live
``search.db``.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Metric cutoffs are part of the metric *names* (P@5, recall@10) — reported
# values are meaningless without them, so they are module constants rather
# than tunable config.
PRECISION_K = 5
RECALL_K = 10

# Default P@5 drop (absolute) treated as a regression by ``has_regression``.
DEFAULT_REGRESSION_THRESHOLD = 0.02

_CAPTURE_FILE_TEMPLATE = "queries-{year}-W{week:02d}.jsonl"


class GoldenSetError(Exception):
    """Raised when a golden-set file is malformed or inconsistent."""


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def precision_at_k(returned: list[str], expected: list[str], k: int = PRECISION_K) -> float:
    """Fraction of the top-*k* returned paths that are expected.

    Normalised by ``min(k, len(expected))`` so queries with fewer than *k*
    expected pages can still score 1.0.
    """
    if not expected or k <= 0:
        return 0.0
    top = returned[:k]
    hits = sum(1 for path in top if path in expected)
    return hits / min(k, len(expected))


def recall_at_k(returned: list[str], expected: list[str], k: int = RECALL_K) -> float:
    """Fraction of expected paths found anywhere in the top-*k* results."""
    if not expected or k <= 0:
        return 0.0
    top = set(returned[:k])
    hits = sum(1 for path in expected if path in top)
    return hits / len(expected)


def mrr(returned: list[str], expected: list[str]) -> float:
    """Reciprocal rank of the first expected path in the results (0 if absent)."""
    for rank, path in enumerate(returned, start=1):
        if path in expected:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Golden set
# ---------------------------------------------------------------------------


@dataclass
class GoldenQuery:
    """One golden-set entry: a query and the pages it should retrieve."""

    query: str
    expected: list[str]
    category: str


def load_golden_set(path: Path) -> list[GoldenQuery]:
    """Load and validate a golden-set YAML file.

    Expected shape::

        queries:
          - query: "how do we sign people in"
            category: semantic
            expected:
              - technical/auth-oidc.md

    Raises:
        GoldenSetError: On missing fields, empty queries, or duplicate
            query strings.
    """
    if not path.exists():
        raise GoldenSetError(f"Golden set not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise GoldenSetError(f"Golden set must be a mapping with a 'queries' list: {path}")

    queries: list[GoldenQuery] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["queries"]):
        if not isinstance(raw, dict):
            raise GoldenSetError(f"Golden entry #{i} is not a mapping")
        query = raw.get("query")
        expected = raw.get("expected")
        category = raw.get("category")
        if not query or not isinstance(query, str):
            raise GoldenSetError(f"Golden entry #{i} is missing a 'query' string")
        if not category or not isinstance(category, str):
            raise GoldenSetError(f"Golden entry #{i} ({query!r}) is missing a 'category'")
        if not expected or not isinstance(expected, list):
            raise GoldenSetError(f"Golden entry #{i} ({query!r}) has no 'expected' paths")
        if query in seen:
            raise GoldenSetError(f"Duplicate golden query: {query!r}")
        seen.add(query)
        queries.append(GoldenQuery(query=query, expected=list(expected), category=category))

    if not queries:
        raise GoldenSetError(f"Golden set contains no queries: {path}")
    return queries


def validate_golden_paths(queries: list[GoldenQuery], corpus_dir: Path) -> list[str]:
    """Return expected paths that do not exist in *corpus_dir* (empty = valid)."""
    missing: list[str] = []
    for entry in queries:
        for rel in entry.expected:
            if not (corpus_dir / rel).is_file():
                missing.append(rel)
    return sorted(set(missing))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class QueryEval:
    """Per-query eval outcome."""

    query: str
    category: str
    expected: list[str]
    returned: list[str]
    precision: float
    recall: float
    mrr: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "category": self.category,
            "expected": self.expected,
            "returned": self.returned,
            "precision_at_5": self.precision,
            "recall_at_10": self.recall,
            "mrr": self.mrr,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryEval:
        return cls(
            query=data["query"],
            category=data["category"],
            expected=list(data.get("expected", [])),
            returned=list(data.get("returned", [])),
            precision=float(data["precision_at_5"]),
            recall=float(data["recall_at_10"]),
            mrr=float(data["mrr"]),
        )


@dataclass
class EvalReport:
    """Aggregated golden-set eval results."""

    generated_at: str
    corpus: str
    embedding_model: str | None
    queries: list[QueryEval] = field(default_factory=list)

    @property
    def overall(self) -> dict[str, float]:
        return _mean_metrics(self.queries)

    @property
    def by_category(self) -> dict[str, dict[str, float]]:
        categories: dict[str, list[QueryEval]] = {}
        for entry in self.queries:
            categories.setdefault(entry.category, []).append(entry)
        return {name: _mean_metrics(entries) for name, entries in sorted(categories.items())}

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "corpus": self.corpus,
            "embedding_model": self.embedding_model,
            "query_count": len(self.queries),
            "overall": self.overall,
            "by_category": self.by_category,
            "queries": [entry.to_dict() for entry in self.queries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalReport:
        return cls(
            generated_at=data.get("generated_at", ""),
            corpus=data.get("corpus", ""),
            embedding_model=data.get("embedding_model"),
            queries=[QueryEval.from_dict(raw) for raw in data.get("queries", [])],
        )

    def format_text(self) -> str:
        lines = [
            f"Mímir retrieval eval — {len(self.queries)} queries "
            f"(corpus: {self.corpus}, embeddings: {self.embedding_model or 'fts-only'})",
            "",
            _format_metric_row("overall", self.overall),
        ]
        lines.extend(
            _format_metric_row(category, metrics) for category, metrics in self.by_category.items()
        )
        zero_hit = [q.query for q in self.queries if q.recall == 0.0]
        if zero_hit:
            lines.append("")
            lines.append(f"zero-recall queries ({len(zero_hit)}):")
            lines.extend(f"  - {query}" for query in zero_hit)
        return "\n".join(lines)


def _mean_metrics(entries: list[QueryEval]) -> dict[str, float]:
    if not entries:
        return {"precision_at_5": 0.0, "recall_at_10": 0.0, "mrr": 0.0}
    n = len(entries)
    return {
        "precision_at_5": sum(e.precision for e in entries) / n,
        "recall_at_10": sum(e.recall for e in entries) / n,
        "mrr": sum(e.mrr for e in entries) / n,
    }


def _format_metric_row(label: str, metrics: dict[str, float]) -> str:
    return (
        f"  {label:<12} P@5 {metrics['precision_at_5']:.3f}   "
        f"MRR {metrics['mrr']:.3f}   recall@10 {metrics['recall_at_10']:.3f}"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_eval(
    corpus_dir: Path,
    golden_path: Path,
    *,
    embedding_model: str | None = None,
    embedding_base_url: str = "",
    embedding_api_key: str = "",
) -> EvalReport:
    """Run the golden-set eval against a hermetic Mímir built from *corpus_dir*.

    The corpus is copied into a temporary Mímir root (as its ``wiki/``), a
    fresh search index is built there, and every golden query is executed via
    the same ``MarkdownMimirAdapter.search()`` path production uses.

    Raises:
        GoldenSetError: If the golden set is malformed or references pages
            that do not exist in the corpus.
    """
    queries = load_golden_set(golden_path)
    missing = validate_golden_paths(queries, corpus_dir)
    if missing:
        raise GoldenSetError(
            f"Golden set references {len(missing)} paths missing from corpus "
            f"{corpus_dir}: {', '.join(missing)}"
        )

    with TemporaryDirectory(prefix="mimir-eval-") as tmp:
        root = Path(tmp)
        shutil.copytree(corpus_dir, root / "wiki", dirs_exist_ok=True)
        adapter = _build_adapter(
            root,
            root / "search.db",
            embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key,
        )
        await adapter.rebuild_search_index()

        results: list[QueryEval] = []
        for entry in queries:
            pages = await adapter.search(entry.query)
            returned = [page.meta.path for page in pages]
            results.append(
                QueryEval(
                    query=entry.query,
                    category=entry.category,
                    expected=entry.expected,
                    returned=returned[:RECALL_K],
                    precision=precision_at_k(returned, entry.expected),
                    recall=recall_at_k(returned, entry.expected),
                    mrr=mrr(returned, entry.expected),
                )
            )

    return EvalReport(
        generated_at=datetime.now(UTC).isoformat(),
        corpus=str(corpus_dir),
        embedding_model=embedding_model,
        queries=results,
    )


def _build_adapter(
    root: Path,
    search_db: Path,
    embedding_model: str | None,
    *,
    embedding_base_url: str = "",
    embedding_api_key: str = "",
):
    """Construct a MarkdownMimirAdapter with the same wiring create_app uses."""
    from mimir.adapters.markdown import MarkdownMimirAdapter
    from mimir.app import _build_embed_fn
    from niuu.adapters.search.sqlite import SqliteSearchAdapter

    embed_fn = (
        _build_embed_fn(embedding_model, base_url=embedding_base_url, api_key=embedding_api_key)
        if embedding_model
        else None
    )
    search_port = SqliteSearchAdapter(path=str(search_db), embed_fn=embed_fn)
    return MarkdownMimirAdapter(root=root, search_port=search_port)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass
class QueryDelta:
    """Per-query metric movement between two reports."""

    query: str
    category: str
    precision_delta: float
    recall_delta: float
    mrr_delta: float


@dataclass
class EvalComparison:
    """Diff of a current eval report against a baseline."""

    overall_delta: dict[str, float]
    regressions: list[QueryDelta]
    improvements: list[QueryDelta]

    def has_regression(self, threshold: float = DEFAULT_REGRESSION_THRESHOLD) -> bool:
        """True when overall P@5 dropped by more than *threshold* (absolute)."""
        return self.overall_delta["precision_at_5"] < -threshold

    def format_text(self) -> str:
        delta = self.overall_delta
        lines = [
            "Eval comparison vs baseline:",
            (
                f"  overall      P@5 {delta['precision_at_5']:+.3f}   "
                f"MRR {delta['mrr']:+.3f}   recall@10 {delta['recall_at_10']:+.3f}"
            ),
        ]
        if self.regressions:
            lines.append(f"regressed queries ({len(self.regressions)}):")
            lines.extend(
                f"  - [{entry.category}] {entry.query} "
                f"(P@5 {entry.precision_delta:+.3f}, MRR {entry.mrr_delta:+.3f})"
                for entry in self.regressions
            )
        if self.improvements:
            lines.append(f"improved queries ({len(self.improvements)}):")
            lines.extend(
                f"  - [{entry.category}] {entry.query} "
                f"(P@5 {entry.precision_delta:+.3f}, MRR {entry.mrr_delta:+.3f})"
                for entry in self.improvements
            )
        if not self.regressions and not self.improvements:
            lines.append("  no per-query changes")
        return "\n".join(lines)


def compare_reports(current: EvalReport, baseline: EvalReport) -> EvalComparison:
    """Compare *current* against *baseline*, pairing queries by query string.

    Queries present in only one report are ignored — the comparison is only
    meaningful over the shared set.
    """
    baseline_by_query = {entry.query: entry for entry in baseline.queries}
    regressions: list[QueryDelta] = []
    improvements: list[QueryDelta] = []
    shared_current: list[QueryEval] = []
    shared_baseline: list[QueryEval] = []

    for entry in current.queries:
        base = baseline_by_query.get(entry.query)
        if base is None:
            continue
        shared_current.append(entry)
        shared_baseline.append(base)
        delta = QueryDelta(
            query=entry.query,
            category=entry.category,
            precision_delta=entry.precision - base.precision,
            recall_delta=entry.recall - base.recall,
            mrr_delta=entry.mrr - base.mrr,
        )
        if delta.precision_delta < 0 or delta.mrr_delta < 0:
            regressions.append(delta)
        elif delta.precision_delta > 0 or delta.mrr_delta > 0:
            improvements.append(delta)

    current_mean = _mean_metrics(shared_current)
    baseline_mean = _mean_metrics(shared_baseline)
    overall_delta = {key: current_mean[key] - baseline_mean[key] for key in current_mean}
    return EvalComparison(
        overall_delta=overall_delta,
        regressions=regressions,
        improvements=improvements,
    )


# ---------------------------------------------------------------------------
# Capture & replay
# ---------------------------------------------------------------------------


@dataclass
class CapturedQuery:
    """One production search query recorded by the capture hook."""

    ts: str
    query: str
    result_paths: list[str]


def capture_file_for(evals_dir: Path, now: datetime | None = None) -> Path:
    """Return the ISO-week capture file path for *now* inside *evals_dir*."""
    moment = now or datetime.now(UTC)
    iso = moment.isocalendar()
    return evals_dir / _CAPTURE_FILE_TEMPLATE.format(year=iso.year, week=iso.week)


def append_capture(evals_dir: Path, query: str, result_paths: list[str]) -> None:
    """Append one search to the current ISO-week capture file.

    Capture must never break a live search: failures are logged as errors
    (loudly, per fail-loudly policy) but not raised.
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "query": query,
        "result_paths": result_paths,
    }
    try:
        evals_dir.mkdir(parents=True, exist_ok=True)
        with capture_file_for(evals_dir).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        logger.exception("mimir eval: failed to capture search query %r", query)


def load_capture(path: Path) -> list[CapturedQuery]:
    """Load a capture JSONL file, skipping (and logging) malformed lines."""
    captures: list[CapturedQuery] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            captures.append(
                CapturedQuery(
                    ts=data["ts"],
                    query=data["query"],
                    result_paths=list(data["result_paths"]),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.error("mimir eval: skipping malformed capture line %s:%d", path, lineno)
    return captures


@dataclass
class ReplayEntry:
    """Ranking drift for one replayed query."""

    query: str
    overlap: float
    dropped: list[str]
    added: list[str]
    top_result_changed: bool


@dataclass
class ReplayReport:
    """Aggregated replay results: how much rankings moved vs capture time."""

    mimir_root: str
    entries: list[ReplayEntry] = field(default_factory=list)

    @property
    def mean_overlap(self) -> float:
        if not self.entries:
            return 0.0
        return sum(entry.overlap for entry in self.entries) / len(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mimir_root": self.mimir_root,
            "query_count": len(self.entries),
            "mean_overlap": self.mean_overlap,
            "entries": [
                {
                    "query": entry.query,
                    "overlap": entry.overlap,
                    "dropped": entry.dropped,
                    "added": entry.added,
                    "top_result_changed": entry.top_result_changed,
                }
                for entry in self.entries
            ],
        }

    def format_text(self) -> str:
        lines = [
            f"Replay of {len(self.entries)} captured queries against {self.mimir_root}",
            f"  mean result overlap: {self.mean_overlap:.3f}",
        ]
        shifted = [entry for entry in self.entries if entry.overlap < 1.0]
        if shifted:
            lines.append(f"queries with ranking drift ({len(shifted)}):")
            for entry in shifted:
                lines.append(
                    f"  - {entry.query} (overlap {entry.overlap:.2f}, "
                    f"top changed: {entry.top_result_changed})"
                )
                if entry.dropped:
                    lines.append(f"      dropped: {', '.join(entry.dropped)}")
                if entry.added:
                    lines.append(f"      added:   {', '.join(entry.added)}")
        return "\n".join(lines)


async def replay_capture(
    mimir_root: Path,
    captures: list[CapturedQuery],
    *,
    embedding_model: str | None = None,
) -> ReplayReport:
    """Re-run captured queries against the wiki at *mimir_root*.

    The search index is built in a throwaway temporary database so the live
    ``search.db`` is never touched. Overlap is Jaccard similarity between the
    captured and fresh top-``RECALL_K`` result sets.
    """
    report = ReplayReport(mimir_root=str(mimir_root))
    with TemporaryDirectory(prefix="mimir-replay-") as tmp:
        adapter = _build_adapter(mimir_root, Path(tmp) / "search.db", embedding_model)
        await adapter.rebuild_search_index()

        for captured in captures:
            pages = await adapter.search(captured.query)
            fresh = [page.meta.path for page in pages][:RECALL_K]
            old = captured.result_paths[:RECALL_K]
            old_set, fresh_set = set(old), set(fresh)
            union = old_set | fresh_set
            overlap = len(old_set & fresh_set) / len(union) if union else 1.0
            report.entries.append(
                ReplayEntry(
                    query=captured.query,
                    overlap=overlap,
                    dropped=[path for path in old if path not in fresh_set],
                    added=[path for path in fresh if path not in old_set],
                    top_result_changed=bool(old or fresh) and (old[:1] != fresh[:1]),
                )
            )

    return report
