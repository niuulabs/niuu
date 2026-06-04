"""PostSessionReflectionService — write operational learnings after ravn.session.ended.

When a ``ravn.session.ended`` event arrives, this service:

1. Calls a cheap LLM with session metadata to extract a structured learning.
2. Searches Mímir for an existing ``learnings/`` page about the same pattern.
3. Updates the existing page (merging a new timeline entry + upgrading
   confidence when warranted) or creates a new one.

Confidence ladder
-----------------
- ``low``    — first observation (1 session)
- ``medium`` — second observation (2 sessions)
- ``high``   — reproduced 3 or more times

The service subscribes to ``ravn.session.ended`` via a
:class:`~sleipnir.ports.events.SleipnirSubscriber`.  Call :meth:`start` once
to register the subscription; call :meth:`stop` to cancel it.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from niuu.domain.mimir import MimirPage
from niuu.ports.mimir import MimirPort
from ravn.config import PostSessionReflectionConfig
from ravn.ports.llm import LLMPort

if TYPE_CHECKING:
    from sleipnir.domain.events import SleipnirEvent
    from sleipnir.ports.events import SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

_RAVN_SESSION_ENDED = "ravn.session.ended"

# Approximate chars-per-token ratio used for rough budget enforcement.
_CHARS_PER_TOKEN = 4

# Number of timeline entries that trigger each confidence level.
_CONFIDENCE_MEDIUM_THRESHOLD = 2
_CONFIDENCE_HIGH_THRESHOLD = 3

_REFLECTION_SYSTEM = (
    "You are an expert at extracting operational learnings from software engineering sessions. "
    "Respond only with valid JSON — no markdown fences, no commentary."
)

_REFLECTION_PROMPT = """\
A Ravn AI agent just completed a coding session. Analyse the session metadata \
below and extract ONE actionable learning that would help future sessions in \
the same repository avoid mistakes or work more efficiently.

Session metadata:
  persona:     {persona}
  outcome:     {outcome}
  token_count: {token_count}
  duration_s:  {duration_s}
  repo_slug:   {repo_slug}

Questions to consider:
1. Was the session unusually expensive or slow? (high token_count or duration)
2. Did the outcome suggest a failure or partial result? (outcome != success)
3. What project-specific quirk might a future session benefit from knowing?

Respond with a single JSON object:
{{
  "title":    "short title — max 80 chars",
  "learning": "concise statement of the operational learning — 1-3 sentences",
  "type":     "observation" or "decision",
  "tags":     ["tag1", "tag2"],
  "evidence": "one sentence describing what this session revealed"
}}

If the session was unremarkable and no useful learning can be extracted, \
respond with: null\
"""


class PostSessionReflectionService:
    """Service that writes Mímir learnings after each ``ravn.session.ended`` event.

    Args:
        subscriber:  Sleipnir subscriber used to register the event handler.
        mimir:       Mímir adapter for searching and writing learning pages.
        llm:         LLM adapter for the reflection call.
        config:      Service configuration (enabled flag, model alias, etc.).
    """

    def __init__(
        self,
        subscriber: SleipnirSubscriber,
        mimir: MimirPort,
        llm: LLMPort,
        config: PostSessionReflectionConfig,
    ) -> None:
        self._subscriber = subscriber
        self._mimir = mimir
        self._llm = llm
        self._config = config
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        """Subscribe to ``ravn.session.ended`` events."""
        if not self._config.enabled:
            logger.info("PostSessionReflectionService: disabled — not subscribing")
            return

        self._subscription = await self._subscriber.subscribe(
            [_RAVN_SESSION_ENDED],
            handler=self._on_session_ended,
        )
        logger.info("PostSessionReflectionService: subscribed to %s", _RAVN_SESSION_ENDED)

    async def stop(self) -> None:
        """Cancel the Sleipnir subscription."""
        if self._subscription is None:
            return
        try:
            await self._subscription.unsubscribe()
        except Exception as exc:
            logger.warning("PostSessionReflectionService: error unsubscribing: %s", exc)
        finally:
            self._subscription = None

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    async def _on_session_ended(self, event: SleipnirEvent) -> None:
        """Handle a ``ravn.session.ended`` event — best-effort, never raises."""
        try:
            await self._process(event.payload)
        except Exception as exc:
            logger.warning(
                "PostSessionReflectionService: unhandled error processing event: %s", exc
            )

    async def _process(self, payload: dict) -> None:
        """Extract a learning from *payload* and write it to Mímir."""
        session_id = payload.get("session_id", "unknown")
        logger.info("PostSessionReflectionService: reflecting on session %s", session_id)

        learning = await self._run_reflection(payload)
        if learning is None:
            logger.info(
                "PostSessionReflectionService: no learning extracted for session %s",
                session_id,
            )
            return

        await self._write_learning(learning, payload)

    # ------------------------------------------------------------------
    # LLM reflection
    # ------------------------------------------------------------------

    async def _run_reflection(self, payload: dict) -> dict | None:
        """Call the LLM and parse the structured learning JSON."""
        prompt = _REFLECTION_PROMPT.format(
            persona=payload.get("persona", ""),
            outcome=payload.get("outcome", ""),
            token_count=payload.get("token_count", 0),
            duration_s=payload.get("duration_s", 0.0),
            repo_slug=payload.get("repo_slug", ""),
        )

        try:
            response = await self._llm.generate(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=_REFLECTION_SYSTEM,
                model=self._config.llm_alias,
                max_tokens=self._config.max_tokens,
            )
        except Exception as exc:
            logger.warning("PostSessionReflectionService: LLM call failed: %s", exc)
            return None

        raw = response.content.strip()
        found_json, parsed = _parse_reflection_json(raw)
        if not found_json:
            logger.warning("PostSessionReflectionService: malformed JSON from LLM")
            return None
        if parsed is None:
            return None

        if not isinstance(parsed, dict):
            logger.warning(
                "PostSessionReflectionService: LLM returned non-object JSON: %s",
                type(parsed).__name__,
            )
            return None

        return parsed

    # ------------------------------------------------------------------
    # Mímir write
    # ------------------------------------------------------------------

    async def _write_learning(self, learning: dict, payload: dict) -> None:
        """Search for an existing page and update it, or create a new one."""
        title = learning.get("title", "").strip()
        if not title:
            logger.warning("PostSessionReflectionService: LLM returned learning without title")
            return

        repo_slug = payload.get("repo_slug", "")
        session_id = payload.get("session_id", "unknown")
        now = datetime.now(UTC)

        existing_page = await self._find_existing_page(title, repo_slug)

        if existing_page is not None:
            updated = _merge_timeline_entry(
                existing_page.content,
                session_id=session_id,
                evidence=learning.get("evidence", ""),
                date=now,
            )
            try:
                await self._mimir.upsert_page(existing_page.meta.path, updated)
                logger.info(
                    "PostSessionReflectionService: updated learning page %r (session=%s)",
                    existing_page.meta.path,
                    session_id,
                )
            except Exception as exc:
                logger.warning(
                    "PostSessionReflectionService: failed to update page %r: %s",
                    existing_page.meta.path,
                    exc,
                )
            return

        page_path = _build_page_path(title, repo_slug)
        content = _build_page_content(
            title=title,
            learning=learning.get("learning", ""),
            page_type=learning.get("type", "observation"),
            tags=learning.get("tags", []),
            evidence=learning.get("evidence", ""),
            repo_slug=repo_slug,
            session_id=session_id,
            date=now,
        )

        try:
            await self._mimir.upsert_page(page_path, content)
            logger.info(
                "PostSessionReflectionService: created learning page %r (session=%s)",
                page_path,
                session_id,
            )
        except Exception as exc:
            logger.warning(
                "PostSessionReflectionService: failed to create page %r: %s",
                page_path,
                exc,
            )

    async def _find_existing_page(self, title: str, repo_slug: str) -> MimirPage | None:
        """Search Mímir for an existing learning page matching *title*.

        Returns the first :class:`~niuu.domain.mimir.MimirPage` whose title
        closely matches, or ``None``.
        """
        keywords = _title_to_keywords(title)
        if not keywords:
            return None

        try:
            results = await self._mimir.search(keywords)
        except Exception as exc:
            logger.warning("PostSessionReflectionService: Mímir search failed: %s", exc)
            return None

        for page in results:
            if page.meta.category != "learnings":
                continue
            if _titles_similar(page.meta.title or "", title):
                return page

        return None


def _parse_reflection_json(raw: str) -> tuple[bool, object | None]:
    """Parse the reflection model's JSON object or null from common wrappers."""
    text = raw.strip()
    if not text:
        return False, None

    for candidate in _reflection_json_candidates(text):
        candidate = candidate.strip()
        if not candidate:
            continue
        if candidate.lower() == "null":
            return True, None
        try:
            return True, json.loads(candidate)
        except json.JSONDecodeError:
            found, parsed = _raw_decode_embedded_json(candidate)
            if found:
                return True, parsed

    return False, None


def _reflection_json_candidates(text: str) -> list[str]:
    """Return likely JSON snippets, prioritizing fenced blocks over full text."""
    candidates: list[str] = []
    fence_pattern = re.compile(r"```(?:json|JSON)?\s*(.*?)```", flags=re.DOTALL)
    candidates.extend(match.group(1) for match in fence_pattern.finditer(text))
    candidates.append(text)
    return candidates


def _raw_decode_embedded_json(text: str) -> tuple[bool, object | None]:
    """Decode the first JSON value embedded in prose, if one exists."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[n":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return True, parsed
    return False, None


# ---------------------------------------------------------------------------
# Learnings injection helper (used by agent at session start)
# ---------------------------------------------------------------------------


async def fetch_relevant_learnings(
    mimir: MimirPort,
    *,
    repo_slug: str,
    max_pages: int,
    token_budget: int,
    environment_id: str = "",
    domain: str = "",
    flock_id: str = "",
) -> str:
    """Query Mímir for learning pages matching *repo_slug* and format for injection.

    Returns a formatted Markdown block ready for inclusion in the system
    prompt, capped at approximately *token_budget* tokens.  Returns an empty
    string when no learnings are found or on any error.
    """
    try:
        pages = await mimir.list_pages(category="learnings")
    except Exception as exc:
        logger.warning("fetch_relevant_learnings: list_pages failed: %s", exc)
        return ""

    if not pages:
        return ""

    prefixes = _learning_injection_prefixes(
        repo_slug=repo_slug,
        environment_id=environment_id,
        domain=domain,
        flock_id=flock_id,
    )
    relevant = [p for p in pages if any(p.path.startswith(prefix) for prefix in prefixes)]

    _epoch = datetime(1970, 1, 1, tzinfo=UTC)
    # Sort by recency (most recently updated first).
    relevant.sort(key=lambda p: p.updated_at or _epoch, reverse=True)

    selected = relevant[:max_pages]
    if not selected:
        return ""

    # Read full content for each selected page (best-effort).
    lines: list[str] = ["## Relevant Past Learnings\n"]
    char_budget = token_budget * _CHARS_PER_TOKEN

    for meta in selected:
        try:
            content = await mimir.read_page(meta.path)
        except Exception:
            continue

        # Strip YAML frontmatter for injection; keep markdown body only.
        body = _strip_frontmatter(content).strip()
        if not body:
            continue

        entry = f"### {meta.title or meta.path}\n{body}\n"
        if len("\n".join(lines)) + len(entry) > char_budget:
            break
        lines.append(entry)

    if len(lines) <= 1:
        return ""

    return "\n".join(lines)


def _learning_injection_prefixes(
    *,
    repo_slug: str = "",
    environment_id: str = "",
    domain: str = "",
    flock_id: str = "",
) -> list[str]:
    """Return ordered learning prefixes for local and promoted knowledge."""
    prefixes: list[str] = []
    if repo_slug:
        safe_repo = re.sub(r"[^a-z0-9_-]", "-", repo_slug.lower())
        prefixes.append(f"learnings/{safe_repo}/")
    if environment_id:
        prefixes.append(f"learnings/environment/{_scope_slug(environment_id)}/")
    if flock_id:
        prefixes.append(f"learnings/flock/{_scope_slug(flock_id)}/")
    if domain:
        prefixes.extend(
            [
                f"learnings/domain/{_scope_slug(domain)}/",
                f"learnings/flock/{_scope_slug(f'flock:{domain}')}/",
            ]
        )
    prefixes.extend(["learnings/shared/", "learnings/general/"])
    return list(dict.fromkeys(prefixes))


# ---------------------------------------------------------------------------
# Page content helpers
# ---------------------------------------------------------------------------


def _build_page_path(title: str, repo_slug: str) -> str:
    """Build a ``learnings/`` wiki path from *title* and *repo_slug*."""
    slug = _slugify(title)
    if repo_slug:
        safe_repo = re.sub(r"[^a-z0-9_-]", "-", repo_slug.lower())
        return f"learnings/{safe_repo}/{slug}.md"
    return f"learnings/general/{slug}.md"


def _build_page_content(
    *,
    title: str,
    learning: str,
    page_type: str,
    tags: list[str],
    evidence: str,
    repo_slug: str,
    session_id: str,
    date: datetime,
) -> str:
    """Render the full page Markdown with YAML frontmatter."""
    tags_yaml = ", ".join(f'"{t}"' for t in tags) if tags else ""
    date_str = date.strftime("%Y-%m-%dT%H:%M:%SZ")
    repo_tag = f'"{repo_slug}"' if repo_slug else ""

    frontmatter_lines = [
        "---",
        f'title: "Learning: {title}"',
        f"type: {page_type}",
        "category: learnings",
        "confidence: low",
    ]
    if repo_slug:
        frontmatter_lines.append(f"repo_slug: {repo_slug}")
    if tags_yaml:
        frontmatter_lines.append(f"tags: [{tags_yaml}]")
    if repo_tag:
        frontmatter_lines.append(f"repo_tags: [{repo_tag}]")
    frontmatter_lines += [
        "timeline:",
        "  - source: ravn_reflection",
        f"    session_id: {session_id}",
        f"    date: {date_str}",
        f'    note: "{_escape_yaml(evidence)}"',
        "---",
    ]

    body_lines = [
        f"# Learning: {title}",
        "",
        "## What was learned",
        learning,
        "",
        "## Evidence",
        f"Session `{session_id}` ({date.strftime('%Y-%m-%d')}): {evidence}",
        "",
        "## Confidence Rationale",
        (
            "Low confidence — observed once. Upgraded to medium after 2 sessions, "
            "high after 3 or more."
        ),
    ]

    return "\n".join(frontmatter_lines) + "\n\n" + "\n".join(body_lines) + "\n"


def _merge_timeline_entry(
    existing_content: str,
    *,
    session_id: str,
    evidence: str,
    date: datetime,
) -> str:
    """Append a new timeline entry and upgrade confidence if threshold is met.

    Parses the YAML ``timeline`` list from the frontmatter, appends the new
    entry, recalculates ``confidence``, and returns the updated page content.
    Uses string manipulation to avoid a full YAML round-trip.
    """
    date_str = date.strftime("%Y-%m-%dT%H:%M:%SZ")
    new_entry = (
        f"  - source: ravn_reflection\n"
        f"    session_id: {session_id}\n"
        f"    date: {date_str}\n"
        f'    note: "{_escape_yaml(evidence)}"'
    )

    # Count existing timeline entries to determine new confidence.
    existing_count = existing_content.count("  - source: ravn_reflection")
    new_count = existing_count + 1

    if new_count >= _CONFIDENCE_HIGH_THRESHOLD:
        new_confidence = "high"
    elif new_count >= _CONFIDENCE_MEDIUM_THRESHOLD:
        new_confidence = "medium"
    else:
        new_confidence = "low"

    # Append timeline entry before the closing "---" of the frontmatter.
    # Strategy: insert after the last existing timeline entry line.
    updated = _insert_timeline_entry(existing_content, new_entry)

    # Update confidence field.
    updated = re.sub(
        r"^confidence:\s+\w+",
        f"confidence: {new_confidence}",
        updated,
        flags=re.MULTILINE,
    )

    return updated


def _insert_timeline_entry(content: str, new_entry: str) -> str:
    """Insert *new_entry* after the last ``source: ravn_reflection`` block."""
    # Find the CLOSING frontmatter "---" delimiter (the second occurrence).
    # re.search would match the opening "---" at position 0, so we collect
    # all matches and use the second one.
    matches = list(re.finditer(r"^---\s*$", content, flags=re.MULTILINE))
    if len(matches) < 2:
        # No closing delimiter; append at end of file.
        return content.rstrip() + "\n" + new_entry + "\n"

    # Find the last "note:" line inside the frontmatter.
    fm_end = matches[1].start()
    frontmatter = content[:fm_end]
    rest = content[fm_end:]

    last_note = list(re.finditer(r'    note: ".*"', frontmatter))
    if last_note:
        insert_pos = last_note[-1].end()
        return frontmatter[:insert_pos] + "\n" + new_entry + frontmatter[insert_pos:] + rest

    # No existing entries; insert before the closing "---".
    return frontmatter + new_entry + "\n" + rest


def _title_to_keywords(title: str) -> str:
    """Extract meaningful search keywords from *title*."""
    # Remove common stop words and short tokens.
    stop = {"a", "an", "the", "and", "or", "for", "in", "on", "at", "to", "of", "is"}
    words = re.findall(r"\b\w{3,}\b", title.lower())
    keywords = [w for w in words if w not in stop]
    return " ".join(keywords[:6])


def _titles_similar(a: str, b: str) -> bool:
    """Return True when *a* and *b* share enough significant words to be duplicates."""

    def significant_words(t: str) -> set[str]:
        stop = {
            "a",
            "an",
            "the",
            "and",
            "or",
            "for",
            "in",
            "on",
            "at",
            "to",
            "of",
            "is",
            "learning",
        }
        return {w for w in re.findall(r"\b\w{3,}\b", t.lower()) if w not in stop}

    words_a = significant_words(a)
    words_b = significant_words(b)
    if not words_a or not words_b:
        return False

    overlap = words_a & words_b
    # Jaccard similarity >= 0.5 counts as similar.
    union = words_a | words_b
    return len(overlap) / len(union) >= 0.5


def _slugify(text: str) -> str:
    """Convert *text* to a URL-safe slug."""
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    slug = slug.strip("-")
    return slug[:60] or "learning"


def _scope_slug(text: str) -> str:
    """Convert Environment/Flock/domain identifiers to promoted-learning path slugs."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "general"


def _escape_yaml(text: str) -> str:
    """Escape double quotes in *text* for inline YAML string embedding."""
    return text.replace('"', '\\"')


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter block (``---…---``) from *content*.

    Note: a functionally equivalent copy lives in ``mimir.compiled_truth``.
    When ``mimir`` is extracted to ``niuu``, consolidate both into a shared
    ``niuu.utils.frontmatter`` utility.
    """
    if not content.startswith("---"):
        return content
    # Find closing delimiter.
    rest = content[3:]
    end = rest.find("\n---")
    if end == -1:
        return content
    return rest[end + 4 :]
