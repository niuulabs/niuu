#!/usr/bin/env python3
"""Run a real resident opportunity-generation proof."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from niuu.utils import import_class, resolve_secret_kwargs
from ravn.config import ResidentOpportunitySourceConfig, Settings
from ravn.domain.resident_expert import ResidentDomainModel
from ravn.domain.resident_opportunity import ResidentOpportunitySourcePort
from ravn.resident_expert import LocalResidentDomainExpertMemory
from ravn.resident_opportunity import (
    LocalResidentOpportunityBackend,
    ResidentOpportunityConfig,
    ResidentOpportunityRuntime,
)
from ravn.resident_portfolio import LocalResidentWorkItemBackend

DEFAULT_MANDATE = (
    "Kanuck Valley Models is my small 3D printing company.\n"
    "You are its resident Ravn.\n"
    "Help it become easier to run, more creative, and more successful.\n"
    "Ask before spending money or operating physical machines."
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/private/tmp/resident-opportunity-proof")
    parser.add_argument("--mandate", default=DEFAULT_MANDATE)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--require-web-evidence", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    opportunity_cfg = settings.resident_opportunity_generation
    expert_memory = LocalResidentDomainExpertMemory(root)
    await _seed_domain_memory(expert_memory, args.mandate)

    sources = _build_sources(settings)
    runtime = ResidentOpportunityRuntime(
        backend=LocalResidentWorkItemBackend(root),
        opportunity_backend=LocalResidentOpportunityBackend(root),
        sources=sources,
        expert_memory=expert_memory,
        config=ResidentOpportunityConfig(
            max_signals=max(1, int(opportunity_cfg.max_signals)),
            max_candidates=max(1, int(opportunity_cfg.max_candidates)),
            max_selected=max(1, int(opportunity_cfg.max_selected)),
            min_total_score=max(0, int(opportunity_cfg.min_total_score)),
            score_max=max(1, int(opportunity_cfg.score_max)),
            score_mid=max(0, int(opportunity_cfg.score_mid)),
            evidence_score_step=max(0, int(opportunity_cfg.evidence_score_step)),
            outcome_score_step=max(0, int(opportunity_cfg.outcome_score_step)),
            signal_score_step=max(0, int(opportunity_cfg.signal_score_step)),
            risk_penalty=max(0, int(opportunity_cfg.risk_penalty)),
            cost_penalty=max(0, int(opportunity_cfg.cost_penalty)),
            duplicate_penalty=max(0, int(opportunity_cfg.duplicate_penalty)),
            rationale_outcome_limit=max(1, int(opportunity_cfg.rationale_outcome_limit)),
            stop_words=tuple(opportunity_cfg.stop_words),
        ),
    )

    reports = []
    for index in range(max(1, int(args.cycles))):
        report = await runtime.run(args.mandate)
        reports.append(report)
        print(
            "[proof] cycle="
            f"{index + 1} signals={len(report.signals)} "
            f"selected={len(report.selected_opportunities)} "
            f"suppressed={len(report.suppressed_opportunities)}"
        )
        for opportunity in report.selected_opportunities:
            print(
                "[proof] selected="
                f"{opportunity.duplicate_key} score={opportunity.score.total} "
                f"stage={opportunity.stage} title={opportunity.title}"
            )
            print(f"[proof] safe_next={opportunity.safe_next_experiment}")
        for note in report.duplicate_notes:
            print(f"[proof] duplicate_note={note}")

    first = reports[0]
    later = reports[1:] if len(reports) > 1 else []
    refs = [ref for report in reports for ref in report.persisted_refs]
    web_evidence = [
        signal.evidence_ref
        for report in reports
        for signal in report.signals
        if signal.source == "web_search" and signal.evidence_ref.startswith("http")
    ]

    print("[proof] Resident opportunity generation proof.")
    print(f"[proof] memory_root={root}")
    print(f"[proof] sources={[type(source).__name__ for source in sources]}")
    print(f"[proof] persisted_refs={len(refs)}")
    print(f"[proof] web_evidence_count={len(web_evidence)}")
    for url in web_evidence[:5]:
        print(f"[proof] web_evidence={url}")

    if not first.signals:
        raise SystemExit("[proof] expected opportunity signals")
    if not first.selected_opportunities:
        raise SystemExit("[proof] expected selected opportunity on first cycle")
    if not first.created_objectives:
        raise SystemExit("[proof] expected portfolio objective created from opportunity")
    if not any(ref.startswith("resident/opportunities/") for ref in refs):
        raise SystemExit("[proof] expected persisted opportunity artifact")
    if not any(ref.startswith("resident/opportunity-reports/") for ref in refs):
        raise SystemExit("[proof] expected persisted opportunity report")
    if args.require_web_evidence and not web_evidence:
        raise SystemExit("[proof] expected real web-search evidence")
    if later and not any(report.suppressed_opportunities for report in later):
        raise SystemExit("[proof] expected duplicate suppression in later cycle")
    if later and not any(report.duplicate_notes for report in later):
        raise SystemExit("[proof] expected duplicate notes in later cycle")


async def _seed_domain_memory(
    expert_memory: LocalResidentDomainExpertMemory,
    mandate: str,
) -> None:
    model = ResidentDomainModel(
        mandate=mandate,
        current_understanding=(
            "Tabletop terrain and model products are made through a small 3D printing "
            "company that wants lower manual effort, better quality, and creative "
            "product ideas under approval boundaries."
        ),
        hypotheses=(
            "Small operators benefit from low-manual-effort workflows.",
            "Creative product exploration should begin with read-only evidence before build work.",
        ),
        known_facts=(
            "The operator requires approval before spending money.",
            "The operator requires approval before physical machine operation.",
        ),
        open_threads=(
            "What evidence-backed product or workflow opportunity is worth a safe experiment?",
        ),
    )
    await expert_memory.write_domain_model(model)


def _build_sources(settings: Settings) -> tuple[ResidentOpportunitySourcePort, ...]:
    source_configs = settings.resident_opportunity_generation.sources
    if not source_configs:
        source_configs = [ResidentOpportunitySourceConfig()]
    sources: list[ResidentOpportunitySourcePort] = []
    for source_config in source_configs:
        if not source_config.enabled:
            continue
        if "Mock" in source_config.adapter:
            raise SystemExit("[proof] mock opportunity source does not count as real proof")
        cls = import_class(source_config.adapter)
        kwargs: dict[str, Any] = resolve_secret_kwargs(
            dict(source_config.kwargs),
            dict(source_config.secret_kwargs_env),
        )
        kwargs.setdefault(
            "domain_term_limit",
            settings.resident_opportunity_generation.domain_term_limit,
        )
        kwargs.setdefault("stop_words", tuple(settings.resident_opportunity_generation.stop_words))
        sources.append(cls(**kwargs))
    if not sources:
        raise SystemExit("[proof] expected at least one enabled opportunity source")
    return tuple(sources)


if __name__ == "__main__":
    asyncio.run(main())
