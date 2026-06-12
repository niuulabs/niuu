"""Retrieval Reflex — deterministic Mímir entity pointer injection (NIU-1059).

A zero-LLM layer that scans incoming agent-turn messages for known Mímir
entities and injects compact pointers (never page bodies) so the agent knows
the knowledge base already covers a topic before re-deriving it.

Entity feed contract (owned by the Mímir stream — do not modify ``src/mimir``)::

    GET {mimir_base_url}/mimir/entities ->
        [{"slug": str, "title": str, "type": str|null, "confidence": str|null,
          "updated_at": iso8601, "path": str}, ...]

Components
----------
- :class:`EntityIndex` — normalized slug/title lookup built from the feed.
- :func:`scan_message` — pure function: capitalized n-grams + @handles → matches.
- :func:`format_pointer_block` — compact pointer text, one line per entity.
- :class:`EntityIndexCache` — TTL-cached index refresh around an async fetcher.
- :class:`ReflexInjector` — ties it together with per-session slug dedupe and
  the ``mimir.reflex.injected`` structured log counter. Fail-open: an
  unreachable feed logs an ERROR and skips injection — it never crashes a turn.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# ISO-8601 timestamps carry the date in the first 10 characters (YYYY-MM-DD).
_ISO_DATE_LENGTH = 10

_HANDLE_RE = re.compile(r"@([A-Za-z0-9][\w\-./]*)")
_WORD_RE = re.compile(r"[A-Za-z0-9][\w'’\-]*")

# Capitalized n-grams are capped at 3 words by the NIU-1059 contract.
_MAX_NGRAM_WORDS = 3

POINTER_BLOCK_HEADER = "Mimir knows (pointers only — use mimir_read for details):"

INJECTED_LOG_EVENT = "mimir.reflex.injected"


@dataclass(frozen=True, slots=True)
class ReflexEntity:
    """One entity row from the Mímir entity feed."""

    slug: str
    title: str
    type: str | None
    confidence: str | None
    updated_at: str
    path: str


def parse_entities(payload: object) -> list[ReflexEntity]:
    """Parse the ``GET /mimir/entities`` JSON payload into entities.

    Rows that are not objects or lack both ``slug`` and ``title`` are skipped —
    a malformed row must not take down the whole feed.
    """
    if not isinstance(payload, list):
        raise ValueError(f"entity feed payload must be a list, got {type(payload).__name__}")
    entities: list[ReflexEntity] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip()
        title = str(row.get("title") or "").strip()
        if not slug and not title:
            continue
        entities.append(
            ReflexEntity(
                slug=slug or title,
                title=title or slug,
                type=row.get("type") or None,
                confidence=row.get("confidence") or None,
                updated_at=str(row.get("updated_at") or ""),
                path=str(row.get("path") or ""),
            )
        )
    return entities


def _normalize(value: str) -> str:
    """Casefold and make hyphen/underscore/space-insensitive."""
    folded = re.sub(r"[-_/]+", " ", value.casefold())
    folded = re.sub(r"[^\w\s]", "", folded)
    return " ".join(folded.split())


class EntityIndex:
    """Normalized title/slug lookup over the entity feed.

    Both the slug (``volundr-auth``) and the title (``Volundr Auth``) of each
    entity resolve to the same entry; lookups are casefolded and
    hyphen/space-insensitive. First entity wins on key collisions.
    """

    def __init__(self, entities: Iterable[ReflexEntity]) -> None:
        self._by_key: dict[str, ReflexEntity] = {}
        for entity in entities:
            for raw_key in (entity.slug, entity.title):
                key = _normalize(raw_key)
                if not key:
                    continue
                self._by_key.setdefault(key, entity)

    def lookup(self, term: str) -> ReflexEntity | None:
        """Return the entity for *term*, or None when unknown."""
        key = _normalize(term)
        if not key:
            return None
        return self._by_key.get(key)

    def __len__(self) -> int:
        return len(self._by_key)


def _candidate_terms(text: str) -> Iterator[str]:
    """Yield candidate entity mentions in document order.

    Candidates are @handles and capitalized n-grams (1–3 words, longest
    first at each position so multi-word entities win over their prefixes).
    """
    candidates: list[tuple[int, int, str]] = []
    for match in _HANDLE_RE.finditer(text):
        candidates.append((match.start(), 0, match.group(1)))

    words = list(_WORD_RE.finditer(text))
    for i, word in enumerate(words):
        if not word.group()[0].isupper():
            continue
        for length in range(_MAX_NGRAM_WORDS, 0, -1):
            window = words[i : i + length]
            if len(window) < length:
                continue
            if any(not w.group()[0].isupper() for w in window):
                continue
            term = " ".join(w.group() for w in window)
            candidates.append((word.start(), _MAX_NGRAM_WORDS - length + 1, term))

    candidates.sort()
    for _, _, term in candidates:
        yield term


def scan_message(
    text: str,
    index: EntityIndex,
    max_pointers: int,
    *,
    seen_slugs: set[str] | None = None,
) -> list[ReflexEntity]:
    """Find known Mímir entities mentioned in *text*.

    Pure and deterministic: no LLM, no I/O. Scans capitalized n-grams
    (1–3 words) and ``@handles`` against *index*, skips slugs in *seen_slugs*,
    dedupes by slug, and returns at most *max_pointers* matches in document
    order.
    """
    if not text or max_pointers <= 0 or len(index) == 0:
        return []
    seen = set(seen_slugs) if seen_slugs else set()
    matches: list[ReflexEntity] = []
    for term in _candidate_terms(text):
        entity = index.lookup(term)
        if entity is None or entity.slug in seen:
            continue
        seen.add(entity.slug)
        matches.append(entity)
        if len(matches) >= max_pointers:
            return matches
    return matches


def _pointer_line(entity: ReflexEntity) -> str:
    details: list[str] = []
    if entity.type:
        details.append(entity.type)
    if entity.confidence:
        details.append(f"{entity.confidence} confidence")
    if entity.updated_at:
        details.append(f"updated {entity.updated_at[:_ISO_DATE_LENGTH]}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"[[{entity.slug}]]{suffix} — mimir_read {entity.path}"


def format_pointer_block(matches: list[ReflexEntity]) -> str:
    """Render compact entity pointers: a short header plus one line per entity.

    Never includes page bodies — pointers only.
    """
    if not matches:
        return ""
    lines = [POINTER_BLOCK_HEADER]
    lines.extend(_pointer_line(entity) for entity in matches)
    return "\n".join(lines)


class EntityIndexCache:
    """TTL cache around an async entity fetcher.

    ``get()`` returns the cached :class:`EntityIndex` while fresh and refreshes
    it once the TTL elapses. Fetch errors propagate to the caller — the
    injector is responsible for the fail-open behaviour.
    """

    def __init__(
        self,
        fetch: Callable[[], Awaitable[list[ReflexEntity]]],
        ttl_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._index: EntityIndex | None = None
        self._fetched_at: float = 0.0

    async def get(self) -> EntityIndex:
        """Return a fresh index, refetching when the TTL has expired."""
        now = self._clock()
        if self._index is not None and now - self._fetched_at < self._ttl_seconds:
            return self._index
        entities = await self._fetch()
        self._index = EntityIndex(entities)
        self._fetched_at = self._clock()
        return self._index


def http_entity_fetcher(
    base_url: str,
    timeout_seconds: float,
) -> Callable[[], Awaitable[list[ReflexEntity]]]:
    """Build an async fetcher for ``GET {base_url}/mimir/entities``."""
    url = f"{base_url.rstrip('/')}/mimir/entities"

    async def _fetch() -> list[ReflexEntity]:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            return parse_entities(response.json())

    return _fetch


class ReflexInjector:
    """Per-process retrieval reflex: scan a turn, return a pointer block.

    Keeps a per-session seen-set so a pointer for the same slug is injected at
    most once per session, and logs a ``mimir.reflex.injected`` structured line
    with the injected slugs so follow-up rates can be measured.
    """

    def __init__(
        self,
        *,
        fetch_entities: Callable[[], Awaitable[list[ReflexEntity]]],
        max_pointers: int,
        cache_ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache = EntityIndexCache(fetch_entities, cache_ttl_seconds, clock=clock)
        self._max_pointers = max_pointers
        self._seen_by_session: dict[str, set[str]] = {}

    async def pointer_block(self, text: str, session_id: str) -> str | None:
        """Return the pointer block for *text*, or None when nothing applies.

        Fail-open: when the entity feed is unreachable this logs an ERROR and
        returns None — the turn proceeds without injection, never crashes.
        """
        try:
            index = await self._cache.get()
        except Exception:
            logger.error(
                "mimir.reflex: entity feed unreachable — skipping pointer injection for session %s",
                session_id,
                exc_info=True,
            )
            return None
        seen = self._seen_by_session.setdefault(session_id, set())
        matches = scan_message(text, index, self._max_pointers, seen_slugs=seen)
        if not matches:
            return None
        slugs = [entity.slug for entity in matches]
        seen.update(slugs)
        logger.info("%s session=%s slugs=%s", INJECTED_LOG_EVENT, session_id, slugs)
        return format_pointer_block(matches)

    async def apply(self, text: str, session_id: str) -> str:
        """Return *text* with the pointer block prefixed, or unchanged."""
        block = await self.pointer_block(text, session_id)
        if block is None:
            return text
        return f"{block}\n\n{text}"


def _resolve_base_url(host_cfg: object, reflex_cfg: object) -> str:
    """Resolve the Mímir base URL from the reflex config or HTTP instances."""
    explicit = getattr(reflex_cfg, "base_url", "")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    instances = getattr(host_cfg, "instances", None)
    if not isinstance(instances, (list, tuple)):
        return ""
    for instance in instances:
        url = getattr(instance, "url", None)
        if isinstance(url, str) and url.strip():
            return url.strip()
    return ""


def build_reflex_injector(host_cfg: object) -> ReflexInjector | None:
    """Build a :class:`ReflexInjector` from a config object holding ``.reflex``.

    *host_cfg* is Ravn's ``MimirConfig`` (which may also carry HTTP
    ``instances`` used as a base-URL fallback) or Skuld's ``SkuldSettings``.
    Returns None when the reflex is disabled. When enabled but no base URL can
    be resolved, logs an ERROR and returns None (config error must be loud).
    """
    reflex_cfg = getattr(host_cfg, "reflex", None)
    if reflex_cfg is None or getattr(reflex_cfg, "enabled", False) is not True:
        return None
    base_url = _resolve_base_url(host_cfg, reflex_cfg)
    if not base_url:
        logger.error(
            "mimir.reflex: enabled but no base_url configured and no HTTP Mimir "
            "instance found — retrieval reflex disabled"
        )
        return None
    return ReflexInjector(
        fetch_entities=http_entity_fetcher(base_url, float(reflex_cfg.timeout_seconds)),
        max_pointers=int(reflex_cfg.max_pointers),
        cache_ttl_seconds=float(reflex_cfg.cache_ttl_seconds),
    )
