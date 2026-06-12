"""Learning loop for Mímir (NIU-1062), modeled on Hindsight's retain/reflect.

Three capabilities, all deterministic (zero LLM calls):

1. **Evidence-counted beliefs** — :func:`compute_page_evidence` scores every
   Key Fact in a page's Compiled Truth zone against its append-only Timeline:
   a proof count (supporting entries) and a freshness trend
   (``new / strengthening / stable / weakening / stale``).
2. **Belief revision with journey** — :func:`revise_belief` rewrites a fact in
   the Compiled Truth zone while appending a ``belief revised:`` entry to the
   Timeline, so the old belief stays queryable forever. Never silently
   overwrites; never edits existing timeline entries (L09-safe).
3. **Write-time scoped consolidation** — :func:`consolidate_source` finds the
   pages a newly-ingested raw source bears on (entity/title mention) and
   recomputes their evidence, so learning happens on every write instead of
   waiting for the nightly dream cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mimir.compiled_truth import (
    append_timeline_entry,
    parse_page,
    rewrite_compiled_truth,
)
from mimir.config import EvidenceConfig
from mimir.ranking import tokenize

_FACT_BULLET_RE = re.compile(r"^\s*-\s+(.*\S)\s*$", re.MULTILINE)
_KEY_FACTS_RE = re.compile(r"### Key Facts\s*\n(.*?)(?=^###|\Z)", re.MULTILINE | re.DOTALL)

TREND_NEW = "new"
TREND_STRENGTHENING = "strengthening"
TREND_STABLE = "stable"
TREND_WEAKENING = "weakening"
TREND_STALE = "stale"


@dataclass
class FactEvidence:
    """Evidence accounting for one compiled-truth fact.

    ``proof_count`` is the total: timeline proofs plus raw-source proofs.
    ``source_proof_count`` breaks out how many came from raw sources.
    """

    fact: str
    proof_count: int
    trend: str
    latest_support: str | None  # YYYY-MM-DD of the freshest supporting entry
    supporting_dates: list[str] = field(default_factory=list)
    source_proof_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "proof_count": self.proof_count,
            "trend": self.trend,
            "latest_support": self.latest_support,
            "supporting_dates": self.supporting_dates,
            "source_proof_count": self.source_proof_count,
        }


def extract_key_facts(content: str) -> list[str]:
    """Bullet lines under '### Key Facts' inside the Compiled Truth zone."""
    page = parse_page(content)
    match = _KEY_FACTS_RE.search(page.compiled_truth)
    if match is None:
        return []
    return _FACT_BULLET_RE.findall(match.group(1))


def compute_page_evidence(
    content: str,
    config: EvidenceConfig | None = None,
    *,
    now: datetime | None = None,
    sources: list[tuple[str, str]] | None = None,
) -> list[FactEvidence]:
    """Score each Key Fact against the page's timeline entries and raw sources.

    A timeline entry supports a fact when they share at least
    ``min_token_overlap`` significant tokens; the same rule applies to each
    ``(source_id, content)`` pair in *sources*. Source proofs add to the
    total ``proof_count`` but carry no date, so trend derivation stays
    timeline-driven:

    - no support → ``new``
    - freshest support older than ``stale_after_days`` → ``stale``
    - older than ``weakening_after_days`` → ``weakening``
    - fresh with ≥ ``strengthening_min_proofs`` proofs → ``strengthening``
    - otherwise → ``stable``
    """
    cfg = config or EvidenceConfig()
    moment = now or datetime.now(UTC)
    page = parse_page(content)
    facts = extract_key_facts(content)
    source_token_sets = [tokenize(source_content) for _, source_content in sources or []]

    entry_token_sets = [
        (entry.date, tokenize(entry.description)) for entry in page.timeline_entries
    ]

    results: list[FactEvidence] = []
    for fact in facts:
        fact_tokens = tokenize(fact)
        supporting: list[str] = []
        for entry_date, entry_tokens in entry_token_sets:
            if len(fact_tokens & entry_tokens) >= cfg.min_token_overlap:
                supporting.append(entry_date)

        source_proofs = sum(
            1
            for source_tokens in source_token_sets
            if len(fact_tokens & source_tokens) >= cfg.min_token_overlap
        )

        supporting.sort()
        results.append(
            FactEvidence(
                fact=fact,
                proof_count=len(supporting) + source_proofs,
                trend=_derive_trend(supporting, cfg, moment),
                latest_support=supporting[-1] if supporting else None,
                supporting_dates=supporting,
                source_proof_count=source_proofs,
            )
        )
    return results


def _derive_trend(supporting_dates: list[str], cfg: EvidenceConfig, now: datetime) -> str:
    if not supporting_dates:
        return TREND_NEW

    latest = _parse_date(supporting_dates[-1])
    if latest is None:
        return TREND_STABLE
    age_days = (now - latest).total_seconds() / 86400.0

    if age_days > cfg.stale_after_days:
        return TREND_STALE
    if age_days > cfg.weakening_after_days:
        return TREND_WEAKENING
    if len(supporting_dates) >= cfg.strengthening_min_proofs:
        return TREND_STRENGTHENING
    return TREND_STABLE


def _parse_date(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def revise_belief(
    content: str,
    old_fact: str,
    new_fact: str,
    attribution: str,
    *,
    now: datetime | None = None,
) -> str:
    """Rewrite *old_fact* to *new_fact*, recording the journey in the Timeline.

    The Compiled Truth zone is rewritable by design; the Timeline is
    append-only. The revision entry preserves the superseded belief so the
    page's history answers "what did we used to believe?".

    Raises:
        ValueError: When *old_fact* is not present in the Compiled Truth zone.
    """
    page = parse_page(content)
    if old_fact not in page.compiled_truth:
        raise ValueError(f"Fact not found in Compiled Truth zone: {old_fact!r}")

    new_truth = page.compiled_truth.replace(old_fact, new_fact, 1)
    revised = rewrite_compiled_truth(content, new_truth)

    moment = now or datetime.now(UTC)
    entry = (
        f'- {moment.strftime("%Y-%m-%d")}: belief revised: "{old_fact}" → '
        f'"{new_fact}". [Source: {attribution}]'
    )
    return append_timeline_entry(revised, entry)


def find_bearing_pages(
    source_title: str,
    source_content: str,
    pages: list[tuple[str, str]],
    *,
    min_token_overlap: int,
) -> list[str]:
    """Paths of wiki pages a new raw source bears on (scoped consolidation).

    A page is affected when its title tokens appear in the source content, or
    the source title shares ``min_token_overlap`` significant tokens with the
    page title. Pure function over (path, title) pairs — zero I/O, zero LLM.
    """
    source_tokens = tokenize(f"{source_title}\n{source_content}")
    source_title_tokens = tokenize(source_title)
    affected: list[str] = []
    for path, title in pages:
        title_tokens = tokenize(title)
        if not title_tokens:
            continue
        if title_tokens <= source_tokens:
            affected.append(path)
            continue
        if len(title_tokens & source_title_tokens) >= min_token_overlap:
            affected.append(path)
    return affected


@dataclass
class ConsolidationResult:
    """Outcome of one write-time scoped consolidation pass (micro-dream)."""

    source_id: str
    pages: list[str] = field(default_factory=list)
    strengthened: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "pages": self.pages,
            "strengthened": self.strengthened,
        }


def consolidate_source(
    source_id: str,
    source_title: str,
    source_content: str,
    pages: list[tuple[str, str, str]],
    other_sources: list[tuple[str, str]],
    config: EvidenceConfig | None = None,
    *,
    now: datetime | None = None,
) -> ConsolidationResult:
    """Write-time scoped consolidation: re-evidence the pages a source bears on.

    For every bearing page (per :func:`find_bearing_pages`), evidence is
    computed twice — once without the newly-ingested source (the pre-ingest
    state, *other_sources* only) and once with it. Facts whose total
    ``proof_count`` rose are counted as ``strengthened``. Pure function over
    ``(path, title, content)`` triples — zero I/O, zero LLM.
    """
    cfg = config or EvidenceConfig()
    bearing = find_bearing_pages(
        source_title,
        source_content,
        [(path, title) for path, title, _ in pages],
        min_token_overlap=cfg.min_token_overlap,
    )
    content_by_path = {path: content for path, _, content in pages}
    all_sources = [*other_sources, (source_id, source_content)]

    strengthened = 0
    for path in bearing:
        content = content_by_path[path]
        before = compute_page_evidence(content, cfg, now=now, sources=other_sources)
        after = compute_page_evidence(content, cfg, now=now, sources=all_sources)
        strengthened += sum(
            1 for pre, post in zip(before, after, strict=True) if post.proof_count > pre.proof_count
        )
    return ConsolidationResult(source_id=source_id, pages=bearing, strengthened=strengthened)
