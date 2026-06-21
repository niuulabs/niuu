from __future__ import annotations

from pathlib import Path

import pytest

from ravn.domain.resident_opportunity import ResidentOpportunitySignal
from ravn.domain.resident_portfolio import ResidentObjectiveStatus
from ravn.resident_expert import LocalResidentDomainExpertMemory
from ravn.resident_opportunity import (
    LocalResidentOpportunityBackend,
    ResidentOpportunityConfig,
    ResidentOpportunityRuntime,
)
from ravn.resident_portfolio import LocalResidentWorkItemBackend


class StaticOpportunitySource:
    def __init__(self, signals: tuple[ResidentOpportunitySignal, ...]) -> None:
        self.calls = 0
        self._signals = signals

    async def collect(self, **kwargs: object) -> tuple[ResidentOpportunitySignal, ...]:
        self.calls += 1
        limit = int(kwargs["limit"])
        return self._signals[:limit]


MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "You are its resident Ravn. Help it become easier to run, more creative, "
    "and more successful. Ask before spending money or operating physical machines."
)


def _config() -> ResidentOpportunityConfig:
    return ResidentOpportunityConfig(
        max_signals=4,
        max_candidates=4,
        max_selected=2,
        min_total_score=10,
        score_max=10,
        score_mid=4,
    )


@pytest.mark.asyncio
async def test_opportunity_generation_creates_evidence_backed_work(tmp_path: Path) -> None:
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    source = StaticOpportunitySource(
        (
            ResidentOpportunitySignal(
                id="sig-modular-terrain",
                source="web_search",
                kind="current_research",
                summary=(
                    "Modular tabletop terrain kits highlight remixable layouts, "
                    "print quality, and customer display photos."
                ),
                evidence_ref="https://example.com/modular-terrain-research",
                themes=("modular terrain",),
                outcomes=("customer delight", "new model ideas"),
            ),
        )
    )
    runtime = ResidentOpportunityRuntime(
        backend=LocalResidentWorkItemBackend(tmp_path),
        opportunity_backend=LocalResidentOpportunityBackend(tmp_path),
        sources=(source,),
        expert_memory=expert_memory,
        config=_config(),
    )

    report = await runtime.run(MANDATE)
    model = await expert_memory.read_domain_model(MANDATE)

    assert source.calls == 1
    assert [item.duplicate_key for item in report.selected_opportunities] == ["modular-terrain"]
    assert report.created_objectives
    objective = report.created_objectives[0]
    assert objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
    assert "physical_operation" in objective.risk_boundaries
    assert "https://example.com/modular-terrain-research" in "\n".join(
        objective.source_evidence
    )
    assert any(ref.startswith("resident/opportunities/") for ref in report.persisted_refs)
    assert any(ref.startswith("resident/opportunity-reports/") for ref in report.persisted_refs)
    assert model is not None
    assert any("modular terrain" in item for item in model.opportunities)
    assert any("opportunity experiment pending" in item for item in model.open_threads)


@pytest.mark.asyncio
async def test_opportunity_generation_suppresses_repeated_opportunities(
    tmp_path: Path,
) -> None:
    signal = ResidentOpportunitySignal(
        id="sig-repeat",
        source="web_search",
        kind="current_research",
        summary="Customers want customizable organization workflows for recurring chores.",
        evidence_ref="https://example.com/home-automation-research",
        themes=("customizable workflows",),
        outcomes=("lower manual effort",),
    )
    backend = LocalResidentWorkItemBackend(tmp_path)
    opportunity_backend = LocalResidentOpportunityBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    runtime = ResidentOpportunityRuntime(
        backend=backend,
        opportunity_backend=opportunity_backend,
        sources=(StaticOpportunitySource((signal,)),),
        expert_memory=expert_memory,
        config=_config(),
    )

    first = await runtime.run("A resident Ravn helps manage a home automation environment.")
    second = await runtime.run("A resident Ravn helps manage a home automation environment.")
    model = await expert_memory.read_domain_model(
        "A resident Ravn helps manage a home automation environment."
    )

    assert first.selected_opportunities
    assert second.selected_opportunities == ()
    assert [item.duplicate_key for item in second.suppressed_opportunities] == [
        "customizable-workflows"
    ]
    assert any("suppressed duplicate opportunity" in note for note in second.duplicate_notes)
    objectives = await backend.list_objectives(
        "A resident Ravn helps manage a home automation environment."
    )
    assert len(objectives) == 1
    assert model is not None
    assert any("suppressed duplicate opportunity" in item for item in model.memory_hygiene_notes)


@pytest.mark.asyncio
async def test_opportunity_generation_is_cross_domain(tmp_path: Path) -> None:
    source = StaticOpportunitySource(
        (
            ResidentOpportunitySignal(
                id="sig-energy",
                source="memory",
                kind="domain_learning",
                summary="Energy monitoring suggests smart pre-heating and anomaly detection.",
                evidence_ref="resident/domain-expert/domain-model.md",
                themes=("energy monitoring",),
                outcomes=("lower manual effort", "quality"),
            ),
        )
    )
    runtime = ResidentOpportunityRuntime(
        backend=LocalResidentWorkItemBackend(tmp_path),
        opportunity_backend=LocalResidentOpportunityBackend(tmp_path),
        sources=(source,),
        config=_config(),
    )

    report = await runtime.run(
        "You are the resident Ravn for a home automation environment. "
        "Make the home easier to run, safer, and less wasteful."
    )

    assert report.selected_opportunities[0].duplicate_key == "energy-monitoring"
    assert "Kanuck" not in report.selected_opportunities[0].summary
