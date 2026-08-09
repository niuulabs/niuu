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
    compare_reports,
    evaluate_adapter,
    load_golden_set,
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


async def show_synthesis(args: argparse.Namespace, queries: list[GoldenQuery]) -> None:
    """Print gbrain's composed answers for a sample of the golden questions.

    Mímir has no counterpart to score this against — both ``MimirPort``
    implementations return ``answer=""``, and ``src/mimir/`` holds no model at
    all. So this is not a comparison; it is the only way to see what the
    capability actually produces before deciding whether we want it.
    """
    adapter = GBrainMimirAdapter(
        mcp_url=args.mcp_url,
        api_token=args.token,
        think_model=args.think_model,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        for entry in queries[: args.think]:
            started = time.monotonic()
            result = await adapter.query(entry.query)
            elapsed = time.monotonic() - started
            print(f"\n[{entry.category}] {entry.query}   ({elapsed:.0f}s)")
            print(f"  expected: {', '.join(entry.expected)}")
            print(f"  cited:    {', '.join(p.meta.path for p in result.sources) or '(none)'}")
            print(f"  {result.answer.strip()}")
    finally:
        await adapter.close()


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
        help="show gbrain's composed answers for the first N golden questions",
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
        await show_synthesis(args, queries)

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
