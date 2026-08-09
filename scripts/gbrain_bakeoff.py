#!/usr/bin/env python3
"""Score gbrain and Mímir on the same golden set (NIU-1133).

The question this answers is narrow and falsifiable: **does gbrain's retrieval
beat Mímir's on our own corpus?** Both stores are handed the same pages, asked
the same queries, and scored by the same code (``mimir.eval.evaluate_adapter``),
so any metric difference is a retrieval difference.

Usage::

    # gbrain only
    uv run python scripts/gbrain_bakeoff.py \\
        --mcp-url http://localhost:3131/mcp --token "$GBRAIN_TOKEN"

    # gbrain vs Mímir-with-embeddings, side by side
    uv run python scripts/gbrain_bakeoff.py \\
        --mcp-url http://localhost:3131/mcp --token "$GBRAIN_TOKEN" \\
        --baseline \\
        --embedding-model Qwen/Qwen3-Embedding-0.6B \\
        --embedding-base-url https://qwen3-embedding-vllm.valaskjalf.asgard.niuu.world/v1

Standing gbrain up locally (it is MIT-licensed, so forking is open to us)::

    brew install oven-sh/bun/bun
    bun install -g github:garrytan/gbrain
    createdb gbrain                      # PGLite cannot serve --http
    export DATABASE_URL=postgres://localhost/gbrain
    gbrain serve --http --port 3131 --bind 0.0.0.0
    gbrain auth create bakeoff           # prints the bearer token

``--bind 0.0.0.0`` matters: gbrain binds loopback by default since v0.34.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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


async def score_gbrain(
    args: argparse.Namespace,
    queries: list[GoldenQuery],
) -> EvalReport:
    adapter = GBrainMimirAdapter(
        mcp_url=args.mcp_url,
        api_token=args.token,
        ingest_url=args.ingest_url,
        search_limit=args.search_limit,
        query_expansion=not args.no_expand,
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
