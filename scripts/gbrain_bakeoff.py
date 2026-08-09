#!/usr/bin/env python3
"""Score gbrain and Mímir on the same golden set (NIU-1133).

The question this answers is narrow and falsifiable: **does gbrain's retrieval
beat Mímir's on our own corpus?** Both stores are handed the same pages, asked
the same queries, and scored by the same code (``mimir.eval.evaluate_adapter``),
so any metric difference is a retrieval difference.

Synthesis (``--think``) is not a comparison — Mímir has no counterpart. Both
``MimirPort`` implementations return ``answer=""`` and ``src/mimir/`` contains
no model at all, because Mímir's synthesis is precomputed: a Ravn writes the
Compiled Truth zone using the Ravn's own model. ``--think`` exists so the
capability can be looked at before anyone decides whether we want it.

Usage::

    # gbrain vs Mímir-with-embeddings, side by side
    uv run python scripts/gbrain_bakeoff.py \\
        --mcp-url http://127.0.0.1:3141/mcp --token "$GBRAIN_TOKEN" \\
        --baseline \\
        --embedding-model Qwen/Qwen3-Embedding-0.6B \\
        --embedding-base-url https://qwen3-embedding-vllm.valaskjalf.asgard.niuu.world/v1

    # show what gbrain's synthesis produces, on our own vLLM
    uv run python scripts/gbrain_bakeoff.py \\
        --mcp-url http://127.0.0.1:3141/mcp --token "$GBRAIN_TOKEN" \\
        --skip-ingest --think 5 --think-model nvidia:nvidia/nemotron-3-super

Standing gbrain up locally (it is MIT-licensed, so forking is open to us)::

    bun install -g github:garrytan/gbrain     # brew's formula needs current Xcode CLT
    createdb gbrain                           # PGLite cannot serve --http
    gbrain init --url postgresql://... \\
        --embedding-model openai:Qwen/Qwen3-Embedding-0.6B --embedding-dimensions 1024
    gbrain import tests/test_mimir/evals/corpus
    gbrain auth create bakeoff                # prints the bearer token
    gbrain serve --http --port 3141 --bind 127.0.0.1

Environment for the above: ``OPENAI_BASE_URL`` at the embedding vLLM,
``provider_base_urls.nvidia`` (in ``~/.gbrain/config.json``) at the chat vLLM,
and ``GBRAIN_AI_CHAT_TIMEOUT_MS`` raised — the 5-minute default is not enough
for a reasoning model over a whole corpus. Avoid port 3131; a personal gbrain
already listens there.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from mimir.eval import (  # noqa: E402
    EvalReport,
    GoldenQuery,
    QueryEval,
    compare_reports,
    evaluate_adapter,
    load_golden_set,
    mrr,
    normalise_path,
    precision_at_k,
    recall_at_k,
    run_eval,
    validate_golden_paths,
)
from ravn.adapters.mimir.gbrain import GBrainMimirAdapter  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "tests/test_mimir/evals/corpus"
DEFAULT_GOLDEN = REPO_ROOT / "tests/test_mimir/evals/golden.yaml"


async def ingest_corpus(adapter: GBrainMimirAdapter, corpus_dir: Path) -> int:
    """Write every corpus page into the brain, preserving its Mímir path."""
    pages = sorted(corpus_dir.rglob("*.md"))
    if not pages:
        raise SystemExit(f"no markdown pages under {corpus_dir}")
    for page in pages:
        rel = page.relative_to(corpus_dir).as_posix()
        await adapter.upsert_page(rel, page.read_text(encoding="utf-8"))
        print(f"  ingested {rel}", file=sys.stderr)
    return len(pages)


async def eval_synthesis(args: argparse.Namespace, queries: list[GoldenQuery]) -> None:
    """Score gbrain's composed answers against the golden set.

    Mímir has no synthesis to compare against — both ``MimirPort``
    implementations return ``answer=""`` and ``src/mimir/`` holds no model at
    all — so this is not a bake-off. It measures gbrain's ``think`` on its own
    terms, against the one label we already have: which pages *should* answer
    each question.

    What is measured is **citation accuracy**, not prose quality: of the pages
    ``think`` cites, how many are the ones the golden set names. That is
    objective and needs no judge. An answer that reads well while citing the
    wrong pages is the failure mode worth catching, because it is the one a
    reader cannot see.

    Also reported: how often synthesis succeeded at all, and how long it took —
    both decide whether this is usable in a turn, independent of quality.
    """
    adapter = GBrainMimirAdapter(
        mcp_url=args.mcp_url,
        api_token=args.token,
        think_model=args.think_model,
        timeout_seconds=args.timeout_seconds,
    )
    sample = queries[args.think_offset : args.think_offset + args.think]
    scored: list[QueryEval] = []
    failures: list[tuple[str, str]] = []
    latencies: list[float] = []
    try:
        for i, entry in enumerate(sample, 1):
            started = time.monotonic()
            try:
                result = await adapter.query(entry.query)
            except Exception as exc:  # synthesis refused, timed out, or errored
                failures.append((entry.query, str(exc)[:140]))
                print(f"[{i}/{len(sample)}] FAILED  {entry.query}", file=sys.stderr)
                continue
            elapsed = time.monotonic() - started
            latencies.append(elapsed)
            cited = [normalise_path(p.meta.path) for p in result.sources]
            expected = [normalise_path(p) for p in entry.expected]
            scored.append(
                QueryEval(
                    query=entry.query,
                    category=entry.category,
                    expected=entry.expected,
                    returned=cited,
                    precision=precision_at_k(cited, expected),
                    recall=recall_at_k(cited, expected),
                    mrr=mrr(cited, expected),
                )
            )
            print(f"[{i}/{len(sample)}] {elapsed:>5.0f}s  {entry.query}", file=sys.stderr)
            print(f"\n[{entry.category}] {entry.query}   ({elapsed:.0f}s)")
            print(f"  expected: {', '.join(entry.expected)}")
            print(f"  cited:    {', '.join(cited) or '(none)'}")
            print(f"  {result.answer.strip()[:700]}")
            if args.think_jsonl:
                # Appended per question: a 62-question run is ~2h and cannot
                # complete inside one command window, so partial results have
                # to survive the run being cut short.
                with open(args.think_jsonl, "a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "query": entry.query,
                                "category": entry.category,
                                "expected": entry.expected,
                                "cited": cited,
                                "precision": scored[-1].precision,
                                "recall": scored[-1].recall,
                                "mrr": scored[-1].mrr,
                                "seconds": round(elapsed, 1),
                                "answer": result.answer.strip(),
                            }
                        )
                        + "\n"
                    )
    finally:
        await adapter.close()

    print("\n=== think: citation accuracy against the golden set ===")
    report = EvalReport(
        generated_at="", corpus=str(args.corpus), embedding_model=args.think_model, queries=scored
    )
    print(report.format_text())
    attempted = len(sample)
    print(f"\n  synthesis succeeded : {len(scored)}/{attempted}")
    if latencies:
        latencies.sort()
        median = latencies[len(latencies) // 2]
        print(f"  latency median/max  : {median:.0f}s / {latencies[-1]:.0f}s")
    for query, why in failures:
        print(f"  FAILED  {query}: {why}")


async def score_gbrain(
    args: argparse.Namespace,
    queries: list[GoldenQuery],
) -> EvalReport:
    adapter = GBrainMimirAdapter(
        mcp_url=args.mcp_url,
        api_token=args.token,
        ingest_url=args.ingest_url,
        search_limit=args.search_limit,
        think_model=args.think_model,
        query_expansion=not args.no_expand,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        if not args.skip_ingest:
            count = await ingest_corpus(adapter, args.corpus)
            print(f"ingested {count} pages into gbrain", file=sys.stderr)
        return await evaluate_adapter(
            adapter,
            queries,
            corpus=str(args.corpus),
            embedding_model="gbrain (built-in)",
        )
    finally:
        await adapter.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", required=True, help="gbrain MCP endpoint, e.g. .../mcp")
    parser.add_argument("--token", required=True, help="bearer from `gbrain auth create`")
    parser.add_argument("--ingest-url", default=None, help="optional gbrain /ingest endpoint")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--search-limit", type=int, default=10)
    parser.add_argument(
        "--no-expand",
        action="store_true",
        help="disable gbrain's LLM query expansion (needs a working chat model)",
    )
    parser.add_argument(
        "--think-model",
        default="",
        help="<provider>:<model> for synthesis, e.g. nvidia:nvidia/nemotron-3-super. "
        "Required for --think: gbrain's `think` ignores the brain's chat_model.",
    )
    parser.add_argument(
        "--think",
        type=int,
        default=0,
        help="score gbrain's synthesis on N golden questions",
    )
    parser.add_argument(
        "--think-offset",
        type=int,
        default=0,
        help="skip the first N questions — lets a long run proceed in slices",
    )
    parser.add_argument(
        "--think-jsonl",
        default="",
        help="append each scored answer here, so a cut-short run keeps its results",
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="the brain already holds the corpus; only run the queries",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="also score Mímir on the same golden set and diff the two",
    )
    parser.add_argument("--embedding-model", default=None, help="baseline embedding model")
    parser.add_argument("--embedding-base-url", default="", help="baseline embedding endpoint")
    parser.add_argument("--embedding-api-key", default="")
    parser.add_argument("--out", type=Path, default=None, help="write the gbrain report as JSON")
    args = parser.parse_args()

    queries = load_golden_set(args.golden)
    missing = validate_golden_paths(queries, args.corpus)
    if missing:
        raise SystemExit(f"golden set references {len(missing)} missing pages: {missing}")

    if args.think:
        if not args.think_model:
            raise SystemExit("--think needs --think-model; gbrain's think ignores chat_model")
        await eval_synthesis(args, queries)

    gbrain_report = await score_gbrain(args, queries)
    print("\n=== gbrain ===")
    print(gbrain_report.format_text())

    if args.out is not None:
        args.out.write_text(json.dumps(gbrain_report.to_dict(), indent=2), encoding="utf-8")

    if not args.baseline:
        return 0

    mimir_report = await run_eval(
        args.corpus,
        args.golden,
        embedding_model=args.embedding_model,
        embedding_base_url=args.embedding_base_url,
        embedding_api_key=args.embedding_api_key,
    )
    print("\n=== Mímir ===")
    print(mimir_report.format_text())
    print("\n=== gbrain vs Mímir (positive = gbrain ahead) ===")
    print(compare_reports(gbrain_report, mimir_report).format_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
