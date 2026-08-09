"""Tests for the Mímir retrieval eval harness (NIU-1055).

Covers:
- Metric math: precision@5, recall@10, MRR
- Golden-set loading and validation (malformed files, missing corpus paths)
- run_eval end-to-end against the committed fixture corpus
- compare_reports flags an artificially-broken ranking
- Query capture: JSONL writing, default-on, router integration
- Replay: ranking drift detection against a capture file
- CLI: --json/--out/--against/--fail-on-regression and replay
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mimir.config import MimirServiceConfig
from mimir.eval import (
    CapturedQuery,
    EvalReport,
    GoldenQuery,
    GoldenSetError,
    QueryEval,
    append_capture,
    capture_file_for,
    compare_reports,
    evaluate_adapter,
    load_capture,
    load_golden_set,
    mrr,
    normalise_path,
    precision_at_k,
    recall_at_k,
    replay_capture,
    run_eval,
    validate_golden_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "tests" / "test_mimir" / "evals" / "corpus"
GOLDEN_PATH = REPO_ROOT / "tests" / "test_mimir" / "evals" / "golden.yaml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """---
type: topic
confidence: high
---

# {title}

## Compiled Truth

### Key Facts
- {body}

## Timeline

- 2026-01-01: Page created. [Source: test, fixture, 2026-01-01]
"""


def _write_page(corpus: Path, rel: str, title: str, body: str) -> None:
    page = corpus / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(PAGE_TEMPLATE.format(title=title, body=body), encoding="utf-8")


def _small_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """A two-page corpus and matching golden set for fast CLI tests."""
    corpus = tmp_path / "corpus"
    _write_page(corpus, "technical/widgets.md", "Widget assembly", "Widgets are assembled daily.")
    _write_page(corpus, "technical/gadgets.md", "Gadget repair", "Gadgets are repaired weekly.")
    golden = tmp_path / "golden.yaml"
    golden.write_text(
        yaml.safe_dump(
            {
                "queries": [
                    {
                        "query": "widget assembly",
                        "category": "keyword",
                        "expected": ["technical/widgets.md"],
                    },
                    {
                        "query": "gadget repair",
                        "category": "keyword",
                        "expected": ["technical/gadgets.md"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return corpus, golden


def _query_eval(query: str, precision: float, recall: float = 1.0, rr: float = 1.0) -> QueryEval:
    return QueryEval(
        query=query,
        category="keyword",
        expected=["a.md"],
        returned=["a.md"],
        precision=precision,
        recall=recall,
        mrr=rr,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_precision_at_k_full_hit() -> None:
    assert precision_at_k(["a", "b"], ["a", "b"], k=5) == 1.0


def test_precision_at_k_normalises_by_expected_count() -> None:
    # One expected page found in the top 5 → perfect precision, not 1/5.
    assert precision_at_k(["a", "x", "y"], ["a"], k=5) == 1.0


def test_precision_at_k_partial() -> None:
    assert precision_at_k(["a", "x"], ["a", "b"], k=5) == 0.5


def test_precision_at_k_empty_expected() -> None:
    assert precision_at_k(["a"], [], k=5) == 0.0


def test_recall_at_k_counts_only_top_k() -> None:
    returned = [f"r{i}" for i in range(10)] + ["a"]
    assert recall_at_k(returned, ["a"], k=10) == 0.0
    assert recall_at_k(["a", *returned], ["a"], k=10) == 1.0


def test_mrr_rank_positions() -> None:
    assert mrr(["a"], ["a"]) == 1.0
    assert mrr(["x", "a"], ["a"]) == 0.5
    assert mrr(["x", "y"], ["a"]) == 0.0


# ---------------------------------------------------------------------------
# Golden set loading
# ---------------------------------------------------------------------------


def test_load_golden_set_parses_committed_file() -> None:
    queries = load_golden_set(GOLDEN_PATH)
    assert len(queries) >= 50
    categories = {entry.category for entry in queries}
    assert {"keyword", "semantic", "relational", "temporal"} <= categories


def test_committed_golden_paths_all_exist() -> None:
    queries = load_golden_set(GOLDEN_PATH)
    assert validate_golden_paths(queries, CORPUS_DIR) == []


def test_load_golden_set_missing_file(tmp_path: Path) -> None:
    with pytest.raises(GoldenSetError, match="not found"):
        load_golden_set(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    "entry",
    [
        {"category": "keyword", "expected": ["a.md"]},  # missing query
        {"query": "q", "expected": ["a.md"]},  # missing category
        {"query": "q", "category": "keyword"},  # missing expected
        {"query": "q", "category": "keyword", "expected": []},  # empty expected
    ],
)
def test_load_golden_set_rejects_malformed_entries(tmp_path: Path, entry: dict) -> None:
    path = tmp_path / "golden.yaml"
    path.write_text(yaml.safe_dump({"queries": [entry]}), encoding="utf-8")
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_load_golden_set_rejects_duplicates(tmp_path: Path) -> None:
    entry = {"query": "q", "category": "keyword", "expected": ["a.md"]}
    path = tmp_path / "golden.yaml"
    path.write_text(yaml.safe_dump({"queries": [entry, dict(entry)]}), encoding="utf-8")
    with pytest.raises(GoldenSetError, match="Duplicate"):
        load_golden_set(path)


def test_validate_golden_paths_reports_missing(tmp_path: Path) -> None:
    queries = [GoldenQuery(query="q", expected=["missing/page.md"], category="keyword")]
    assert validate_golden_paths(queries, tmp_path) == ["missing/page.md"]


# ---------------------------------------------------------------------------
# run_eval
# ---------------------------------------------------------------------------


async def test_run_eval_on_committed_corpus() -> None:
    report = await run_eval(CORPUS_DIR, GOLDEN_PATH)

    assert len(report.queries) >= 50
    assert report.embedding_model is None
    # FTS-only baseline: exact-vocabulary queries must retrieve well.
    assert report.by_category["keyword"]["precision_at_5"] > 0.8
    # Sanity: metrics are bounded.
    for metrics in (report.overall, *report.by_category.values()):
        for value in metrics.values():
            assert 0.0 <= value <= 1.0


async def test_run_eval_rejects_golden_referencing_missing_pages(tmp_path: Path) -> None:
    corpus, golden = _small_corpus(tmp_path)
    golden.write_text(
        yaml.safe_dump(
            {
                "queries": [
                    {"query": "q", "category": "keyword", "expected": ["not/there.md"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GoldenSetError, match="missing from corpus"):
        await run_eval(corpus, golden)


async def test_run_eval_report_round_trips_via_json(tmp_path: Path) -> None:
    corpus, golden = _small_corpus(tmp_path)
    report = await run_eval(corpus, golden)
    restored = EvalReport.from_dict(json.loads(json.dumps(report.to_dict())))
    assert restored.overall == report.overall
    assert [q.query for q in restored.queries] == [q.query for q in report.queries]


# ---------------------------------------------------------------------------
# evaluate_adapter — the bake-off seam (NIU-1133)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("technical/auth-oidc.md", "technical/auth-oidc"),
        ("wiki/technical/auth-oidc.md", "technical/auth-oidc"),
        ("/technical/Auth-OIDC", "technical/auth-oidc"),
        ("technical/auth-oidc", "technical/auth-oidc"),
    ],
)
def test_normalise_path_collapses_store_specific_addressing(raw: str, expected: str) -> None:
    assert normalise_path(raw) == expected


class _StubStore:
    """Minimal MimirPort-shaped search surface, addressing pages by slug."""

    def __init__(self, results: dict[str, list[str]]) -> None:
        self._results = results

    async def search(self, query: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(meta=SimpleNamespace(path=path))
            for path in self._results.get(query, [])
        ]


async def test_evaluate_adapter_scores_a_store_that_returns_slugs() -> None:
    """A store addressing pages without ``.md`` must not be penalised for it.

    This is what makes the gbrain bake-off meaningful: gbrain returns
    extensionless slugs, so without normalisation it would score 0.0 on every
    query for a reason that has nothing to do with retrieval quality.
    """
    queries = [
        GoldenQuery(query="oidc", expected=["technical/auth-oidc.md"], category="keyword"),
    ]
    report = await evaluate_adapter(
        _StubStore({"oidc": ["technical/auth-oidc", "projects/other"]}),
        queries,
        corpus="stub",
        embedding_model="stub-model",
    )

    assert report.overall["precision_at_5"] == 1.0
    assert report.overall["mrr"] == 1.0
    assert report.embedding_model == "stub-model"
    assert report.corpus == "stub"


async def test_evaluate_adapter_reports_a_miss_as_zero() -> None:
    queries = [
        GoldenQuery(query="oidc", expected=["technical/auth-oidc.md"], category="keyword"),
    ]
    report = await evaluate_adapter(
        _StubStore({"oidc": ["projects/unrelated"]}),
        queries,
        corpus="stub",
    )

    assert report.overall["precision_at_5"] == 0.0
    assert report.overall["recall_at_10"] == 0.0
    assert report.queries[0].expected == ["technical/auth-oidc.md"]


# ---------------------------------------------------------------------------
# compare_reports
# ---------------------------------------------------------------------------


def test_compare_flags_broken_ranking() -> None:
    baseline = EvalReport(
        generated_at="t0",
        corpus="c",
        embedding_model=None,
        queries=[_query_eval("q1", 1.0), _query_eval("q2", 1.0)],
    )
    # Artificially break the ranking: every query loses its hits.
    broken = EvalReport(
        generated_at="t1",
        corpus="c",
        embedding_model=None,
        queries=[
            _query_eval("q1", 0.0, recall=0.0, rr=0.0),
            _query_eval("q2", 0.0, recall=0.0, rr=0.0),
        ],
    )
    comparison = compare_reports(broken, baseline)
    assert comparison.has_regression()
    assert {entry.query for entry in comparison.regressions} == {"q1", "q2"}
    assert comparison.overall_delta["precision_at_5"] == -1.0
    assert "regressed queries (2)" in comparison.format_text()


def test_compare_ignores_unmatched_queries_and_finds_improvements() -> None:
    baseline = EvalReport(
        generated_at="t0",
        corpus="c",
        embedding_model=None,
        queries=[_query_eval("shared", 0.5, rr=0.5), _query_eval("baseline-only", 1.0)],
    )
    current = EvalReport(
        generated_at="t1",
        corpus="c",
        embedding_model=None,
        queries=[_query_eval("shared", 1.0), _query_eval("current-only", 0.0)],
    )
    comparison = compare_reports(current, baseline)
    assert not comparison.has_regression()
    assert [entry.query for entry in comparison.improvements] == ["shared"]
    assert comparison.regressions == []


def test_compare_no_changes_formats_cleanly() -> None:
    report = EvalReport(
        generated_at="t",
        corpus="c",
        embedding_model=None,
        queries=[_query_eval("q", 1.0)],
    )
    comparison = compare_reports(report, report)
    assert "no per-query changes" in comparison.format_text()


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def test_eval_capture_defaults_on() -> None:
    # On by default so Analytics gets query traffic out of the box.
    assert MimirServiceConfig().eval_capture is True


def test_append_and_load_capture(tmp_path: Path) -> None:
    append_capture(tmp_path, "first query", ["a.md", "b.md"])
    append_capture(tmp_path, "second query", [])

    files = list(tmp_path.glob("queries-*-W*.jsonl"))
    assert len(files) == 1
    captures = load_capture(files[0])
    assert [entry.query for entry in captures] == ["first query", "second query"]
    assert captures[0].result_paths == ["a.md", "b.md"]
    assert captures[0].ts  # ISO timestamp recorded


def test_load_capture_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    good = json.dumps({"ts": "t", "query": "ok", "result_paths": ["a.md"]})
    path.write_text(f"{good}\nnot-json\n{json.dumps({'query': 'no-ts'})}\n", encoding="utf-8")
    captures = load_capture(path)
    assert [entry.query for entry in captures] == ["ok"]


def test_append_capture_survives_unwritable_dir(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory", encoding="utf-8")
    # Must log, not raise — capture can never break a live search.
    append_capture(blocked, "query", [])


def test_capture_file_for_uses_iso_week(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    moment = datetime(2026, 1, 1, tzinfo=UTC)  # ISO week 2026-W01
    assert capture_file_for(tmp_path, moment).name == "queries-2026-W01.jsonl"


def _capture_app(tmp_path: Path, capture_dir: Path | None) -> TestClient:
    from mimir.adapters.markdown import MarkdownMimirAdapter
    from mimir.router import MimirRouter

    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    page = tmp_path / "mimir" / "wiki" / "technical" / "widgets.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Widget assembly\n\nWidgets are assembled daily.\n", encoding="utf-8")

    router = MimirRouter(adapter=adapter, eval_capture_dir=capture_dir)
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return TestClient(app)


def test_router_search_captures_when_enabled(tmp_path: Path) -> None:
    capture_dir = tmp_path / "evals"
    client = _capture_app(tmp_path, capture_dir)

    response = client.get("/mimir/search", params={"q": "widgets"})
    assert response.status_code == 200

    files = list(capture_dir.glob("queries-*.jsonl"))
    assert len(files) == 1
    captures = load_capture(files[0])
    assert captures[0].query == "widgets"
    assert captures[0].result_paths == ["technical/widgets.md"]


def test_router_search_does_not_capture_by_default(tmp_path: Path) -> None:
    client = _capture_app(tmp_path, None)
    response = client.get("/mimir/search", params={"q": "widgets"})
    assert response.status_code == 200
    assert list(tmp_path.rglob("queries-*.jsonl")) == []


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


async def test_replay_detects_ranking_drift(tmp_path: Path) -> None:
    root = tmp_path / "mimir"
    _write_page(root / "wiki", "technical/widgets.md", "Widget assembly", "Widgets daily.")

    captures = [
        # Captured when a now-deleted page ranked first.
        CapturedQuery(ts="t", query="widgets", result_paths=["technical/old-widgets.md"]),
        # Captured results matching current state exactly.
        CapturedQuery(ts="t", query="assembly", result_paths=["technical/widgets.md"]),
    ]
    report = await replay_capture(root, captures)

    drifted = {entry.query: entry for entry in report.entries}
    assert drifted["widgets"].overlap == 0.0
    assert drifted["widgets"].dropped == ["technical/old-widgets.md"]
    assert drifted["widgets"].added == ["technical/widgets.md"]
    assert drifted["widgets"].top_result_changed
    assert drifted["assembly"].overlap == 1.0
    assert not drifted["assembly"].top_result_changed
    assert 0.0 < report.mean_overlap < 1.0
    assert "ranking drift" in report.format_text()
    assert report.to_dict()["query_count"] == 2


async def test_replay_never_touches_live_search_db(tmp_path: Path) -> None:
    root = tmp_path / "mimir"
    _write_page(root / "wiki", "technical/widgets.md", "Widget assembly", "Widgets daily.")
    await replay_capture(root, [CapturedQuery(ts="t", query="widgets", result_paths=[])])
    assert not (root / "search.db").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_eval_json_and_out(tmp_path: Path) -> None:
    from mimir.__main__ import app

    corpus, golden = _small_corpus(tmp_path)
    out = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "--corpus", str(corpus), "--golden", str(golden), "--json", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["query_count"] == 2
    assert json.loads(result.output)["overall"]["precision_at_5"] == 1.0


def test_cli_eval_against_fails_on_regression(tmp_path: Path) -> None:
    from mimir.__main__ import app

    corpus, golden = _small_corpus(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "--corpus", str(corpus), "--golden", str(golden), "--out", str(baseline_path)],
    )
    assert result.exit_code == 0, result.output

    # Artificially break the ranking: rewrite the page so the golden query
    # "widget assembly" no longer matches its title or body at all.
    _write_page(corpus, "technical/widgets.md", "Sprocket making", "Sprockets are made daily.")

    result = runner.invoke(
        app,
        [
            "eval",
            "--corpus",
            str(corpus),
            "--golden",
            str(golden),
            "--against",
            str(baseline_path),
            "--fail-on-regression",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "regressed queries" in result.output


def test_cli_eval_against_passes_without_regression(tmp_path: Path) -> None:
    from mimir.__main__ import app

    corpus, golden = _small_corpus(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner = CliRunner()
    runner.invoke(
        app,
        ["eval", "--corpus", str(corpus), "--golden", str(golden), "--out", str(baseline_path)],
    )
    result = runner.invoke(
        app,
        [
            "eval",
            "--corpus",
            str(corpus),
            "--golden",
            str(golden),
            "--against",
            str(baseline_path),
            "--fail-on-regression",
        ],
    )
    assert result.exit_code == 0, result.output


def test_cli_replay(tmp_path: Path) -> None:
    from mimir.__main__ import app

    root = tmp_path / "mimir"
    _write_page(root / "wiki", "technical/widgets.md", "Widget assembly", "Widgets daily.")
    capture_path = tmp_path / "capture.jsonl"
    capture_path.write_text(
        json.dumps({"ts": "t", "query": "widgets", "result_paths": ["technical/widgets.md"]})
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "replay", "--capture", str(capture_path), "--path", str(root), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mean_overlap"] == 1.0


def test_cli_replay_empty_capture_exits_nonzero(tmp_path: Path) -> None:
    from mimir.__main__ import app

    capture_path = tmp_path / "empty.jsonl"
    capture_path.write_text("", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "replay", "--capture", str(capture_path), "--path", str(tmp_path)],
    )
    assert result.exit_code == 1
