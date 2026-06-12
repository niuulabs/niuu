"""Per-boost ablation table for the Mímir ranking layer (NIU-1057 evidence).

Runs the golden set against the fixture corpus once per ranking-config
variant and prints overall P@5 / MRR / recall@10 per variant. FTS-only —
no model downloads.

Usage: uv run python scripts/mimir_ranking_ablation.py [--embedding-model NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.config import RankingConfig
from mimir.eval import load_golden_set, mrr, precision_at_k, recall_at_k
from niuu.adapters.search.sqlite import SqliteSearchAdapter

CORPUS = Path("tests/test_mimir/evals/corpus")
GOLDEN = Path("tests/test_mimir/evals/golden.yaml")

_EMBEDDING_MODEL: str | None = None


def neutral() -> RankingConfig:
    """All boosts disabled but the pipeline (over-fetch, dedup) active."""
    return RankingConfig(
        recency_half_life_days=0,
        title_match_boost=1.0,
        confidence_boosts={},
        page_type_weights={},
        backlink_alpha=0.0,
        zone_weights={},
        graph_injection_base=0.0,
        graph_neighbor_boost=1.0,
        graph_entity_boost=1.0,
    )


def variants() -> dict[str, RankingConfig]:
    defaults = RankingConfig()
    out: dict[str, RankingConfig] = {"neutral (no boosts)": neutral()}

    # label -> the default-config fields that variant re-enables on top of neutral.
    single_boost_fields = {
        "recency": ["recency_half_life_days"],
        "title_match": ["title_match_boost"],
        "confidence": ["confidence_boosts"],
        "page_type": ["page_type_weights"],
        "backlinks": ["backlink_alpha"],
        "zone": ["zone_weights"],
        "graph (relational arm)": [
            "graph_injection_base",
            "graph_neighbor_boost",
            "graph_entity_boost",
        ],
    }
    for label, fields in single_boost_fields.items():
        cfg = neutral()
        for name in fields:
            setattr(cfg, name, getattr(defaults, name))
        out[f"+{label}"] = cfg

    out["full (defaults)"] = defaults
    return out


async def run_variant(config: RankingConfig) -> tuple[float, float, float]:
    queries = load_golden_set(GOLDEN)
    with TemporaryDirectory(prefix="mimir-ablation-") as tmp:
        root = Path(tmp)
        shutil.copytree(CORPUS, root / "wiki", dirs_exist_ok=True)
        embed_fn = None
        if _EMBEDDING_MODEL is not None:
            from mimir.app import _build_embed_fn

            embed_fn = _build_embed_fn(_EMBEDDING_MODEL)
        adapter = MarkdownMimirAdapter(
            root=root,
            search_port=SqliteSearchAdapter(path=str(root / "search.db"), embed_fn=embed_fn),
            ranking_config=config,
        )
        await adapter.rebuild_search_index()

        p_sum = m_sum = r_sum = 0.0
        for entry in queries:
            returned = [p.meta.path for p in await adapter.search(entry.query)]
            p_sum += precision_at_k(returned, entry.expected)
            m_sum += mrr(returned, entry.expected)
            r_sum += recall_at_k(returned, entry.expected)
        n = len(queries)
        return p_sum / n, m_sum / n, r_sum / n


async def main() -> None:
    rows: list[tuple[str, float, float, float]] = []
    for name, config in variants().items():
        p5, m, r10 = await run_variant(config)
        rows.append((name, p5, m, r10))
        print(f"  done: {name}", file=sys.stderr)

    base = rows[0]
    print(f"\n{'variant':<28} {'P@5':>7} {'MRR':>7} {'rec@10':>7} {'ΔP@5':>8}")
    for name, p5, m, r10 in rows:
        print(f"{name:<28} {p5:>7.3f} {m:>7.3f} {r10:>7.3f} {p5 - base[1]:>+8.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedding-model", default=None)
    args = parser.parse_args()
    _EMBEDDING_MODEL = args.embedding_model
    asyncio.run(main())
