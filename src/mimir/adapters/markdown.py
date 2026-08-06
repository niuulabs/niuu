"""MarkdownMimirAdapter — filesystem-backed Mímir knowledge base.

Directory layout (all paths relative to the configured ``root``):

    wiki/
      index.md          — content catalog, updated on every ingest/lint
      log.md            — append-only chronological record
      <category>/       — subdirectories per top-level category
        <page>.md       — individual wiki pages
    raw/
      <source_id>.json  — immutable source metadata + content
    MIMIR.md            — schema and conventions (seeded on first run)

The adapter is intentionally LLM-free: it manages the *filesystem layer* only.
All synthesis (writing page content, answering queries) is performed by the
Ravn agent using the six mimir_* tool wrappers.

Staleness detection note
------------------------
Lint-level staleness detection (checking whether a wiki page's source has
changed) cannot re-fetch remote URLs, so the lint pass never populates the
``stale`` field of ``MimirLintReport``.  Real staleness detection is triggered
during re-ingest: call ``is_source_stale(source_id, current_content)`` *before*
calling ``ingest()`` — if it returns True, the source has changed and the
derived wiki pages should be reviewed and updated.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from mimir.compiled_truth import (
    _FRONTMATTER_RE,
    extract_wikilinks,
)
from mimir.compiled_truth import (
    parse_page as parse_compiled_truth_page,
)

try:
    from sleipnir.domain.catalog import mimir_page_written as _catalog_page_written
except ImportError:
    _catalog_page_written = None  # type: ignore[assignment]
from mimir.config import EvidenceConfig, RankingConfig
from mimir.learning import consolidate_source as compute_source_consolidation
from mimir.ranking import apply_boosts, tokenize, zone_factor
from niuu.domain.mimir import (
    EntityType,
    LintIssue,
    MimirLintReport,
    MimirMountSummary,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
    PageConfidence,
    PageType,
    ThreadContextRef,
    ThreadOwnershipError,
    ThreadState,
    ThreadYamlSchema,
    compute_content_hash,
    slugify,
)
from niuu.ports.mimir import MimirPort
from niuu.ports.search import SearchPort

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


# Maximum characters per search chunk (~500 tokens).
_CHUNK_MAX_CHARS = 2000
_SEARCH_RESULT_LIMIT = 20

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIMIR_MD_SEED = """\
# Mímir — wiki schema and conventions

## Directory structure

```
wiki/
  index.md          — content catalog, one line per page
  log.md            — append-only chronological record
  technical/        — infrastructure, codebase, how things work
    volundr/        — platform architecture, session management, auth
    ravn/           — agent architecture, tool configs, known quirks
    valaskjalf/     — cluster layout, node specs, workload placement
    k8s/            — kubernetes patterns, Kyverno policies
  projects/         — KVM models, ODIN platform, active Linear sagas
  research/         — topic deep-dives (one-time or ongoing)
  household/        — Burlington home, Netherlands property, finances
  self/             — patterns, preferences, rhythms (earned through observation)
raw/                — immutable source documents (JSON metadata + content)
MIMIR.md            — this file
```

## Page format

- Filename: lowercase, hyphen-separated, `.md` extension
- Title: first `# Heading` in the file
- Summary: second line of the file (after the heading), one sentence
- Links use relative paths: `[auth](../volundr/auth.md)`

## Operations

- **Ingest**: read source → identify takeaways → write or update wiki pages →
  update `index.md` → append to `log.md`
- **Query**: read `index.md` → read relevant pages → synthesise answer →
  optionally write answer as new page → append to `log.md`
- **Lint**: scan for orphans, contradictions, gaps →
  fix or flag → update `index.md` and `log.md`

## Staleness detection

Lint cannot re-fetch remote URLs so it does not populate the `stale` report
field.  Call `is_source_stale(source_id, current_content)` before re-ingesting
a source to detect whether the original content has changed.

## Log entry format

```
## [YYYY-MM-DD] ingest | <title>
## [YYYY-MM-DD] query  | <question>
## [YYYY-MM-DD] lint   | <pages> pages checked, <issues> issues found
```

## Categories

- **technical/**: infrastructure, codebase, how things work
- **projects/**: KVM models, ODIN platform, active Linear sagas
- **research/**: topic deep-dives
- **household/**: Burlington, Netherlands, finances
- **self/**: earned through observation — patterns, preferences, rhythms

## self/ conventions

- Written in third person ("Jozef typically...", "Prefers...")
- Only concrete observations, not inferences
- Updated when a pattern is observed at least twice

## Synthesis workflow

When processing a raw source, follow these steps in order:

1. Call `mimir_query` with the source's main topic — check for existing pages that overlap.
2. Call `mimir_ingest` to persist the raw source (assigns a `source_id`).
3. Read the source content and identify 3-7 distinct key claims worth preserving.
4. For each claim, decide: does it belong on an existing page, or does it warrant a new one?
5. Optionally run 1-2 targeted `web_search` calls if recency matters (versioned tools,
   dated facts, ongoing events). Do not research for research's sake.
6. Write or update wiki pages with `mimir_write`. Each page: concise synthesis, not
   transcription. One claim per section. Relative cross-links. `<!-- sources: id -->` footer.
7. Call `mimir_search` to find related pages — add cross-links where relevant.
8. Append to `wiki/log.md` via `mimir_write`.

## Page quality criteria

A well-formed Mímir wiki page:
- Opens with a `# Title` heading and a one-sentence summary on the second line.
- Uses `##` sections for each distinct claim or concept.
- States facts concisely — never transcribes source text verbatim.
- Links to related pages with relative markdown paths: `[auth](../volundr/auth.md)`.
- Closes with a `<!-- sources: source_id1,source_id2 -->` HTML comment.
- Does NOT contain speculation, personal opinions, or unverified claims.

## Staleness criteria

A page is considered stale when:
- Its `<!-- sources: ... -->` source_ids reference a raw source whose `content_hash`
  has changed (re-ingest detected a difference).
- It references facts that are time-sensitive (software versions, statuses, dates)
  and has not been updated in more than 30 days.

When `mimir_lint` flags a page as stale, re-read the raw source and update the page
with any changed facts before removing the stale flag.
"""

_LOG_INGEST_PREFIX = "ingest"
_LOG_QUERY_PREFIX = "query"
_LOG_LINT_PREFIX = "lint"

_MIN_GAP_MENTION_COUNT = 3  # mentions before a concept is flagged as a gap
_MIN_KEY_FACTS = 3  # minimum key facts in a Compiled Truth zone before flagging as thin
_STALE_CONTENT_DAYS = 60  # days without update before a page is flagged as stale content
_LINT_CACHE_FILE = ".lint-cache.json"  # stores timeline hashes for L09 detection
# Result of the last lint that actually ran, so mount summaries can report a
# lint count without triggering a full corpus pass of their own.
_LINT_LAST_COUNT_KEY = "last_issue_count"
_LINT_LAST_RUN_KEY = "last_checked_at"


def _stat_or_none(path: Path, kind: str) -> os.stat_result | None:
    """Stat *path*, or return None if it went away underneath us.

    Summaries walk directories that ingest and lint are actively writing to, so
    a file can vanish between the glob and the stat.  One racing delete must
    not fail the whole summary — the entry is skipped and reported.
    """
    try:
        return path.stat()
    except OSError as exc:
        logger.warning("Mímir: failed to stat %s %s: %s", kind, path.name, exc)
        return None


# Page types that must have Compiled Truth + Timeline zones
_MANDATORY_COMPILED_TRUTH_TYPES: frozenset[PageType] = frozenset(
    {PageType.entity, PageType.directive, PageType.decision}
)

# Infer page type from top-level directory when frontmatter type is absent
_CATEGORY_TO_PAGE_TYPE: dict[str, str] = {
    "technical": "topic",
    "projects": "entity",
    "research": "topic",
    "household": "observation",
    "self": "preference",
}

_COMPILED_TRUTH_HEADING = "## Compiled Truth"
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

# Typed relationship edges (NIU-1058), an additive FORMAT.md extension:
#   - [[slug]] — rel: works_at — description
# Untyped relationship lines remain valid; the rel label is optional metadata.
_TYPED_EDGE_RE = re.compile(
    r"^\s*-\s*\[\[([^\[\]]+)\]\]\s*[—-]+\s*rel:\s*([a-zA-Z_]+)", re.MULTILINE
)


def _extract_typed_edges(content: str) -> dict[str, str]:
    """Map wikilink slug → relationship type for typed edge lines."""
    return {
        slug.strip().lower(): rel_type.lower() for slug, rel_type in _TYPED_EDGE_RE.findall(content)
    }


def _parse_meta_frontmatter(content: str) -> dict[str, Any]:
    """Parse the frontmatter fields MimirPageMeta carries; tolerant of junk."""
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return {}
    try:
        raw = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    if not isinstance(raw, dict):
        return {}

    parsed: dict[str, Any] = {}
    try:
        parsed["page_type"] = PageType(raw["type"]) if raw.get("type") else None
    except ValueError:
        parsed["page_type"] = None
    try:
        parsed["confidence"] = PageConfidence(raw["confidence"]) if raw.get("confidence") else None
    except ValueError:
        parsed["confidence"] = None
    try:
        parsed["entity_type"] = EntityType(raw["entity_type"]) if raw.get("entity_type") else None
    except ValueError:
        parsed["entity_type"] = None
    related = raw.get("related_entities")
    parsed["related_entities"] = (
        [str(slug) for slug in related] if isinstance(related, list) else []
    )
    return parsed


# (path, score, breakdown, content, meta) — content/meta None when ranking is
# disabled and pages are loaded lazily at materialisation time.
_RankedCandidate = tuple[str, float, "dict[str, float]", "str | None", "MimirPageMeta | None"]


@dataclass
class LinkEdge:
    """A directed wikilink edge, optionally typed (rel)."""

    target: str
    rel: str | None = None


@dataclass
class LinkGraph:
    """In-memory wikilink graph derived from the markdown on disk.

    ``inbound`` is the reverse adjacency: for each target path, the list of
    ``(source_path, rel)`` edges pointing at it. Backlink counts are
    ``len(inbound[path])``; keeping the edges (not just counts) lets the
    relational arm and BFS avoid O(V·E) full-graph scans.
    """

    forward: dict[str, list[LinkEdge]]
    inbound: dict[str, list[tuple[str, str | None]]]
    stem_to_path: dict[str, str]
    entities: list[MimirPageMeta]


# ---------------------------------------------------------------------------
# Path security (local import to avoid circular deps)
# ---------------------------------------------------------------------------


class PathSecurityError(ValueError):
    """Raised when a path escapes the wiki directory."""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class MarkdownMimirAdapter(MimirPort):
    """Filesystem-backed Mímir knowledge base adapter.

    Args:
        root: Root directory for the Mímir store (e.g. ``~/.ravn/mimir``).
        search_port: Optional SearchPort for hybrid FTS/semantic search.
            When provided, ``search()`` delegates to it and pages are
            automatically indexed on write.  When ``None``, the adapter
            falls back to its built-in keyword-counting search.
    """

    def __init__(
        self,
        root: str | Path = "~/.ravn/mimir",
        sleipnir_publisher: object | None = None,
        *,
        search_port: SearchPort | None = None,
        ranking_config: RankingConfig | None = None,
        evidence_config: EvidenceConfig | None = None,
    ) -> None:
        self._root = Path(root).expanduser()
        self._wiki = self._root / "wiki"
        self._raw = self._root / "raw"
        self._threads = self._root / "threads"
        self._schema = self._root / "MIMIR.md"
        self._index = self._wiki / "index.md"
        self._log = self._wiki / "log.md"
        self._sleipnir_publisher = sleipnir_publisher
        self._search_port = search_port
        self._ranking = ranking_config or RankingConfig()
        self._evidence = evidence_config or EvidenceConfig()
        # Paths affected by the most recent write-time consolidation pass,
        # surfaced in the ingest HTTP response (NIU-1062).
        self._last_consolidated: list[str] = []
        # Tracks how many chunks were indexed per page path so we can remove
        # them before re-indexing on update.  Populated lazily on first write.
        self._page_chunk_counts: dict[str, int] = {}
        # Lazily-built wikilink graph (NIU-1058); invalidated on page writes.
        self._graph_cache: LinkGraph | None = None
        # Raw-source metadata keyed by filename, each entry stamped with the
        # (mtime_ns, size) it was parsed from.  list_sources() only needs four
        # fields, but the bodies behind them run to megabytes, so re-parsing
        # every source on every call is what starved the event loop.
        self._source_meta_cache: dict[str, tuple[tuple[int, int], MimirSourceMeta]] = {}
        # Lint walks and rewrites the whole corpus; it now runs off-loop, so
        # serialise it rather than let two passes interleave their cache writes.
        self._lint_lock = asyncio.Lock()
        self._ensure_layout()

    # ------------------------------------------------------------------
    # Layout bootstrap
    # ------------------------------------------------------------------

    def _ensure_layout(self) -> None:
        """Create the directory structure and seed MIMIR.md on first run."""
        self._wiki.mkdir(parents=True, exist_ok=True)
        self._raw.mkdir(parents=True, exist_ok=True)
        self._threads.mkdir(parents=True, exist_ok=True)

        if not self._schema.exists():
            self._schema.write_text(_MIMIR_MD_SEED, encoding="utf-8")
            logger.info("mimir: seeded MIMIR.md at %s", self._schema)

        if not self._index.exists():
            self._index.write_text("# Mímir — content catalog\n\n", encoding="utf-8")

        if not self._log.exists():
            self._log.write_text("# Mímir — activity log\n\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Path security
    # ------------------------------------------------------------------

    def _safe_wiki_path(self, path: str) -> Path:
        """Resolve *path* to an absolute path that lies within the wiki root.

        Raises ``PathSecurityError`` if the resolved path escapes the wiki
        directory (e.g. due to ``../`` traversal components).
        """
        wiki_root = self._wiki.resolve()
        resolved = (self._wiki / path).resolve()
        try:
            resolved.relative_to(wiki_root)
        except ValueError:
            raise PathSecurityError(
                f"Path '{path}' resolves outside the wiki directory '{wiki_root}'"
            )
        return resolved

    def _wiki_relative_path(self, path: Path) -> Path:
        """Return *path* relative to the wiki root, tolerant of symlink aliases."""
        try:
            return path.relative_to(self._wiki)
        except ValueError:
            return path.resolve().relative_to(self._wiki.resolve())

    # ------------------------------------------------------------------
    # MimirPort implementation
    # ------------------------------------------------------------------

    async def ingest(self, source: MimirSource) -> list[str]:
        """Persist a raw source and record the ingest in the log.

        Returns an empty list — page creation is delegated to the agent via
        ``upsert_page()``.  The raw source is stored for staleness tracking.
        After persistence, write-time scoped consolidation (NIU-1062) runs so
        bearing pages get their evidence recomputed without a dream cycle.
        """
        self._write_raw_source(source)
        self._append_log(
            _LOG_INGEST_PREFIX,
            source.title,
            f"source_id={source.source_id} type={source.source_type}",
        )
        logger.info("mimir: ingested source %r (%s)", source.title, source.source_id)
        self._last_consolidated = self.consolidate_source(source)
        return []

    def consolidate_source(self, source: MimirSource) -> list[str]:
        """Write-time scoped consolidation ("micro-dream", NIU-1062).

        Finds the wiki pages the freshly-persisted *source* bears on, then
        recomputes their fact evidence with and without the new source to
        count how many facts it strengthened. Deterministic, zero LLM.
        Gated by ``EvidenceConfig.consolidate_on_ingest``.

        Returns:
            The wiki paths of the bearing pages (empty when gated off).
        """
        if not self._evidence.consolidate_on_ingest:
            return []

        pages = [
            (meta.path, meta.title, content) for meta, content in self._list_pages_with_content()
        ]
        other_sources = [
            (source_id, content)
            for source_id, content in self._all_raw_source_pairs()
            if source_id != source.source_id
        ]
        result = compute_source_consolidation(
            source.source_id,
            source.title,
            source.content,
            pages,
            other_sources,
            self._evidence,
        )
        logger.info(
            "mimir.consolidation: source=%s pages=%d strengthened=%d",
            result.source_id,
            len(result.pages),
            result.strengthened,
        )
        return result.pages

    async def query(self, question: str) -> MimirQueryResult:
        """Return index content + relevant pages for the agent to synthesise.

        The adapter performs keyword-based relevance ranking.  Full synthesis
        is done by the agent via the ``mimir_query`` tool.
        """
        pages = await self.search(question)
        self._append_log(_LOG_QUERY_PREFIX, question)
        return MimirQueryResult(
            question=question,
            answer="",  # filled in by the agent after reading pages
            sources=pages,
        )

    async def lint(self, fix: bool = False) -> MimirLintReport:
        """Scan the wiki and return a structured health-check report (L01–L12).

        When *fix* is ``True``, auto-fixable issues are corrected in-place:

        - L05 broken wikilinks → replaced with the closest fuzzy-match slug
        - L11 stale index     → index.md rebuilt from the current page set
        - L12 invalid frontmatter → missing ``type`` field inferred from path

        Staleness detection (L03) requires re-fetching remote URLs and is
        therefore not performed here; use ``is_source_stale()`` during re-ingest.
        """
        async with self._lint_lock:
            return await asyncio.to_thread(self._lint_sync, fix)

    def _lint_sync(self, fix: bool) -> MimirLintReport:
        """Synchronous body of ``lint`` — see there for semantics."""
        pages_with_content = self._list_pages_with_content()
        all_pages = [meta for meta, _ in pages_with_content]
        content_map = {meta.path: content for meta, content in pages_with_content}
        indexed = self._read_indexed_paths()
        lint_cache = self._load_lint_cache()

        issues: list[LintIssue] = []
        issues.extend(self._check_orphans(all_pages, indexed))
        issues.extend(self._check_contradictions(content_map))
        # L03 (stale sources) requires remote re-fetch — skipped in lint pass
        issues.extend(self._check_gaps(all_pages, content_map))
        issues.extend(self._check_broken_wikilinks(content_map))
        issues.extend(self._check_missing_source_attribution(content_map))
        issues.extend(self._check_thin_pages(content_map))
        issues.extend(self._check_stale_content(all_pages))
        issues.extend(self._check_timeline_edits(content_map, lint_cache))
        issues.extend(self._check_empty_compiled_truth(content_map))
        issues.extend(self._check_stale_index(all_pages, indexed))
        issues.extend(self._check_invalid_frontmatter(content_map))

        if fix:
            issues = self._apply_fixes(issues, content_map, all_pages)
            # Re-read content map after in-place fixes so the cache is accurate
            pages_with_content = self._list_pages_with_content()
            content_map = {meta.path: c for meta, c in pages_with_content}

        self._update_timeline_cache(content_map, lint_cache)
        # Recorded so mount summaries can report a real, attributable lint count
        # instead of running their own pass over the corpus.
        lint_cache[_LINT_LAST_COUNT_KEY] = len(issues)
        lint_cache[_LINT_LAST_RUN_KEY] = datetime.now(UTC).isoformat()
        self._save_lint_cache(lint_cache)

        report = MimirLintReport(issues=issues, pages_checked=len(all_pages))
        sev = report.summary
        self._append_log(
            _LOG_LINT_PREFIX,
            f"{len(all_pages)} pages checked, {len(issues)} issues found",
            f"errors={sev['error']} warnings={sev['warning']} info={sev['info']}",
        )
        logger.info(
            "mimir: lint complete — %d pages checked, %d issues (errors=%d warnings=%d info=%d)",
            len(all_pages),
            len(issues),
            sev["error"],
            sev["warning"],
            sev["info"],
        )
        return report

    async def search(self, query: str) -> list[MimirPage]:
        """Search wiki pages ranked by relevance.

        When a SearchPort is configured, hybrid FTS/semantic search is used
        and results include relevance scores in ``meta.summary``.  Falls back
        to built-in keyword counting when no SearchPort is present.
        """
        if not query.strip():
            return []

        if self._search_port is not None:
            return await self._search_via_port(query)

        return await self._search_keywords(query)

    async def _search_via_port(self, query: str) -> list[MimirPage]:
        """Delegate search to the configured SearchPort, then apply ranking.

        Pipeline (NIU-1057/1058/1062): over-fetch candidates from the port,
        weight chunks by zone (compiled truth vs timeline), dedup to pages,
        inject relational candidates (query-matched entities + their 1-hop
        neighbors), apply multiplicative boosts, and return the top results
        with per-boost attribution on ``meta.score_breakdown``.
        """
        config = self._ranking
        fetch_limit = (
            _SEARCH_RESULT_LIMIT * config.overfetch_factor
            if config.enabled
            else _SEARCH_RESULT_LIMIT
        )
        search_results = await self._search_port.search(query, limit=fetch_limit)  # type: ignore[union-attr]

        # Best chunk per page, zone-weighted.
        base_scores: dict[str, float] = {}
        for result in search_results:
            page_path = result.metadata.get("page_path", "")
            if not page_path or not (self._wiki / page_path).exists():
                continue
            score = result.score
            if config.enabled:
                score *= zone_factor(result.metadata.get("section_heading", ""), config)
            if score > base_scores.get(page_path, 0.0):
                base_scores[page_path] = score

        if not config.enabled:
            return self._load_ranked_pages(
                [(path, score, {}, None, None) for path, score in base_scores.items()]
            )

        # Relational arm (NIU-1058): entities named in the query pull in
        # themselves and their 1-hop neighbourhood even when retrieval missed
        # them entirely (FTS implicit-AND returns nothing for most
        # natural-language queries). graph_injection_base <= 0 disables it.
        graph = self._link_graph()
        entity_paths: set[str] = set()
        neighbor_paths: set[str] = set()
        if config.graph_injection_base > 0:
            entity_paths, neighbor_paths = self._relational_candidates(query, graph)
            for path in entity_paths | neighbor_paths:
                base_scores.setdefault(path, config.graph_injection_base)

        query_tokens = tokenize(query)
        ranked: list[_RankedCandidate] = []
        for path, base in base_scores.items():
            md_path = self._wiki / path
            if not md_path.exists():
                continue
            content = md_path.read_text(encoding="utf-8")
            meta = self._build_page_meta(md_path, content)
            graph_factor = 1.0
            if path in entity_paths:
                graph_factor = config.graph_entity_boost
            elif path in neighbor_paths:
                graph_factor = config.graph_neighbor_boost
            final, breakdown = apply_boosts(
                base,
                meta,
                query_tokens,
                config,
                backlink_count=len(graph.inbound.get(path, ())),
                graph_factor=graph_factor,
            )
            ranked.append((path, final, breakdown, content, meta))

        ranked.sort(key=lambda item: item[1], reverse=True)
        return self._load_ranked_pages(ranked[:_SEARCH_RESULT_LIMIT])

    def _load_ranked_pages(self, ranked: list[_RankedCandidate]) -> list[MimirPage]:
        """Materialise ranked candidates into ordered MimirPages.

        Content/meta are carried through from the ranking pass so each page is
        read from disk exactly once per search; the ``None`` form (ranking
        disabled) loads lazily here instead.
        """
        pages: list[tuple[float, MimirPage]] = []
        for path, score, breakdown, content, meta in ranked:
            if content is None or meta is None:
                md_path = self._wiki / path
                if not md_path.exists():
                    continue
                content = md_path.read_text(encoding="utf-8")
                meta = self._build_page_meta(md_path, content)
            meta.search_score = score
            meta.score_breakdown = breakdown or None
            pages.append((score, MimirPage(meta=meta, content=content)))
        pages.sort(key=lambda t: t[0], reverse=True)
        return [page for _, page in pages]

    async def _search_keywords(self, query: str) -> list[MimirPage]:
        """Built-in keyword-counting fallback search."""
        keywords = set(re.split(r"\W+", query.lower())) - {"", "the", "a", "an", "is", "in"}
        results: list[tuple[int, MimirPage]] = []

        for md_path in self._wiki.rglob("*.md"):
            if md_path.name in {"index.md", "log.md"}:
                continue
            content = md_path.read_text(encoding="utf-8")
            meta = self._build_page_meta(md_path, content)
            haystack = "\n".join(
                part for part in (meta.title, meta.path, meta.summary, content) if part
            ).lower()
            score = sum(haystack.count(kw) for kw in keywords)
            if score == 0:
                continue
            results.append((score, MimirPage(meta=meta, content=content)))

        results.sort(key=lambda t: t[0], reverse=True)
        # Capped like the SearchPort path above. Uncapped, a common word
        # matched most of the wiki and every match was returned with its
        # content: one query against a 1,584-page store produced a 3.2 MB
        # tool result, which exhausted the agent's prompt budget on its own.
        # A ranked search that returns everything has not ranked anything.
        return [page for _, page in results[:_SEARCH_RESULT_LIMIT]]

    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: MimirPageMeta | None = None,
    ) -> None:
        """Create or replace a wiki page and update index.md.

        The *mimir* and *meta* parameters are accepted for interface
        compatibility but are ignored here — this adapter always writes to its
        own filesystem root and does not persist metadata separately.
        When a SearchPort is configured, the page is automatically re-indexed.
        """
        page_path = self._safe_wiki_path(path)
        page_path.parent.mkdir(parents=True, exist_ok=True)

        is_new = not page_path.exists()
        page_path.write_text(content, encoding="utf-8")
        self._graph_cache = None

        if is_new:
            self._add_to_index(path, content)
        else:
            self._update_index_entry(path, content)

        logger.info("mimir: upserted page (new=%s)", is_new)

        # NIU-582: emit mimir.page.written to Sleipnir catalog (best-effort)
        if self._sleipnir_publisher is not None and _catalog_page_written is not None:
            try:
                category = path.split("/")[0] if "/" in path else "uncategorised"
                _event = _catalog_page_written(
                    page_path=path,
                    category=category,
                    author="ravn",
                    source="mimir:markdown",
                )
                await self._sleipnir_publisher.publish(_event)
            except Exception:
                logger.warning("Failed to emit mimir.page.written; continuing.", exc_info=True)

        if self._search_port is not None:
            await self._reindex_page(path, content)

    async def delete_page(self, path: str, mimir: str | None = None) -> bool:
        """Delete a wiki page and remove every derived reference to it."""
        del mimir
        page_path = self._safe_wiki_path(path)
        if not page_path.exists():
            return False

        content = page_path.read_text(encoding="utf-8")
        if self._search_port is not None:
            chunk_count = self._page_chunk_counts.get(path)
            if chunk_count is None:
                category = path.split("/")[0] if "/" in path else "uncategorised"
                page_type = "thread" if path.startswith("threads/") else "wiki"
                chunk_count = len(
                    _chunk_markdown(
                        content,
                        path,
                        category,
                        page_type,
                        page_title=_extract_title(content, path),
                    )
                )
            for index in range(chunk_count):
                await self._search_port.remove(f"{path}::{index}")
            self._page_chunk_counts.pop(path, None)

        page_path.unlink()
        self._remove_from_index(path)
        self._graph_cache = None

        logger.info("mimir: deleted page %s", _sanitize_log(path))
        return True

    async def get_page(self, path: str) -> MimirPage:
        """Return content and metadata for the wiki page at *path* in one call."""
        page_path = self._safe_wiki_path(path)
        if not page_path.exists():
            raise FileNotFoundError(f"Mímir page not found: {path}")
        content = page_path.read_text(encoding="utf-8")
        meta = self._build_page_meta(page_path, content)
        return MimirPage(meta=meta, content=content)

    async def read_page(self, path: str) -> str:
        """Return the raw Markdown content of the page at *path*."""
        page_path = self._safe_wiki_path(path)
        if not page_path.exists():
            raise FileNotFoundError(f"Mímir page not found: {path}")
        return page_path.read_text(encoding="utf-8")

    async def summarize(self) -> MimirMountSummary:
        """Return page/source counts and last-write time without reading bodies.

        Answered entirely from directory entries and ``stat()``: the mount
        listing is polled continuously and previously walked the whole corpus
        (every page body, every source blob, plus a full lint) on each call,
        which blocked the event loop for seconds at a time and cost the service
        its liveness probe.
        """
        return await asyncio.to_thread(self._summarize_sync)

    def _summarize_sync(self) -> MimirMountSummary:
        """Synchronous body of ``summarize`` — see there for semantics."""
        categories: set[str] = set()
        page_count = 0
        last_write: datetime | None = None

        def _note_write(mtime: float) -> datetime:
            return datetime.fromtimestamp(mtime, tz=UTC)

        # glob on a missing directory yields nothing, so no existence guard is
        # needed — and the store's layout is created in the constructor anyway.
        for md_path in self._wiki.rglob("*.md"):
            if md_path.name in {"index.md", "log.md"}:
                continue
            stat = _stat_or_none(md_path, "page")
            if stat is None:
                continue
            page_count += 1
            rel_parts = str(self._wiki_relative_path(md_path)).split("/")
            categories.add(rel_parts[0] if len(rel_parts) > 1 else "uncategorised")
            written = _note_write(stat.st_mtime)
            if last_write is None or written > last_write:
                last_write = written

        source_count = 0
        for json_path in self._raw.glob("*.json"):
            stat = _stat_or_none(json_path, "raw source")
            if stat is None:
                continue
            source_count += 1
            written = _note_write(stat.st_mtime)
            if last_write is None or written > last_write:
                last_write = written

        lint_cache = self._load_lint_cache()
        checked_raw = lint_cache.get(_LINT_LAST_RUN_KEY)
        checked_at: datetime | None = None
        if isinstance(checked_raw, str):
            try:
                checked_at = datetime.fromisoformat(checked_raw)
            except ValueError:
                logger.warning("Mímir: lint cache holds an unparseable timestamp %r", checked_raw)

        return MimirMountSummary(
            page_count=page_count,
            source_count=source_count,
            categories=sorted(categories),
            last_write=last_write,
            lint_issues=int(lint_cache.get(_LINT_LAST_COUNT_KEY, 0)),
            lint_checked_at=checked_at,
        )

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        """List all wiki pages, optionally filtered by category or path prefix."""
        pages_with_content = await asyncio.to_thread(self._list_pages_with_content, category)
        pages = [meta for meta, _ in pages_with_content]
        if prefix is None:
            return pages
        return [meta for meta in pages if meta.path.startswith(prefix)]

    async def read_source(self, source_id: str) -> MimirSource | None:
        """Return the full raw source by ID, or None if not found."""
        return await asyncio.to_thread(self.read_raw_source, source_id)

    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        """List all ingested raw sources.

        When *unprocessed_only* is True, returns only sources not yet referenced
        by any wiki page (cross-referenced via ``<!-- sources: ... -->`` footers).
        """
        return await asyncio.to_thread(self._list_sources_sync, unprocessed_only)

    def _list_sources_sync(self, unprocessed_only: bool) -> list[MimirSourceMeta]:
        """Synchronous body of ``list_sources`` — see there for semantics."""
        all_sources = self._read_source_meta()

        # With nothing ingested there is nothing to cross-reference, so skip the
        # page walk the unprocessed filter would otherwise do.
        if not unprocessed_only or not all_sources:
            return all_sources

        # Collect all source_ids referenced across wiki pages
        referenced_ids: set[str] = set()
        for _, content in self._list_pages_with_content():
            referenced_ids.update(self._extract_source_ids(content))

        # Build a map of content_hash → source_id for all referenced sources so that
        # re-ingested sources with identical content (same hash, new id) are not
        # re-synthesised.
        referenced_hashes: set[str] = set()
        for src in all_sources:
            if src.source_id in referenced_ids:
                # Load the full source to get its hash
                full = self.read_raw_source(src.source_id)
                if full is not None:
                    referenced_hashes.add(full.content_hash)

        results: list[MimirSourceMeta] = []
        for src in all_sources:
            if src.source_id in referenced_ids:
                continue
            full = self.read_raw_source(src.source_id)
            if full is not None and full.content_hash in referenced_hashes:
                continue
            results.append(src)
        return results

    # ------------------------------------------------------------------
    # Thread methods
    # ------------------------------------------------------------------

    async def create_thread(
        self,
        title: str,
        weight: float = 0.5,
        context_refs: list[ThreadContextRef] | None = None,
        next_action_hint: str | None = None,
    ) -> MimirPage:
        """Create a new thread YAML + Markdown pair under threads/."""
        slug = slugify(title)
        if not slug:
            raise ValueError(f"Cannot create thread: title {title!r} produces an empty slug")
        yaml_path = self._safe_thread_path(f"threads/{slug}")
        md_path = self._safe_thread_md_path(slug)

        if yaml_path.exists():
            raise FileExistsError(f"Thread already exists: threads/{slug}")

        now = datetime.now(UTC)
        first_ref_summary = context_refs[0].ref_summary if context_refs else ""
        schema = ThreadYamlSchema(
            title=title,
            state=ThreadState.open,
            weight=weight,
            created_at=now,
            updated_at=now,
            owner_id=None,
            next_action_hint=next_action_hint,
            resolved_artifact_path=None,
            context_refs=context_refs or [],
            weight_signals={
                "age_days": 0.0,
                "mention_count": 0,
                "operator_engagement_count": 0,
                "peer_interest_count": 0,
                "sub_thread_count": 0,
            },
        )
        schema.to_yaml(yaml_path)

        md_content = _build_thread_md(title, now, first_ref_summary)
        md_path.write_text(md_content, encoding="utf-8")
        logger.info("mimir: created thread '%s' at threads/%s", title, slug)

        return self._schema_to_page(slug, schema, content=md_content)

    async def get_thread(self, path: str) -> MimirPage:
        """Return full thread data, loading both YAML metadata and Markdown content."""
        slug = path.removeprefix("threads/")
        yaml_path = self._safe_thread_path(path)
        md_path = self._safe_thread_md_path(slug)

        if not yaml_path.exists():
            raise FileNotFoundError(f"Thread not found: {path}")

        schema = ThreadYamlSchema.from_yaml(yaml_path)
        content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        return self._schema_to_page(slug, schema, content=content)

    async def get_thread_queue(
        self,
        owner_id: str | None = None,
        limit: int = 50,
    ) -> list[MimirPage]:
        """Return open threads sorted by weight — hot path, YAML only."""
        threads: list[MimirPage] = []
        for yaml_path in self._threads.glob("*.yaml"):
            try:
                schema = ThreadYamlSchema.from_yaml(yaml_path)
            except Exception:
                logger.warning("mimir: skipping invalid thread YAML: %s", yaml_path.name)
                continue

            if schema.state in (ThreadState.closed, ThreadState.dissolved):
                continue

            if owner_id and schema.owner_id and schema.owner_id != owner_id:
                continue

            threads.append(self._schema_to_page(yaml_path.stem, schema))

        threads.sort(key=lambda p: p.meta.thread_weight or 0.0, reverse=True)
        return threads[:limit]

    async def update_thread_state(self, path: str, state: ThreadState) -> None:
        """Transition a thread to *state* — writes YAML only."""
        yaml_path = self._safe_thread_path(path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Thread not found: {path}")
        schema = ThreadYamlSchema.from_yaml(yaml_path)
        schema.state = state
        schema.updated_at = datetime.now(UTC)
        schema.to_yaml(yaml_path)

    async def list_threads(
        self,
        state: ThreadState | None = None,
        limit: int = 100,
    ) -> list[MimirPage]:
        """List thread pages, optionally filtered by *state*."""
        threads_dir = self._root / "threads"
        if not threads_dir.exists():
            return []
        results: list[MimirPage] = []
        for yaml_path in sorted(threads_dir.glob("*.yaml")):
            try:
                schema = ThreadYamlSchema.from_yaml(yaml_path)
            except Exception:
                continue
            if state is not None and schema.state != state:
                continue
            slug = yaml_path.stem
            path = f"threads/{slug}"
            results.append(
                MimirPage(
                    meta=MimirPageMeta(
                        path=path,
                        title=schema.title,
                        summary=schema.next_action_hint or "",
                        category="threads",
                        updated_at=schema.updated_at,
                        source_ids=[],
                    ),
                    content="",
                )
            )
            if len(results) >= limit:
                break
        return results

    async def update_thread_weight(
        self,
        path: str,
        weight: float,
        signals: dict | None = None,
    ) -> None:
        """Update the weight score for a thread — writes YAML only."""
        yaml_path = self._safe_thread_path(path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Thread not found: {path}")
        schema = ThreadYamlSchema.from_yaml(yaml_path)
        schema.weight = weight
        schema.updated_at = datetime.now(UTC)
        if signals is not None:
            schema.weight_signals = signals
        schema.to_yaml(yaml_path)

    async def assign_thread_owner(self, path: str, owner_id: str | None) -> None:
        """Assign (or clear) the owner of a thread with lock-file mutual exclusion."""
        yaml_path = self._safe_thread_path(path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Thread not found: {path}")
        lock_path = yaml_path.with_suffix(".lock")
        lock_path.touch()
        try:
            schema = ThreadYamlSchema.from_yaml(yaml_path)
            if owner_id and schema.owner_id and schema.owner_id != owner_id:
                raise ThreadOwnershipError(path, schema.owner_id)
            schema.owner_id = owner_id
            schema.updated_at = datetime.now(UTC)
            schema.to_yaml(yaml_path)
        finally:
            lock_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Thread helpers
    # ------------------------------------------------------------------

    def _safe_thread_path(self, path: str) -> Path:
        """Resolve a thread stem path to its YAML file, rejecting path traversal.

        Raises ``PathSecurityError`` if the resolved path escapes the threads
        directory (e.g. due to ``../`` traversal in the slug).
        """
        slug = path.removeprefix("threads/")
        threads_root = self._threads.resolve()
        resolved = (self._threads / f"{slug}.yaml").resolve()
        try:
            resolved.relative_to(threads_root)
        except ValueError:
            raise PathSecurityError(
                f"Path '{path}' resolves outside the threads directory '{threads_root}'"
            )
        return resolved

    def _safe_thread_md_path(self, slug: str) -> Path:
        """Resolve a thread slug to its Markdown file, rejecting path traversal."""
        threads_root = self._threads.resolve()
        resolved = (self._threads / f"{slug}.md").resolve()
        try:
            resolved.relative_to(threads_root)
        except ValueError:
            raise PathSecurityError(
                f"Slug '{slug}' resolves outside the threads directory '{threads_root}'"
            )
        return resolved

    def _schema_to_page(
        self,
        slug: str,
        schema: ThreadYamlSchema,
        content: str = "",
    ) -> MimirPage:
        """Build a MimirPage from a ThreadYamlSchema."""
        meta = MimirPageMeta(
            path=f"threads/{slug}",
            title=schema.title,
            summary=schema.next_action_hint or "",
            category="threads",
            updated_at=schema.updated_at,
            source_ids=[],
            thread_state=schema.state,
            thread_weight=schema.weight,
            is_thread=True,
        )
        return MimirPage(meta=meta, content=content)

    # ------------------------------------------------------------------
    # Search index management
    # ------------------------------------------------------------------

    async def rebuild_search_index(self) -> int:
        """Re-index all wiki pages from the filesystem.

        Idempotent — clears any previously tracked chunk counts and rebuilds
        the entire search index from the current ``wiki/`` directory.

        Returns:
            Number of pages indexed.
        """
        if self._search_port is None:
            return 0

        # Wipe the index before re-indexing to avoid stale orphan chunks.
        await self._search_port.rebuild()
        self._page_chunk_counts.clear()
        count = 0
        for md_path in self._wiki.rglob("*.md"):
            if md_path.name in {"index.md", "log.md"}:
                continue
            content = md_path.read_text(encoding="utf-8")
            rel = str(self._wiki_relative_path(md_path))
            await self._reindex_page(rel, content)
            count += 1

        logger.info("mimir: rebuilt search index — %d pages", count)
        return count

    async def _reindex_page(self, path: str, content: str) -> None:
        """Remove old chunks for *path* and index fresh ones."""
        assert self._search_port is not None  # noqa: S101 — caller-checked

        # Remove previously indexed chunks for this page.
        old_count = self._page_chunk_counts.get(path, 0)
        for i in range(old_count):
            await self._search_port.remove(f"{path}::{i}")

        # Derive metadata from path.
        parts = path.split("/")
        category = parts[0] if len(parts) > 1 else "uncategorised"
        page_type = "thread" if path.startswith("threads/") else "wiki"

        page_title = _extract_title(content, path)
        chunks = _chunk_markdown(content, path, category, page_type, page_title=page_title)
        for i, (chunk_content, chunk_meta) in enumerate(chunks):
            await self._search_port.index(f"{path}::{i}", chunk_content, chunk_meta)

        self._page_chunk_counts[path] = len(chunks)

    # ------------------------------------------------------------------
    # Link graph (NIU-1058)
    # ------------------------------------------------------------------

    def _link_graph(self) -> LinkGraph:
        """Return the wikilink/related-entities graph, building it lazily.

        Derived purely from the markdown on disk — nothing is persisted; the
        cache is invalidated on every page write.
        """
        if self._graph_cache is not None:
            return self._graph_cache
        self._graph_cache = self._build_link_graph()
        return self._graph_cache

    def _build_link_graph(self) -> LinkGraph:
        pages: list[tuple[str, str, MimirPageMeta]] = []
        stem_to_path: dict[str, str] = {}
        for md_path in sorted(self._wiki.rglob("*.md")):
            if md_path.name in {"index.md", "log.md"}:
                continue
            rel = str(self._wiki_relative_path(md_path))
            content = md_path.read_text(encoding="utf-8")
            meta = self._build_page_meta(md_path, content)
            pages.append((rel, content, meta))
            stem = md_path.stem.lower()
            # entities/ pages win stem collisions — wikilinks resolve there first.
            if stem not in stem_to_path or rel.startswith("entities/"):
                stem_to_path[stem] = rel

        forward: dict[str, list[LinkEdge]] = {}
        inbound: dict[str, list[tuple[str, str | None]]] = {}
        entities: list[MimirPageMeta] = []
        for rel, content, meta in pages:
            if meta.page_type is not None and meta.page_type.value == "entity":
                entities.append(meta)
            rel_types = _extract_typed_edges(content)
            edges: list[LinkEdge] = []
            slugs = set(extract_wikilinks(content)) | set(meta.related_entities)
            for slug in slugs:
                target = stem_to_path.get(slug.strip().lower())
                if target is None or target == rel:
                    continue
                edge_rel = rel_types.get(slug.strip().lower())
                edges.append(LinkEdge(target=target, rel=edge_rel))
                inbound.setdefault(target, []).append((rel, edge_rel))
            forward[rel] = edges

        return LinkGraph(
            forward=forward,
            inbound=inbound,
            stem_to_path=stem_to_path,
            entities=entities,
        )

    def _relational_candidates(self, query: str, graph: LinkGraph) -> tuple[set[str], set[str]]:
        """Paths of entities named in *query* and their 1-hop neighbourhood."""
        query_tokens = tokenize(query)
        entity_paths: set[str] = set()
        for meta in graph.entities:
            title_tokens = tokenize(meta.title)
            if title_tokens and title_tokens <= query_tokens:
                entity_paths.add(meta.path)
                continue
            stem = Path(meta.path).stem.lower()
            if stem.replace("-", " ") in query.lower():
                entity_paths.add(meta.path)

        neighbors: set[str] = set()
        for path in entity_paths:
            neighbors.update(edge.target for edge in graph.forward.get(path, []))
            neighbors.update(source for source, _ in graph.inbound.get(path, []))
        return entity_paths, neighbors - entity_paths

    def related_pages(self, path: str, depth: int = 1, rel: str | None = None) -> list[dict]:
        """BFS over the link graph from *path* up to *depth* hops (NIU-1058).

        Returns dicts with target path, hop distance, edge type (when typed),
        and direction. Used by ``GET /mimir/related`` and the MCP tool.
        """
        graph = self._link_graph()
        results: list[dict] = []
        visited = {path}
        frontier = [path]
        for hop in range(1, max(1, depth) + 1):
            next_frontier: list[str] = []
            for current in frontier:
                for edge in graph.forward.get(current, []):
                    if edge.target in visited or (rel is not None and edge.rel != rel):
                        continue
                    visited.add(edge.target)
                    next_frontier.append(edge.target)
                    results.append(
                        {"path": edge.target, "hop": hop, "rel": edge.rel, "direction": "out"}
                    )
                for source, edge_rel in graph.inbound.get(current, []):
                    if source in visited or (rel is not None and edge_rel != rel):
                        continue
                    visited.add(source)
                    next_frontier.append(source)
                    results.append({"path": source, "hop": hop, "rel": edge_rel, "direction": "in"})
            frontier = next_frontier
        return results

    def filesystem_root(self) -> Path | None:
        """This adapter is filesystem-backed; expose the store root."""
        return self._root

    def entity_index(self) -> list[MimirPageMeta]:
        """All entity-typed pages (feeds ``GET /mimir/entities``, NIU-1059)."""
        return list(self._link_graph().entities)

    # ------------------------------------------------------------------
    # Raw source storage
    # ------------------------------------------------------------------

    def _write_raw_source(self, source: MimirSource) -> None:
        """Persist a raw source as JSON in the raw/ directory."""
        dest = self._raw / f"{source.source_id}.json"
        data = {
            "source_id": source.source_id,
            "title": source.title,
            "content": source.content,
            "source_type": source.source_type,
            "origin_url": source.origin_url,
            "content_hash": source.content_hash,
            "ingested_at": source.ingested_at.isoformat(),
        }
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _all_raw_source_pairs(self) -> list[tuple[str, str]]:
        """Return ``(source_id, content)`` pairs for every persisted raw source."""
        if not self._raw.exists():
            return []
        pairs: list[tuple[str, str]] = []
        for json_path in sorted(self._raw.glob("*.json")):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                pairs.append((data["source_id"], data["content"]))
            except Exception as exc:
                logger.warning("Mímir: failed to read raw source %s: %s", json_path.name, exc)
        return pairs

    def _read_source_meta(self) -> list[MimirSourceMeta]:
        """Return metadata for every persisted raw source.

        Raw sources are append-only megabyte-scale JSON blobs, so results are
        cached per file and re-parsed only when the file's ``(mtime_ns, size)``
        changes.  A source that is unreadable or malformed is dropped from the
        listing with a warning rather than failing the whole listing — one bad
        file must not make the mount look empty.
        """
        metas: list[MimirSourceMeta] = []
        live_names: set[str] = set()

        for json_path in self._raw.glob("*.json"):
            stat = _stat_or_none(json_path, "raw source")
            if stat is None:
                continue

            live_names.add(json_path.name)
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = self._source_meta_cache.get(json_path.name)
            if cached is not None and cached[0] == stamp:
                metas.append(cached[1])
                continue

            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                meta = MimirSourceMeta(
                    source_id=data["source_id"],
                    title=data["title"],
                    ingested_at=datetime.fromisoformat(data["ingested_at"]),
                    source_type=data["source_type"],
                )
            except Exception as exc:
                logger.warning("Mímir: failed to read raw source %s: %s", json_path.name, exc)
                continue

            self._source_meta_cache[json_path.name] = (stamp, meta)
            metas.append(meta)

        # Drop cache entries for sources that no longer exist on disk.
        for stale in self._source_meta_cache.keys() - live_names:
            del self._source_meta_cache[stale]

        return metas

    def read_raw_source(self, source_id: str) -> MimirSource | None:
        """Read a persisted raw source by ID.  Returns None if not found."""
        dest = self._raw / f"{source_id}.json"
        if not dest.exists():
            return None
        data = json.loads(dest.read_text(encoding="utf-8"))
        return MimirSource(
            source_id=data["source_id"],
            title=data["title"],
            content=data["content"],
            source_type=data["source_type"],
            origin_url=data.get("origin_url"),
            content_hash=data["content_hash"],
            ingested_at=datetime.fromisoformat(data["ingested_at"]),
        )

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Return the SHA-256 hex digest of *content*.

        Delegates to ``niuu.domain.mimir.compute_content_hash``.
        Kept as a static method for backwards compatibility with callers that
        reference it via ``MarkdownMimirAdapter.compute_content_hash``.
        """
        return compute_content_hash(content)

    def is_source_stale(self, source_id: str, current_content: str) -> bool:
        """Return True if the stored hash differs from the hash of *current_content*.

        This is the correct staleness check: call it before re-ingesting a
        source to see whether the content has changed since the last ingest.
        """
        existing = self.read_raw_source(source_id)
        if existing is None:
            return False
        current_hash = compute_content_hash(current_content)
        return existing.content_hash != current_hash

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _read_indexed_paths(self) -> set[str]:
        """Return the set of page paths currently listed in index.md.

        Catalog entries have the exact shape the index writers emit:
        ``- [title](path) — summary …``. The path is matched as the
        ``](path)`` immediately preceding the ``—`` summary separator, which
        makes the parse robust against the two things that previously broke
        L11 sync detection:

        * **Brackets in the title** (e.g. directive pages whose H1 is
          ``[INITIATIVE TASK — …]``) — a ``[^\\]]`` title class dropped these
          entries, reporting real pages as perpetually "missing".
        * **Markdown links embedded in the summary prose** — an un-anchored
          scan matched these as catalog paths, reporting phantom "stale"
          entries no rebuild could clear.

        Greedy ``.*`` over the title absorbs nested brackets; requiring the
        ``—`` after ``](path)`` excludes summary links (which are followed by
        prose, not the separator).
        """
        if not self._index.exists():
            return set()
        content = self._index.read_text(encoding="utf-8")
        return set(re.findall(r"(?m)^\s*-\s+\[.*\]\(([^)]+\.md)\)\s*—", content))

    def _add_to_index(self, path: str, content: str) -> None:
        """Append a new entry to index.md."""
        title = _extract_title(content, path)
        summary = _extract_summary(content)
        category = path.split("/")[0] if "/" in path else "uncategorised"
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = f"- [{title}]({path}) — {summary} *(added {date_str}, {category})*\n"
        with self._index.open("a", encoding="utf-8") as f:
            f.write(entry)

    def _update_index_entry(self, path: str, content: str) -> None:
        """Update the summary in an existing index.md entry for *path*."""
        if not self._index.exists():
            return
        index_content = self._index.read_text(encoding="utf-8")
        title = _extract_title(content, path)
        summary = _extract_summary(content)
        new_lines = []
        for line in index_content.splitlines(keepends=True):
            if f"]({path})" in line:
                meta_match = re.search(r"\*\(.*?\)\*", line)
                meta = meta_match.group(0) if meta_match else ""
                line = f"- [{title}]({path}) — {summary} {meta}\n".rstrip() + "\n"
            new_lines.append(line)
        self._index.write_text("".join(new_lines), encoding="utf-8")

    def _remove_from_index(self, path: str) -> None:
        if not self._index.exists():
            return
        marker = f"]({path})"
        lines = self._index.read_text(encoding="utf-8").splitlines(keepends=True)
        self._index.write_text(
            "".join(line for line in lines if marker not in line),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Log management
    # ------------------------------------------------------------------

    def _append_log(self, prefix: str, subject: str, detail: str = "") -> None:
        """Append a structured entry to log.md."""
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = f"\n## [{date_str}] {prefix} | {subject}\n"
        if detail:
            entry += f"{detail}\n"
        with self._log.open("a", encoding="utf-8") as f:
            f.write(entry)

    # ------------------------------------------------------------------
    # Lint helpers — L01–L12
    # ------------------------------------------------------------------

    def _check_orphans(self, pages: list[MimirPageMeta], indexed: set[str]) -> list[LintIssue]:
        """L01 — pages not linked in index.md."""
        return [
            LintIssue(
                id="L01",
                severity="warning",
                message="Page is not linked in index.md",
                page_path=p.path,
                auto_fixable=False,
            )
            for p in pages
            if p.path not in indexed
        ]

    def _check_contradictions(self, content_map: dict[str, str]) -> list[LintIssue]:
        """L02 — pages containing a [CONTRADICTION] flag marker."""
        return [
            LintIssue(
                id="L02",
                severity="warning",
                message="Page contains a contradiction flag marker",
                page_path=path,
                auto_fixable=False,
            )
            for path, content in content_map.items()
            if "[CONTRADICTION]" in content or "⚠️ contradiction" in content.lower()
        ]

    def _check_gaps(
        self, pages: list[MimirPageMeta], content_map: dict[str, str]
    ) -> list[LintIssue]:
        """L04 — concepts mentioned ≥ N times without a dedicated page."""
        existing_titles = {p.title.lower() for p in pages}
        mention_counts: dict[str, int] = {}

        for content in content_map.values():
            for concept in re.findall(r"\[\[([^\]]+)\]\]", content):
                key = concept.lower().strip()
                if key not in existing_titles:
                    mention_counts[key] = mention_counts.get(key, 0) + 1

        return [
            LintIssue(
                id="L04",
                severity="info",
                message=(f"Concept '{concept}' mentioned {count} times but has no dedicated page"),
                page_path=concept,
                auto_fixable=False,
            )
            for concept, count in mention_counts.items()
            if count >= _MIN_GAP_MENTION_COUNT
        ]

    def _check_broken_wikilinks(self, content_map: dict[str, str]) -> list[LintIssue]:
        """L05 — [[slug]] references whose target page does not exist."""
        known_slugs = {Path(path).stem for path in content_map}
        issues: list[LintIssue] = []

        for page_path, content in content_map.items():
            for link in extract_wikilinks(content):
                slug = link.strip()
                if slug not in known_slugs:
                    suggestions = difflib.get_close_matches(slug, known_slugs, n=1, cutoff=0.6)
                    hint = f" (closest match: '{suggestions[0]}')" if suggestions else ""
                    issues.append(
                        LintIssue(
                            id="L05",
                            severity="warning",
                            message=f"Broken wikilink [[{link}]] — no matching page found{hint}",
                            page_path=page_path,
                            auto_fixable=bool(suggestions),
                        )
                    )
        return issues

    def _check_missing_source_attribution(self, content_map: dict[str, str]) -> list[LintIssue]:
        """L06 — timeline entries lacking [Source: ...] attribution."""
        issues: list[LintIssue] = []

        for page_path, content in content_map.items():
            page = parse_compiled_truth_page(content)
            for entry in page.timeline_entries:
                if not entry.has_source:
                    issues.append(
                        LintIssue(
                            id="L06",
                            severity="error",
                            message=f"Timeline entry missing [Source: ...]: {entry.raw!r}",
                            page_path=page_path,
                            auto_fixable=False,
                        )
                    )
        return issues

    def _check_thin_pages(self, content_map: dict[str, str]) -> list[LintIssue]:
        """L07 — compiled-truth pages with fewer than _MIN_KEY_FACTS key facts."""
        issues: list[LintIssue] = []

        for page_path, content in content_map.items():
            page = parse_compiled_truth_page(content)
            if page.page_type not in _MANDATORY_COMPILED_TRUTH_TYPES:
                continue
            ct = page.compiled_truth
            if not ct:
                continue
            fact_count = len(re.findall(r"^#{3,}|^[-*]\s+", ct, re.MULTILINE))
            if fact_count < _MIN_KEY_FACTS:
                issues.append(
                    LintIssue(
                        id="L07",
                        severity="warning",
                        message=(
                            f"Compiled Truth has {fact_count} key fact(s) "
                            f"(minimum is {_MIN_KEY_FACTS})"
                        ),
                        page_path=page_path,
                        auto_fixable=False,
                    )
                )
        return issues

    def _check_stale_content(self, pages: list[MimirPageMeta]) -> list[LintIssue]:
        """L08 — pages not updated in _STALE_CONTENT_DAYS days."""
        now = datetime.now(UTC)
        issues: list[LintIssue] = []

        for meta in pages:
            age_days = (now - meta.updated_at).days
            if age_days >= _STALE_CONTENT_DAYS:
                issues.append(
                    LintIssue(
                        id="L08",
                        severity="info",
                        message=(
                            f"Page not updated in {age_days} days "
                            f"(threshold: {_STALE_CONTENT_DAYS})"
                        ),
                        page_path=meta.path,
                        auto_fixable=False,
                    )
                )
        return issues

    def _check_timeline_edits(
        self, content_map: dict[str, str], lint_cache: dict
    ) -> list[LintIssue]:
        """L09 — detect when a timeline section was edited rather than appended to."""
        timeline_cache = lint_cache.get("timeline_hashes", {})
        issues: list[LintIssue] = []

        for page_path, content in content_map.items():
            page = parse_compiled_truth_page(content)
            if not page.timeline_entries:
                continue

            current_entries = [e.raw for e in page.timeline_entries]
            stored = timeline_cache.get(page_path)
            if stored is None:
                continue

            stored_entries: list[str] = stored.get("entries", [])
            stored_hash: str = stored.get("hash", "")
            current_hash = compute_content_hash("\n".join(current_entries))

            if current_hash == stored_hash:
                continue

            # Append-only check: stored entries must be an exact prefix of current
            is_append_only = (
                len(current_entries) >= len(stored_entries)
                and current_entries[: len(stored_entries)] == stored_entries
            )

            if not is_append_only:
                issues.append(
                    LintIssue(
                        id="L09",
                        severity="error",
                        message=(
                            "Timeline section was edited (not just appended to) since last lint"
                        ),
                        page_path=page_path,
                        auto_fixable=False,
                    )
                )
        return issues

    def _check_empty_compiled_truth(self, content_map: dict[str, str]) -> list[LintIssue]:
        """L10 — pages with an empty ## Compiled Truth section."""
        issues: list[LintIssue] = []

        for page_path, content in content_map.items():
            page = parse_compiled_truth_page(content)
            if page.page_type not in _MANDATORY_COMPILED_TRUTH_TYPES:
                continue
            if _COMPILED_TRUTH_HEADING not in content:
                continue
            if not page.compiled_truth.strip():
                issues.append(
                    LintIssue(
                        id="L10",
                        severity="warning",
                        message="Compiled Truth section is present but empty",
                        page_path=page_path,
                        auto_fixable=False,
                    )
                )
        return issues

    def _check_stale_index(self, pages: list[MimirPageMeta], indexed: set[str]) -> list[LintIssue]:
        """L11 — index.md out of sync with wiki/ directory."""
        all_paths = {p.path for p in pages}
        extra = indexed - all_paths
        missing = all_paths - indexed
        if not extra and not missing:
            return []
        count = len(extra) + len(missing)
        return [
            LintIssue(
                id="L11",
                severity="warning",
                message=(
                    f"index.md is out of sync with wiki/ ({count} discrepancy/discrepancies: "
                    f"{len(missing)} missing, {len(extra)} stale)"
                ),
                page_path="index.md",
                auto_fixable=True,
            )
        ]

    def _check_invalid_frontmatter(self, content_map: dict[str, str]) -> list[LintIssue]:
        """L12 — pages missing the required 'type' frontmatter field."""
        issues: list[LintIssue] = []

        for page_path, content in content_map.items():
            page = parse_compiled_truth_page(content)
            if page.page_type is None:
                inferred = self._infer_page_type(page_path)
                issues.append(
                    LintIssue(
                        id="L12",
                        severity="warning",
                        message=(
                            f"Missing required 'type' frontmatter field (suggested: '{inferred}')"
                        ),
                        page_path=page_path,
                        auto_fixable=True,
                    )
                )
        return issues

    # ------------------------------------------------------------------
    # Auto-fix helpers
    # ------------------------------------------------------------------

    def _apply_fixes(
        self,
        issues: list[LintIssue],
        content_map: dict[str, str],
        pages: list[MimirPageMeta],
    ) -> list[LintIssue]:
        """Apply in-place fixes for auto-fixable issues; return remaining issues.

        All fixes for a given page are applied to the same in-memory content
        before the file is written, so multiple fix types on the same page
        do not overwrite each other.
        """
        known_slugs = {Path(path).stem for path in content_map}
        remaining: list[LintIssue] = []

        # Collect fixable issue IDs per page
        page_fixes: dict[str, set[str]] = {}  # page_path → set of fix IDs
        fix_l11 = False

        for issue in issues:
            if issue.id == "L05" and issue.auto_fixable:
                page_fixes.setdefault(issue.page_path, set()).add("L05")
            elif issue.id == "L11":
                fix_l11 = True
            elif issue.id == "L12":
                page_fixes.setdefault(issue.page_path, set()).add("L12")
            else:
                remaining.append(issue)

        # Apply all fixes for each page in a single write
        for page_path, fix_ids in page_fixes.items():
            content = content_map.get(page_path, "")
            original = content

            if "L05" in fix_ids:
                content = self._fix_broken_wikilinks(content, known_slugs)
                if content == original:
                    # No match found — keep the issue
                    remaining.append(
                        LintIssue(
                            id="L05",
                            severity="warning",
                            message="Broken wikilinks could not be resolved (no close match)",
                            page_path=page_path,
                            auto_fixable=False,
                        )
                    )

            if "L12" in fix_ids:
                content = self._fix_invalid_frontmatter(page_path, content)

            if content != original:
                page_file = self._wiki / page_path
                page_file.write_text(content, encoding="utf-8")
                logger.info(
                    "mimir: auto-fixed %s in %s",
                    ", ".join(sorted(fix_ids)),
                    page_path,
                )

        if fix_l11:
            self._rebuild_index(pages, content_map)
            logger.info("mimir: L11 auto-fixed stale index.md (rebuilt)")

        return remaining

    def _fix_broken_wikilinks(self, content: str, known_slugs: set[str]) -> str:
        """Replace broken [[slug]] links with the closest fuzzy-match slug."""

        def replace_link(match: re.Match) -> str:
            link = match.group(1).strip()
            if link in known_slugs:
                return match.group(0)
            suggestions = difflib.get_close_matches(link, known_slugs, n=1, cutoff=0.6)
            if suggestions:
                return f"[[{suggestions[0]}]]"
            return match.group(0)

        return _WIKILINK_RE.sub(replace_link, content)

    def _fix_invalid_frontmatter(self, page_path: str, content: str) -> str:
        """Add an inferred 'type' field to pages with missing frontmatter."""
        inferred = self._infer_page_type(page_path)
        fm_match = _FRONTMATTER_RE.match(content)
        if fm_match:
            try:
                fm = yaml.safe_load(fm_match.group(1)) or {}
            except yaml.YAMLError:
                fm = {}
            fm["type"] = inferred
            fm_yaml = yaml.dump(
                fm, default_flow_style=False, allow_unicode=True, sort_keys=False
            ).strip()
            return f"---\n{fm_yaml}\n---\n{content[fm_match.end() :]}"
        # No frontmatter at all — prepend a minimal block
        return f"---\ntype: {inferred}\n---\n{content}"

    def _rebuild_index(self, pages: list[MimirPageMeta], content_map: dict[str, str]) -> None:
        """Rewrite index.md from the current page set."""
        header = "# Mímir — content catalog\n\n"
        lines: list[str] = []
        for meta in sorted(pages, key=lambda p: p.path):
            content = content_map.get(meta.path, "")
            summary = _extract_summary(content)
            date_str = meta.updated_at.strftime("%Y-%m-%d")
            lines.append(
                f"- [{meta.title}]({meta.path}) — {summary} *(updated {date_str}, {meta.category})*"
            )
        self._index.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")

    def _infer_page_type(self, page_path: str) -> str:
        """Infer a page type from its top-level directory."""
        category = page_path.split("/")[0] if "/" in page_path else ""
        return _CATEGORY_TO_PAGE_TYPE.get(category, "topic")

    # ------------------------------------------------------------------
    # Lint cache — used by L09 timeline edit detection
    # ------------------------------------------------------------------

    def _load_lint_cache(self) -> dict:
        """Load the persisted lint cache, or return an empty dict."""
        cache_path = self._wiki / _LINT_CACHE_FILE
        if not cache_path.exists():
            return {}
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_lint_cache(self, cache: dict) -> None:
        """Persist the lint cache to disk."""
        cache_path = self._wiki / _LINT_CACHE_FILE
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    def _update_timeline_cache(self, content_map: dict[str, str], cache: dict) -> None:
        """Update the timeline_hashes section of the lint cache."""
        timeline_cache: dict[str, dict] = {}
        for page_path, content in content_map.items():
            page = parse_compiled_truth_page(content)
            if not page.timeline_entries:
                continue
            current_entries = [e.raw for e in page.timeline_entries]
            timeline_cache[page_path] = {
                "hash": compute_content_hash("\n".join(current_entries)),
                "entries": current_entries,
            }
        cache["timeline_hashes"] = timeline_cache

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def _list_pages_with_content(
        self,
        category: str | None = None,
    ) -> list[tuple[MimirPageMeta, str]]:
        """Return all wiki pages with their content, optionally filtered by category."""
        results: list[tuple[MimirPageMeta, str]] = []
        search_root = self._wiki / category if category else self._wiki

        if not search_root.exists():
            return results

        for md_path in search_root.rglob("*.md"):
            if md_path.name in {"index.md", "log.md"}:
                continue
            content = md_path.read_text(encoding="utf-8")
            meta = self._build_page_meta(md_path, content)
            results.append((meta, content))

        return results

    def _build_page_meta(self, md_path: Path, content: str) -> MimirPageMeta:
        """Build a MimirPageMeta from a path and its already-read content."""
        rel = self._wiki_relative_path(md_path)
        path_str = str(rel)
        parts = path_str.split("/")
        category = parts[0] if len(parts) > 1 else "uncategorised"
        stat = md_path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        source_ids = self._extract_source_ids(content)
        front = _parse_meta_frontmatter(content)
        return MimirPageMeta(
            path=path_str,
            title=_extract_title(content, path_str),
            summary=_extract_summary(content),
            category=category,
            updated_at=updated_at,
            source_ids=source_ids,
            page_type=front.get("page_type"),
            confidence=front.get("confidence"),
            entity_type=front.get("entity_type"),
            related_entities=front.get("related_entities", []),
        )

    @staticmethod
    def _extract_source_ids(content: str) -> list[str]:
        """Extract source IDs from a page's footer comment.

        Pages may embed source references as: ``<!-- sources: id1,id2 -->``
        """
        match = re.search(r"<!--\s*sources:\s*([^<]+?)\s*-->\s*$", content)
        if not match:
            return []
        return [s.strip() for s in match.group(1).split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_thread_md(title: str, created_at: datetime, context_ref_summary: str) -> str:
    """Return the initial Markdown content for a new thread."""
    date_str = created_at.strftime("%Y-%m-%d")
    history_line = f"- {date_str} — Opened"
    if context_ref_summary:
        history_line += f" from {context_ref_summary}"
    return (
        f"# {title}\n\n"
        "## Context\n\n"
        "Where this thread came from and why it matters.\n\n"
        "## What I know so far\n\n"
        "Accumulated findings — updated each work cycle.\n\n"
        "## Open questions\n\n"
        "The unresolved things. When empty, thread is probably closeable.\n\n"
        "## Next action\n\n"
        "One sentence. Updated by the agent after each work cycle.\n\n"
        "## History\n\n"
        f"{history_line}\n"
    )


def _fallback_title_from_path(path: str | None) -> str:
    """Derive a readable title from a page path when no explicit title exists."""
    if not path:
        return "Untitled"

    stem = Path(path).stem.strip()
    if not stem:
        return "Untitled"

    ticket_match = re.match(r"^([A-Z]+-\d+)[-_](.+)$", stem)
    if ticket_match:
        ticket_id, remainder = ticket_match.groups()
        remainder_title = re.sub(r"[-_]+", " ", remainder).strip().title()
        return f"{ticket_id} {remainder_title}".strip()

    return re.sub(r"[-_]+", " ", stem).strip().title() or "Untitled"


def _extract_title(content: str, path: str | None = None) -> str:
    """Return the best available page title from frontmatter, H1, or path."""
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
    if frontmatter_match:
        try:
            parsed = yaml.safe_load(frontmatter_match.group(1)) or {}
        except yaml.YAMLError:
            parsed = {}
        title = str(parsed.get("title", "")).strip()
        if title:
            return title

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return _fallback_title_from_path(path)


def _extract_summary(content: str) -> str:
    """Return the first non-heading, non-empty line from *content*."""
    heading_seen = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading_seen = True
            continue
        if heading_seen:
            return stripped[:120]
    return ""


def _chunk_markdown(
    content: str,
    page_path: str,
    category: str,
    page_type: str = "wiki",
    *,
    page_title: str | None = None,
    max_chars: int = _CHUNK_MAX_CHARS,
) -> list[tuple[str, dict[str, Any]]]:
    """Split markdown content into searchable chunks.

    Strategy:
    1. Split on ``## `` headings — each H2 section becomes a candidate chunk.
    2. Everything before the first ``## `` heading is the intro chunk.
    3. Sections exceeding *max_chars* are split further on ``### `` headings
       or blank-line-delimited paragraphs.

    Returns:
        List of ``(chunk_text, metadata)`` pairs where metadata contains
        ``page_path``, ``section_heading``, ``category``, and ``page_type``.
    """
    base_meta: dict[str, Any] = {
        "page_path": page_path,
        "category": category,
        "page_type": page_type,
    }
    resolved_title = page_title or _extract_title(content, page_path)
    search_prefix = f"# {resolved_title}\nPath: {page_path}\nCategory: {category}\n\n"

    # Split on H2 boundaries, keeping the heading with each section.
    h2_pattern = re.compile(r"(?=^## )", re.MULTILINE)
    raw_sections = h2_pattern.split(content)

    chunks: list[tuple[str, dict[str, Any]]] = []
    for section in raw_sections:
        if not section.strip():
            continue

        # Extract the section heading (first line starting with ##).
        first_line = section.splitlines()[0].strip() if section.strip() else ""
        heading = first_line.lstrip("#").strip() if first_line.startswith("#") else ""

        if len(section) <= max_chars:
            meta = {**base_meta, "section_heading": heading}
            chunks.append((f"{search_prefix}{section.strip()}", meta))
            continue

        # Section too large — try splitting on H3 headings first.
        h3_pattern = re.compile(r"(?=^### )", re.MULTILINE)
        sub_sections = h3_pattern.split(section)

        if len(sub_sections) > 1:
            for sub in sub_sections:
                if not sub.strip():
                    continue
                sub_first = sub.splitlines()[0].strip() if sub.strip() else ""
                sub_heading = (
                    sub_first.lstrip("#").strip() if sub_first.startswith("#") else heading
                )
                _append_paragraphs(sub, sub_heading, base_meta, max_chars, chunks)
        else:
            _append_paragraphs(section, heading, base_meta, max_chars, chunks)

    # Always return at least one chunk containing the full content.
    if not chunks:
        meta = {**base_meta, "section_heading": resolved_title}
        chunks.append((f"{search_prefix}{content.strip()}", meta))

    return chunks


def _append_paragraphs(
    text: str,
    heading: str,
    base_meta: dict[str, Any],
    max_chars: int,
    out: list[tuple[str, dict[str, Any]]],
) -> None:
    """Split *text* on blank lines and append paragraph-level chunks to *out*."""
    if len(text) <= max_chars:
        meta = {**base_meta, "section_heading": heading}
        out.append((text.strip(), meta))
        return

    paragraphs = re.split(r"\n{2,}", text)
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > max_chars and current_parts:
            meta = {**base_meta, "section_heading": heading}
            out.append(("\n\n".join(current_parts), meta))
            current_parts = [para]
            current_len = len(para)
        else:
            current_parts.append(para)
            current_len += len(para)

    if current_parts:
        meta = {**base_meta, "section_heading": heading}
        out.append(("\n\n".join(current_parts), meta))
