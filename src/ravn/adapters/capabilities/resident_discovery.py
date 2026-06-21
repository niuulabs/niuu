"""Resident capability discovery adapters."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from niuu.utils import import_class, resolve_secret_kwargs

from ravn.domain.capability_catalog import Capability, CapabilityKind
from ravn.domain.resident_portfolio import (
    CapabilityDiscoveryPort,
    ResidentCapabilityDiscoveryResult,
    ResidentCapabilityGap,
    ResidentCapabilityOption,
)
from ravn.ports.web_search import SearchResult, WebSearchPort

_DEFAULT_SEARCH_PROVIDER = "ravn.adapters.tools.web_search.DuckDuckGoLiteSearchProvider"
_DEFAULT_NUM_RESULTS = 5
_STOP_WORDS = {
    "adapter",
    "capability",
    "execution",
    "missing",
    "path",
    "safe",
    "tool",
    "workflow",
}


class CatalogWebCapabilityDiscoveryBackend(CapabilityDiscoveryPort):
    """Research capability gaps using the configured catalog and web search."""

    def __init__(
        self,
        *,
        catalog_capabilities: Sequence[Capability | dict[str, Any]] | None = None,
        search_provider_adapter: str = _DEFAULT_SEARCH_PROVIDER,
        search_provider_kwargs: dict[str, Any] | None = None,
        search_provider_secret_kwargs_env: dict[str, str] | None = None,
        num_results: int = _DEFAULT_NUM_RESULTS,
        candidate_adapter_configs: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        self._catalog = tuple(_capability_from_config(item) for item in catalog_capabilities or ())
        provider_cls = import_class(search_provider_adapter)
        provider_kwargs = resolve_secret_kwargs(
            dict(search_provider_kwargs or {}),
            dict(search_provider_secret_kwargs_env or {}),
        )
        self._search: WebSearchPort = provider_cls(**provider_kwargs)
        self._num_results = max(1, int(num_results))
        self._candidate_adapter_configs = tuple(dict(item) for item in candidate_adapter_configs or ())

    async def discover(
        self,
        mandate: str,
        gap: ResidentCapabilityGap,
    ) -> ResidentCapabilityDiscoveryResult:
        capability = _compact_line(gap.capability or gap.summary, limit=120)
        existing = _matching_capabilities(self._catalog, capability)
        duplicate_notes = _duplicate_check_notes(existing, capability)
        search_results: list[SearchResult] = []
        research_evidence: tuple[str, ...] = ()

        if not existing:
            query = f"{capability} adapter workflow automation safe integration"
            search_results = await self._search.search(query, num_results=self._num_results)
            research_evidence = tuple(_search_result_line(item) for item in search_results)

        options = _options_from_existing(existing, gap, capability)
        configuration_evidence: tuple[str, ...] = ()
        if not existing:
            researched_option = _research_option(gap, capability, search_results)
            adapter_options, configuration_evidence = _adapter_options(
                gap,
                capability,
                self._candidate_adapter_configs,
            )
            options = (researched_option, *adapter_options)

        if gap.risk_boundaries:
            options = (
                *options,
                ResidentCapabilityOption(
                    id=f"approve-{_slug(capability)}",
                    title=f"Ask operator before risky capability use for {capability}",
                    summary="Clarify approval boundaries before external or physical side effects.",
                    risks=gap.risk_boundaries,
                    approval_required=True,
                    safe_next_experiment="Ask the operator for explicit approval boundaries.",
                    evidence=gap.source_evidence,
                ),
            )

        recommended = options[0] if options else None
        return ResidentCapabilityDiscoveryResult(
            gap=gap,
            capability_summary=f"Capability gap: {capability}",
            why_it_matters=(
                "The resident needs a known capability path before it can safely "
                "advance or delegate this objective."
            ),
            known_constraints=tuple(
                item
                for item in (
                    *gap.risk_boundaries,
                    *gap.blocked_dependencies,
                    *gap.required_capabilities,
                )
                if item
            ),
            candidate_options=options,
            recommended_option_id=recommended.id if recommended else "",
            recommended_safe_next_experiment=(
                recommended.safe_next_experiment if recommended else "Record that no path was found."
            ),
            unresolved_questions=(
                ("What approval boundary applies?",) if gap.risk_boundaries else ()
            ),
            budget_notes=(
                f"catalog matches: {len(existing)}; web results: {len(search_results)}"
            ),
            existing_capabilities=tuple(_capability_line(item) for item in existing),
            duplicate_check_notes=duplicate_notes,
            research_evidence=research_evidence,
            configuration_evidence=configuration_evidence,
        )


def _capability_from_config(raw: Capability | dict[str, Any]) -> Capability:
    if isinstance(raw, Capability):
        return raw
    kind_value = str(raw.get("kind") or CapabilityKind.TOOL.value)
    try:
        kind = CapabilityKind(kind_value)
    except ValueError:
        kind = CapabilityKind.TOOL
    return Capability(
        capability_id=str(raw.get("id") or raw.get("capability_id") or raw.get("name") or ""),
        kind=kind,
        name=str(raw.get("name") or raw.get("capability_id") or raw.get("id") or ""),
        description=str(raw.get("description") or ""),
        input_schema=dict(raw.get("input_schema") or {}),
        required_permission=str(raw.get("required_permission") or ""),
        tags=[str(item) for item in raw.get("tags") or []],
        source=str(raw.get("source") or "configured_catalog"),
        version=str(raw.get("version") or ""),
        metadata=dict(raw.get("metadata") or {}),
    )


def _matching_capabilities(
    catalog: tuple[Capability, ...],
    capability: str,
) -> tuple[Capability, ...]:
    wanted = _tokens(capability)
    if not wanted:
        return ()
    scored: list[tuple[int, Capability]] = []
    for item in catalog:
        haystack = _tokens(
            " ".join(
                (
                    item.capability_id,
                    item.name,
                    item.description,
                    " ".join(item.tags),
                )
            )
        )
        score = len(wanted & haystack)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].capability_id))
    return tuple(item for _, item in scored[:3])


def _options_from_existing(
    existing: tuple[Capability, ...],
    gap: ResidentCapabilityGap,
    capability: str,
) -> tuple[ResidentCapabilityOption, ...]:
    options: list[ResidentCapabilityOption] = []
    for item in existing:
        options.append(
            ResidentCapabilityOption(
                id=f"use-existing-{_slug(item.capability_id or item.name)}",
                title=f"Use existing capability {item.name}",
                summary=(
                    "An existing catalog capability appears to satisfy this gap; "
                    "verify it before building anything new."
                ),
                required_tools=(item.name,) if item.kind is CapabilityKind.TOOL else (),
                required_workflows=(item.name,) if item.kind is CapabilityKind.WORKFLOW else (),
                safe_next_experiment=(
                    f"Run a read-only dry-run with existing capability {item.name}."
                ),
                evidence=(
                    *gap.source_evidence,
                    f"existing capability match: {_capability_line(item)}",
                ),
            )
        )
    if not options:
        return ()
    return tuple(options)


def _research_option(
    gap: ResidentCapabilityGap,
    capability: str,
    search_results: Sequence[SearchResult],
) -> ResidentCapabilityOption:
    evidence = tuple(_search_result_line(item) for item in search_results) or gap.source_evidence
    return ResidentCapabilityOption(
        id=f"evaluate-researched-{_slug(capability)}",
        title=f"Evaluate researched capability paths for {capability}",
        summary="Compare real external options before building a new adapter or workflow.",
        required_tools=("web_search", "web_fetch"),
        safe_next_experiment="Read the top sources and record fit, risk, cost, and adapter shape.",
        evidence=evidence,
    )


def _adapter_options(
    gap: ResidentCapabilityGap,
    capability: str,
    configs: tuple[dict[str, Any], ...],
) -> tuple[tuple[ResidentCapabilityOption, ...], tuple[str, ...]]:
    options: list[ResidentCapabilityOption] = []
    evidence: list[str] = []
    for raw in configs:
        adapter = str(raw.get("adapter") or "").strip()
        if not adapter:
            continue
        kwargs = dict(raw.get("kwargs") or {})
        title = str(raw.get("title") or f"Configure adapter {adapter}")
        summary = str(
            raw.get("summary")
            or "Configure a capability adapter through YAML using class path plus kwargs."
        )
        risks = tuple(str(item) for item in raw.get("risks") or ())
        approval_required = bool(raw.get("approval_required") or risks)
        config = {"adapter": adapter, "kwargs": kwargs}
        if isinstance(raw.get("safe_experiment_input"), dict):
            config["safe_experiment_input"] = dict(raw["safe_experiment_input"])
        evidence_line = f"candidate adapter config: {json.dumps(config, sort_keys=True)}"
        evidence.append(evidence_line)
        options.append(
            ResidentCapabilityOption(
                id=f"configure-{_slug(adapter)}",
                title=title,
                summary=summary,
                required_adapters=(adapter,),
                risks=risks,
                approval_required=approval_required,
                safe_next_experiment=str(
                    raw.get("safe_next_experiment")
                    or "Load the adapter from YAML and run a no-side-effect dry-run."
                ),
                evidence=(*gap.source_evidence, evidence_line),
                configuration=config,
            )
        )
    return tuple(options), tuple(evidence)


def _duplicate_check_notes(
    existing: tuple[Capability, ...],
    capability: str,
) -> tuple[str, ...]:
    if existing:
        names = ", ".join(item.capability_id or item.name for item in existing)
        return (
            f"existing capability satisfies '{capability}': {names}",
            "adapter creation suppressed until the existing capability is disproven",
        )
    return (f"no existing catalog capability matched '{capability}'",)


def _capability_line(item: Capability) -> str:
    return (
        f"{item.kind.value}:{item.name} id={item.capability_id} "
        f"source={item.source or 'catalog'} tags={','.join(item.tags) or 'none'}"
    )


def _search_result_line(item: SearchResult) -> str:
    return _compact_line(f"{item.title} | {item.url} | {item.snippet}", limit=300)


def _tokens(value: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-z0-9_]+", str(value).casefold().replace("_", " "))
        if len(item) > 2 and item not in _STOP_WORDS
    }


def _slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", str(value).casefold()))[:90]


def _compact_line(value: str, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."
