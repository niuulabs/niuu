"""Post-session reflection candidates and evidence-gated learning promotion.

When a ``ravn.session.ended`` event arrives, this service:

1. Calls a cheap LLM with session metadata to extract a reflection candidate.
2. Stores it under ``learning-candidates/`` where normal learning retrieval
   cannot see it.
3. Promotes it to ``learnings/`` only after repeated independent observations
   or stronger, explicit evidence supplied by feedback/review/verification.

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
_RAVN_TASK_COMPLETED = "ravn.task.completed"
_FEEDBACK_RECORDED = "feedback.recorded"
_ODIN_REVIEW_DECIDED = "odin.review.decided"
_POSITIVE_FEEDBACK = frozenset({"useful", "good_action", "draft_accepted"})

# Approximate chars-per-token ratio used for rough budget enforcement.
_CHARS_PER_TOKEN = 4
_REFLECTION_CONTEXT_MAX_CHARS = 12_000

# Number of timeline entries that trigger each confidence level.
_CONFIDENCE_MEDIUM_THRESHOLD = 2
_CONFIDENCE_HIGH_THRESHOLD = 3

# Titles are compared by Jaccard; claims and evidence notes by overlap
# coefficient. Jaccard punishes a paraphrase for the words it did not reuse, so
# two wordings of one belief scored 0.33-0.42 — below any threshold that also
# kept genuinely different learnings apart. Overlap coefficient asks the
# question that actually matters ("is the shorter claim contained in the
# longer?") and separated cleanly on the 22 real duplicates this was calibrated
# against: duplicates >= 0.56, unrelated claims <= 0.40.
_TITLE_DUPLICATE_SIMILARITY = 0.5
_CLAIM_DUPLICATE_OVERLAP = 0.5
_EVIDENCE_DUPLICATE_OVERLAP = 0.5
# Below this many significant words the overlap coefficient is trivially high
# (a two-word claim is "contained" in almost anything), so require near-identity
# instead.
_MIN_OVERLAP_WORDS = 5

_REFLECTION_SYSTEM = (
    "You extract possible operational learning candidates from AI agent sessions. "
    "Respond only with valid JSON or the literal null. No markdown fences, no commentary."
)

_REFLECTION_PROMPT = """\
A Ravn AI agent just completed a session. Analyse the record below and extract \
ONE possible actionable learning candidate that might help this agent make a \
better future judgment in the same kind of environment.

Session metadata:
  persona:     {persona}
  outcome:     {outcome}
  token_count: {token_count}
  duration_s:  {duration_s}
{repo_line}

Recorded work context:
{work_context}

Questions to consider:
{questions}

Respond with a single JSON object:
{{
  "title":    "short title — max 80 chars",
  "learning": "concise statement of the operational learning — 1-3 sentences",
  "type":     "observation" or "decision",
  "tags":     ["tag1", "tag2"],
  "evidence": "one sentence describing what this session revealed"
}}

A learning must concern the work or its subject matter. Token counts, duration, \
outcome bookkeeping, missing fields, and other session mechanics are not \
learnings. Do not infer facts absent from the record. If the record contains \
insufficient evidence for a useful learning, respond with exactly: null

Do not explain why there is no learning. Do not wrap the response in markdown.\
"""

_REPO_QUESTIONS = """\
1. What concrete project behavior, constraint, or successful approach is supported by the record?
2. Would remembering it change a future decision in this repository?
3. Is the evidence specific enough to avoid turning a one-off event into a general rule?
"""

_GENERIC_QUESTIONS = """\
1. What did the session reveal about the environment or subject the agent works in?
2. Did an approach succeed or fail in a way worth remembering next time?
3. Is the evidence specific enough to avoid turning a one-off event into a general rule?
"""


class PostSessionReflectionService:
    """Store reflections as candidates and promote only evidence-backed ones.

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
            [
                _RAVN_SESSION_ENDED,
                _RAVN_TASK_COMPLETED,
                _FEEDBACK_RECORDED,
                _ODIN_REVIEW_DECIDED,
            ],
            handler=self._on_event,
        )
        logger.info("PostSessionReflectionService: subscribed to records and promotion evidence")

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

    async def _on_event(self, event: SleipnirEvent) -> None:
        if event.event_type == _RAVN_SESSION_ENDED:
            await self._on_session_ended(event)
            return
        try:
            await self._record_promotion_evidence(event)
        except Exception as exc:
            logger.warning(
                "PostSessionReflectionService: failed to record promotion evidence: %s",
                exc,
            )

    async def _on_session_ended(self, event: SleipnirEvent) -> None:
        """Handle a ``ravn.session.ended`` event — best-effort, never raises."""
        try:
            await self._process(event.payload)
        except Exception as exc:
            logger.warning(
                "PostSessionReflectionService: unhandled error processing event: %s", exc
            )

    async def _record_promotion_evidence(self, event: SleipnirEvent) -> None:
        """Apply explicit feedback, verification, or review to one candidate."""
        candidate_path = _candidate_path_from_payload(event.payload)
        if not candidate_path:
            return

        evidence_payload: dict[str, object]
        source: str
        if event.event_type == _FEEDBACK_RECORDED:
            feedback_type = str(event.payload.get("feedback_type") or "").casefold()
            if feedback_type not in _POSITIVE_FEEDBACK:
                return
            source = "external_feedback"
            evidence_payload = {"external_feedback_refs": [event.event_id]}
        elif event.event_type == _ODIN_REVIEW_DECIDED:
            decision = str(event.payload.get("decision") or "").casefold()
            requested_action = str(event.payload.get("requested_action") or "").casefold()
            if decision != "approved" or requested_action != "promote":
                return
            source = "reviewed_promotion"
            evidence_payload = {
                "reviewed_promotion": {"decision": "approved"},
            }
        elif event.event_type == _RAVN_TASK_COMPLETED:
            refs = event.payload.get("verification_refs")
            if event.payload.get("outcome_verified") is not True or not isinstance(refs, list):
                return
            source = "verified_outcome"
            evidence_payload = {
                "outcome_verified": True,
                "verification_refs": refs,
            }
        else:
            return

        content = await self._mimir.read_page(candidate_path)
        if not isinstance(content, str) or "category: learning-candidates" not in content:
            return
        updated = _append_candidate_evidence(
            content,
            source=source,
            event_id=event.event_id,
            note=str(event.payload.get("notes") or event.summary or source),
            date=event.timestamp,
        )
        title = _candidate_title(updated)
        if not title:
            return
        await self._persist_candidate_and_maybe_promote(
            candidate_path=candidate_path,
            candidate_content=updated,
            title=title,
            repo_slug="",
            payload=evidence_payload,
            learning_path_override=candidate_path.replace(
                "learning-candidates/",
                "learnings/",
                1,
            ),
        )

    async def _process(self, payload: dict) -> None:
        """Extract a reflection candidate from *payload* and record its evidence."""
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
        repo_slug = str(payload.get("repo_slug") or "").strip()
        work_context = {
            key: payload[key]
            for key in ("structured_outcome", "outcome_event_type")
            if payload.get(key) not in (None, "", {}, [])
        }
        rendered_context = (
            json.dumps(work_context, indent=2, sort_keys=True, default=str)
            if work_context
            else "(no subject-matter context was recorded)"
        )
        if len(rendered_context) > _REFLECTION_CONTEXT_MAX_CHARS:
            rendered_context = (
                rendered_context[:_REFLECTION_CONTEXT_MAX_CHARS].rstrip()
                + "\n… (recorded context truncated)"
            )
        prompt = _REFLECTION_PROMPT.format(
            persona=payload.get("persona", ""),
            outcome=payload.get("outcome", ""),
            token_count=payload.get("token_count", 0),
            duration_s=payload.get("duration_s", 0.0),
            repo_line=f"  repo_slug:   {repo_slug}" if repo_slug else "",
            work_context=rendered_context,
            questions=_REPO_QUESTIONS if repo_slug else _GENERIC_QUESTIONS,
        )

        attempts = 2
        for attempt in range(attempts):
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
            if not raw:
                if attempt + 1 < attempts:
                    logger.info("PostSessionReflectionService: empty LLM response; retrying")
                    continue
                return None

            found_json, parsed = _parse_reflection_json(raw)
            if found_json:
                break

            if attempt + 1 < attempts:
                logger.info(
                    "PostSessionReflectionService: malformed JSON from LLM; retrying excerpt=%r",
                    _compact_log_excerpt(raw),
                )
                continue

            logger.warning(
                "PostSessionReflectionService: malformed JSON from LLM excerpt=%r",
                _compact_log_excerpt(raw),
            )
            return None
        else:
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
        """Record a candidate and promote it only when evidence qualifies."""
        title = learning.get("title", "").strip()
        if not title:
            logger.warning("PostSessionReflectionService: LLM returned learning without title")
            return

        repo_slug = payload.get("repo_slug", "")
        session_id = payload.get("session_id", "unknown")
        now = datetime.now(UTC)

        claim = str(learning.get("learning", "") or "").strip()
        existing_page = await self._find_existing_page(title, repo_slug, claim)

        if existing_page is not None:
            if existing_page.meta.category == "learnings":
                logger.info(
                    "PostSessionReflectionService: trusted learning %r already exists; "
                    "reflection %s cannot mutate it",
                    existing_page.meta.path,
                    session_id,
                )
                return
            updated = _merge_timeline_entry(
                existing_page.content,
                session_id=session_id,
                evidence=learning.get("evidence", ""),
                date=now,
            )
            await self._persist_candidate_and_maybe_promote(
                candidate_path=existing_page.meta.path,
                candidate_content=updated,
                title=title,
                repo_slug=repo_slug,
                payload=payload,
            )
            return

        page_path = _build_candidate_path(title, repo_slug)
        content = _build_candidate_content(
            title=title,
            learning=learning.get("learning", ""),
            page_type=learning.get("type", "observation"),
            tags=learning.get("tags", []),
            evidence=learning.get("evidence", ""),
            repo_slug=repo_slug,
            session_id=session_id,
            date=now,
        )

        await self._persist_candidate_and_maybe_promote(
            candidate_path=page_path,
            candidate_content=content,
            title=title,
            repo_slug=repo_slug,
            payload=payload,
        )

    async def _persist_candidate_and_maybe_promote(
        self,
        *,
        candidate_path: str,
        candidate_content: str,
        title: str,
        repo_slug: str,
        payload: dict,
        learning_path_override: str = "",
    ) -> None:
        """Persist evidence first, then materialize a trusted learning if qualified."""
        try:
            await self._mimir.upsert_page(candidate_path, candidate_content)
        except Exception as exc:
            logger.warning(
                "PostSessionReflectionService: failed to write candidate %r: %s",
                candidate_path,
                exc,
            )
            return

        evidence_count = len(_timeline_session_ids(candidate_content))
        reason = _promotion_reason(
            payload,
            evidence_count=evidence_count,
            min_repetitions=self._config.candidate_min_repetitions,
        )
        if not reason:
            logger.info(
                "PostSessionReflectionService: recorded candidate %r (%d/%d observations)",
                candidate_path,
                evidence_count,
                self._config.candidate_min_repetitions,
            )
            return

        # A candidate promotes to ONE learning page for its whole life. The path
        # used to be re-derived from each reflection's title, so a candidate that
        # kept gathering evidence minted a fresh learnings/ file every time the
        # model reworded its own title — one belief ended up occupying 22 pages,
        # each citing the same sessions.
        learning_path = (
            learning_path_override
            or _promoted_learning_path(candidate_content)
            or _build_page_path(title, repo_slug)
        )
        promoted_content = _promote_candidate_content(
            candidate_content,
            candidate_path=candidate_path,
            promotion_reason=reason,
        )
        try:
            await self._mimir.upsert_page(learning_path, promoted_content)
            await self._mimir.upsert_page(
                candidate_path,
                _mark_candidate_promoted(candidate_content, learning_path),
            )
        except Exception as exc:
            logger.warning(
                "PostSessionReflectionService: failed to promote candidate %r: %s",
                candidate_path,
                exc,
            )
            return
        logger.info(
            "PostSessionReflectionService: promoted %r to %r (%s)",
            candidate_path,
            learning_path,
            reason,
        )

    async def _find_existing_page(
        self,
        title: str,
        repo_slug: str,
        claim: str = "",
    ) -> MimirPage | None:
        """Search Mímir for an existing learning page making the same point.

        Returns the first :class:`~niuu.domain.mimir.MimirPage` whose title or
        underlying claim closely matches, or ``None``.
        """
        keywords = _title_to_keywords(title)
        if not keywords:
            return None

        try:
            results = await self._mimir.search(keywords)
        except Exception as exc:
            logger.warning("PostSessionReflectionService: Mímir search failed: %s", exc)
            return None

        matches = [
            page
            for page in results
            if page.meta.category in {"learning-candidates", "learnings"}
            and (
                _titles_similar(page.meta.title or "", title)
                or (claim and _claims_similar(_page_claim(page.content), claim))
            )
        ]
        for category in ("learning-candidates", "learnings"):
            match = next((page for page in matches if page.meta.category == category), None)
            if match is not None:
                return match

        return None


def _parse_reflection_json(raw: str) -> tuple[bool, object | None]:
    """Parse the reflection model's JSON object or null from common wrappers."""
    text = raw.strip()
    if not text:
        return False, None
    if _looks_like_no_learning_response(text):
        return True, None

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
            found, parsed = _parse_yamlish_json(candidate)
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


def _parse_yamlish_json(text: str) -> tuple[bool, object | None]:
    """Parse JSON-shaped output with local-model looseness such as trailing commas."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False, None
    try:
        import yaml  # PyYAML is already present via pydantic-settings[yaml].

        parsed = yaml.safe_load(stripped)
    except Exception:
        return False, None
    if parsed is None or isinstance(parsed, dict | list):
        return True, parsed
    return False, None


def _looks_like_no_learning_response(text: str) -> bool:
    """Treat common prose refusals as a null learning."""
    normalized = re.sub(r"\s+", " ", text.strip().lower()).strip(" .")
    if normalized == "null":
        return True
    no_learning_markers = (
        "no actionable learning",
        "no useful learning",
        "no learning can be extracted",
        "no learning extracted",
        "session was unremarkable",
        "nothing useful to learn",
    )
    return any(marker in normalized for marker in no_learning_markers)


def _compact_log_excerpt(text: str, *, limit: int = 200) -> str:
    """Return a single-line bounded excerpt safe for parser diagnostics."""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


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
    prompt, capped at approximately *token_budget* tokens. An empty string
    means no learnings matched — which is an answer, not a failure.

    A Mímir that cannot be listed raises. Swallowing it returns the same empty
    string as "no learnings exist", and the two are not the same: one is a
    resident with nothing to recall, the other is a resident that has lost
    access to everything its flock ever promoted. See
    ``.claude/rules/no-fallbacks.md``.

    On a composite adapter this lists every configured mount, so a resident
    with local and shared Mímir mounted sees learnings from both.
    """
    pages = await mimir.list_pages(category="learnings")
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
        # No try/except: list_pages just named this page, so a read that fails
        # means the mount went away mid-call, not that the page is absent.
        content = await mimir.read_page(meta.path)

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


def _build_candidate_path(title: str, repo_slug: str) -> str:
    """Build an isolated path that normal learning retrieval never scans."""
    slug = _slugify(title)
    if repo_slug:
        safe_repo = re.sub(r"[^a-z0-9_-]", "-", repo_slug.lower())
        return f"learning-candidates/{safe_repo}/{slug}.md"
    return f"learning-candidates/general/{slug}.md"


def _build_candidate_content(
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
    """Render a reflection candidate with explicit untrusted status."""
    content = _build_page_content(
        title=title,
        learning=learning,
        page_type=page_type,
        tags=tags,
        evidence=evidence,
        repo_slug=repo_slug,
        session_id=session_id,
        date=date,
    )
    content = content.replace(
        f'title: "Learning: {title}"',
        f'title: "Learning candidate: {title}"',
        1,
    )
    content = content.replace("category: learnings", "category: learning-candidates", 1)
    content = content.replace(
        "confidence: low",
        "confidence: low\nstatus: candidate\nevidence_count: 1",
        1,
    )
    content = content.replace(f"# Learning: {title}", f"# Learning candidate: {title}", 1)
    content = content.replace(
        "## What was learned",
        "## Candidate claim\n\n"
        "Untrusted reflection; do not inject as operational context.\n\n"
        "## What was learned",
        1,
    )
    return content


def _candidate_path_from_payload(payload: dict) -> str:
    """Read an explicit candidate reference without accepting path traversal."""
    candidates: list[object] = [payload.get("learning_candidate_path")]
    for key in ("correction", "evidence"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested.get("learning_candidate_path"))
    for value in candidates:
        path = str(value or "").strip().lstrip("/")
        if path.startswith("learning-candidates/") and ".." not in path.split("/"):
            return path
    return ""


def _candidate_title(content: str) -> str:
    match = re.search(
        r'^title:\s*["\']?Learning candidate:\s*(.*?)["\']?\s*$',
        content,
        flags=re.MULTILINE,
    )
    return match.group(1).strip() if match is not None else ""


def _append_candidate_evidence(
    content: str,
    *,
    source: str,
    event_id: str,
    note: str,
    date: datetime,
) -> str:
    """Append non-reflection evidence with an immutable event reference."""
    if event_id and re.search(
        rf"^\s{{4}}event_id:\s*{re.escape(event_id)}\s*$",
        content,
        flags=re.MULTILINE,
    ):
        return content
    entry = (
        f"  - source: {source}\n"
        f"    event_id: {event_id}\n"
        f"    date: {date.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f'    note: "{_escape_yaml(note)}"'
    )
    updated = _insert_timeline_entry(content, entry)
    count_match = re.search(r"^evidence_count:\s*(\d+)", updated, flags=re.MULTILINE)
    count = int(count_match.group(1)) + 1 if count_match is not None else 1
    return _set_frontmatter_value(updated, "evidence_count", str(count))


def _promotion_reason(
    payload: dict,
    *,
    evidence_count: int,
    min_repetitions: int,
) -> str:
    """Return the explicit evidence class that permits promotion, if any."""
    reviewed = payload.get("reviewed_promotion")
    if reviewed is True or (
        isinstance(reviewed, dict)
        and str(reviewed.get("decision") or "").casefold() in {"approved", "promote"}
    ):
        return "explicit_reviewed_promotion"
    feedback_refs = payload.get("external_feedback_refs")
    if payload.get("external_feedback_verified") is True or (
        isinstance(feedback_refs, list) and any(str(item).strip() for item in feedback_refs)
    ):
        return "external_feedback"
    verification_refs = payload.get("verification_refs")
    if payload.get("outcome_verified") is True and (
        not isinstance(verification_refs, list)
        or any(str(item).strip() for item in verification_refs)
    ):
        return "verified_outcome"
    if evidence_count >= min_repetitions:
        return f"repeated_evidence:{evidence_count}"
    return ""


def _promote_candidate_content(
    content: str,
    *,
    candidate_path: str,
    promotion_reason: str,
) -> str:
    """Materialize a trusted learning while retaining its evidence provenance."""
    promoted = re.sub(
        r'^title: "Learning candidate: (.*)"$',
        r'title: "Learning: \1"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    promoted = promoted.replace("category: learning-candidates", "category: learnings", 1)
    promoted = promoted.replace("status: candidate", "status: promoted", 1)
    promoted = promoted.replace("# Learning candidate:", "# Learning:", 1)
    promoted = promoted.replace(
        "Untrusted reflection; do not inject as operational context.",
        "Promoted after the evidence gate below was satisfied.",
        1,
    )
    promoted = _set_frontmatter_value(
        promoted,
        "promotion_reason",
        f'"{_escape_yaml(promotion_reason)}"',
    )
    return _set_frontmatter_value(
        promoted,
        "promoted_from",
        f'"{_escape_yaml(candidate_path)}"',
    )


def _mark_candidate_promoted(content: str, learning_path: str) -> str:
    marked = content.replace("status: candidate", "status: promoted", 1)
    return _set_frontmatter_value(
        marked,
        "promoted_to",
        f'"{_escape_yaml(learning_path)}"',
    )


def _set_frontmatter_value(content: str, key: str, value: str) -> str:
    pattern = rf"^{re.escape(key)}:.*$"
    if re.search(pattern, content, flags=re.MULTILINE):
        return re.sub(pattern, f"{key}: {value}", content, count=1, flags=re.MULTILINE)
    timeline = re.search(r"^timeline:\s*$", content, flags=re.MULTILINE)
    if timeline is None:
        return content
    return content[: timeline.start()] + f"{key}: {value}\n" + content[timeline.start() :]


def _timeline_session_ids(content: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"^\s{4}session_id:\s*(.+?)\s*$", content, flags=re.MULTILINE)
        if match.group(1).strip()
    }


def _promoted_learning_path(content: str) -> str:
    """Return the learnings/ path this candidate was already promoted to, if any."""
    match = re.search(r"^promoted_to:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    if match is None:
        return ""
    path = match.group(1).strip().strip('"').strip("'")
    # Only accept a path this module could have written; never follow one that
    # escapes the learnings tree.
    if not path.startswith("learnings/") or ".." in path.split("/"):
        return ""
    return path


def _timeline_notes(content: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"^\s{4}note:\s*(.+?)\s*$", content, flags=re.MULTILINE)
        if match.group(1).strip()
    ]


def _evidence_is_new(content: str, evidence: str) -> bool:
    """Whether *evidence* says something the timeline does not already record.

    Observing one unchanged fact on a schedule is a single observation repeated,
    not independent corroboration. Counting each repetition drove a candidate to
    "high confidence, 34 sessions" while every note said the same thing.
    """
    if not _significant_words(evidence):
        return False
    return not any(
        _texts_overlap(evidence, note, threshold=_EVIDENCE_DUPLICATE_OVERLAP)
        for note in _timeline_notes(content)
    )


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
    if session_id in _timeline_session_ids(existing_content):
        return existing_content
    if not _evidence_is_new(existing_content, evidence):
        # Same observation again from a new session. Recording it would raise
        # evidence_count and promote the claim on the strength of one fact the
        # resident simply kept re-reading.
        return existing_content

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

    updated = re.sub(
        r"^evidence_count:\s*\d+",
        f"evidence_count: {new_count}",
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


def _significant_words(text: str) -> set[str]:
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
        "candidate",
    }
    return {w for w in re.findall(r"\b\w{3,}\b", text.lower()) if w not in stop}


def _titles_similar(a: str, b: str) -> bool:
    """Return True when *a* and *b* share enough significant words to be duplicates."""
    words_a = _significant_words(a)
    words_b = _significant_words(b)
    if not words_a or not words_b:
        return False

    overlap = words_a & words_b
    union = words_a | words_b
    return len(overlap) / len(union) >= _TITLE_DUPLICATE_SIMILARITY


def _page_claim(content: str) -> str:
    """Extract the ``What was learned`` claim from a candidate or learning page."""
    match = re.search(
        r"^## What was learned\s*$\n+(.*?)(?=\n## |\Z)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _texts_overlap(a: str, b: str, *, threshold: float) -> bool:
    """Whether the shorter of two texts is largely contained in the longer one."""
    words_a = _significant_words(a)
    words_b = _significant_words(b)
    if not words_a or not words_b:
        return False
    shorter = min(len(words_a), len(words_b))
    if shorter < _MIN_OVERLAP_WORDS:
        return words_a == words_b
    return len(words_a & words_b) / shorter >= threshold


def _claims_similar(a: str, b: str) -> bool:
    """Return True when two claim bodies assert substantially the same thing.

    Titles alone were not enough: one belief reappeared as "wait for research
    findings…", "defer action pending research findings…" and "delay backlog
    work pending…", which overlap too little as titles to be caught, while the
    claims underneath them said the same thing in different words.
    """
    return _texts_overlap(a, b, threshold=_CLAIM_DUPLICATE_OVERLAP)


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
