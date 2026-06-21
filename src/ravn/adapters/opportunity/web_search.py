"""Opportunity source backed by web search and resident memory context."""

from __future__ import annotations

import re
from typing import Any

from niuu.utils import import_class, resolve_secret_kwargs
from ravn.domain.resident_expert import ResidentDomainModel
from ravn.domain.resident_opportunity import (
    ResidentOpportunitySignal,
    ResidentOpportunitySourcePort,
)
from ravn.domain.resident_portfolio import ResidentObjective
from ravn.ports.web_search import SearchResult, WebSearchPort
from ravn.resident_continuation import _compact_line, _slug

_DEFAULT_SEARCH_PROVIDER = "ravn.adapters.tools.web_search.DuckDuckGoLiteSearchProvider"
_DEFAULT_NUM_RESULTS = 5
_DEFAULT_QUERY_TEMPLATES = (
    "{domain} trends opportunities",
    "{domain} customer pain points improvements",
    "{domain} emerging workflows tooling",
)
_STOP_WORDS = {
    "about",
    "after",
    "and",
    "before",
    "company",
    "customer",
    "domain",
    "emerging",
    "from",
    "improvements",
    "opportunities",
    "resident",
    "sells",
    "small",
    "that",
    "this",
    "tooling",
    "trends",
    "what",
    "with",
    "workflows",
}


class WebSearchOpportunitySource(ResidentOpportunitySourcePort):
    """Collect current opportunity signals through a pluggable web search port."""

    def __init__(
        self,
        *,
        search_provider_adapter: str = _DEFAULT_SEARCH_PROVIDER,
        search_provider_kwargs: dict[str, Any] | None = None,
        search_provider_secret_kwargs_env: dict[str, str] | None = None,
        num_results: int = _DEFAULT_NUM_RESULTS,
        query_templates: tuple[str, ...] | list[str] = _DEFAULT_QUERY_TEMPLATES,
        stop_words: tuple[str, ...] | list[str] = tuple(_STOP_WORDS),
        domain_term_limit: int = 3,
    ) -> None:
        provider_cls = import_class(search_provider_adapter)
        provider_kwargs = resolve_secret_kwargs(
            dict(search_provider_kwargs or {}),
            dict(search_provider_secret_kwargs_env or {}),
        )
        self._search: WebSearchPort = provider_cls(**provider_kwargs)
        self._num_results = max(1, int(num_results))
        self._query_templates = tuple(str(item) for item in query_templates if str(item).strip())
        self._stop_words = frozenset(str(item).casefold() for item in stop_words)
        self._domain_term_limit = max(1, int(domain_term_limit))

    async def collect(
        self,
        *,
        mandate: str,
        domain_model: ResidentDomainModel | None,
        objectives: tuple[ResidentObjective, ...],
        limit: int,
    ) -> tuple[ResidentOpportunitySignal, ...]:
        domain = _domain_terms(
            mandate,
            domain_model,
            stop_words=self._stop_words,
            domain_term_limit=self._domain_term_limit,
        )
        queries = [
            template.format(domain=domain, mandate=_compact_line(mandate, limit=120))
            for template in self._query_templates
        ]
        signals: list[ResidentOpportunitySignal] = []
        for query in queries:
            if len(signals) >= limit:
                break
            remaining = limit - len(signals)
            results = await self._search.search(
                query,
                num_results=min(self._num_results, remaining),
            )
            for result in results:
                signals.append(_signal_from_result(query, result, self._stop_words))
        return tuple(signals[:limit])


def _signal_from_result(
    query: str,
    result: SearchResult,
    stop_words: frozenset[str],
) -> ResidentOpportunitySignal:
    summary = _compact_line(
        " ".join(item for item in (result.title, result.snippet) if item),
        limit=260,
    )
    return ResidentOpportunitySignal(
        id=_slug(f"{query}-{result.url}") or "web-opportunity-signal",
        source="web_search",
        kind="current_research",
        summary=summary,
        evidence_ref=result.url,
        themes=tuple(_themes(summary, stop_words=stop_words)),
        outcomes=tuple(_outcomes(summary)),
    )


def _domain_terms(
    mandate: str,
    domain_model: ResidentDomainModel | None,
    *,
    stop_words: frozenset[str],
    domain_term_limit: int,
) -> str:
    seed = mandate
    if domain_model is not None and domain_model.current_understanding:
        seed = domain_model.current_understanding
    terms = _themes(seed, stop_words=stop_words)
    return " ".join(terms[:domain_term_limit]) if terms else _compact_line(mandate, limit=80)


def _themes(text: str, *, stop_words: frozenset[str]) -> list[str]:
    words = [
        word
        for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text.casefold())
        if word not in stop_words
    ]
    themes: list[str] = []
    for index, word in enumerate(words):
        phrase = " ".join(words[index : index + 2])
        if phrase and phrase not in themes:
            themes.append(phrase)
    return themes


def _outcomes(text: str) -> list[str]:
    lowered = text.casefold()
    outcomes: list[str] = []
    if "customer" in lowered or "buyer" in lowered:
        outcomes.append("customer delight")
    if "inventory" in lowered or "stock" in lowered:
        outcomes.append("reliable operations")
    if "quality" in lowered or "defect" in lowered:
        outcomes.append("quality")
    if "creative" in lowered or "new" in lowered:
        outcomes.append("new ideas")
    return outcomes
