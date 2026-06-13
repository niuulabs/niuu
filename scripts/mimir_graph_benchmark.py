"""Link-graph build benchmark for the Mímir markdown adapter (NIU-1058 evidence).

Generates N synthetic FORMAT.md-style wiki pages (~20% entities, the rest
topics), each with 2-4 wikilinks to random other pages plus one typed edge
line, then times:

  - ``adapter._build_link_graph()``  — cold build from disk
  - ``adapter._link_graph()``        — cached lookup after priming
  - ``adapter.related_pages(depth=2)`` — a 2-hop traversal

Exit criterion: the cold build for 1000 pages stays sub-second (the graph is
also built lazily and cached, so steady-state cost is the cached lookup).

Usage: uv run python scripts/mimir_graph_benchmark.py [--pages 1000]
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from mimir.adapters.markdown import MarkdownMimirAdapter

ENTITY_RATIO = 0.2
RNG_SEED = 42

ENTITY_TEMPLATE = """---
type: entity
confidence: medium
entity_type: person
---

# {title}

## Compiled Truth

### Key Facts
- Synthetic page linking {wikilinks}.

### Relationships
- [[{typed_target}]] — rel: works_at — x

## Timeline

- 2026-01-01: Created. [Source: bench, fixture, 2026-01-01]
"""

TOPIC_TEMPLATE = """---
type: topic
---

# {title}

## Compiled Truth

### Key Facts
- Synthetic page linking {wikilinks}.

### Relationships
- [[{typed_target}]] — rel: works_at — x

## Timeline

- 2026-01-01: Created. [Source: bench, fixture, 2026-01-01]
"""


def _generate_wiki(root: Path, pages: int, rng: random.Random) -> list[str]:
    """Write *pages* synthetic wiki pages under *root*/wiki; return their paths."""
    slugs = [f"page-{i:04d}" for i in range(pages)]
    wiki = root / "wiki"
    (wiki / "entities").mkdir(parents=True, exist_ok=True)
    (wiki / "topics").mkdir(parents=True, exist_ok=True)

    rel_paths: list[str] = []
    for i, slug in enumerate(slugs):
        is_entity = rng.random() < ENTITY_RATIO
        others = [s for s in rng.sample(slugs, k=6) if s != slug]
        links = others[: rng.randint(2, 4)]
        typed_target = others[-1]
        template = ENTITY_TEMPLATE if is_entity else TOPIC_TEMPLATE
        content = template.format(
            title=f"Page {i:04d}",
            wikilinks=" and ".join(f"[[{s}]]" for s in links),
            typed_target=typed_target,
        )
        category = "entities" if is_entity else "topics"
        (wiki / category / f"{slug}.md").write_text(content, encoding="utf-8")
        rel_paths.append(f"{category}/{slug}.md")
    return rel_paths


def main() -> int:
    parser = argparse.ArgumentParser(prog="mimir-graph-benchmark")
    parser.add_argument("--pages", type=int, default=1000)
    args = parser.parse_args()

    rng = random.Random(RNG_SEED)
    with TemporaryDirectory(prefix="mimir-graph-bench-") as tmp:
        root = Path(tmp) / "mimir"
        rel_paths = _generate_wiki(root, args.pages, rng)
        adapter = MarkdownMimirAdapter(root=root)

        start = time.perf_counter()
        graph = adapter._build_link_graph()
        cold_s = time.perf_counter() - start

        adapter._link_graph()  # prime the cache
        start = time.perf_counter()
        adapter._link_graph()
        cached_s = time.perf_counter() - start

        start_page = rel_paths[0]
        start = time.perf_counter()
        related = adapter.related_pages(start_page, depth=2)
        related_s = time.perf_counter() - start

        edges = sum(len(e) for e in graph.forward.values())
        print(f"pages={args.pages} edges={edges} entities={len(graph.entities)}")
        print(f"related_pages({start_page!r}, depth=2) -> {len(related)} pages")
        print()
        print(f"{'operation':<38} {'time':>12}")
        print("-" * 51)
        print(f"{'_build_link_graph() cold':<38} {cold_s * 1000:>9.1f} ms")
        print(f"{'_link_graph() cached':<38} {cached_s * 1_000_000:>9.1f} us")
        print(f"{'related_pages(depth=2)':<38} {related_s * 1000:>9.1f} ms")
        print("-" * 51)
        verdict = "PASS (sub-second)" if cold_s < 1.0 else "FAIL (>= 1s)"
        print(f"cold build exit criterion: {verdict}")
        return 0 if cold_s < 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
