from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.capabilities.resident_discovery import CatalogWebCapabilityDiscoveryBackend
from ravn.domain.capability_catalog import Capability, CapabilityKind
from ravn.domain.resident_portfolio import (
    ResidentCapabilityDiscoveryResult,
    ResidentCapabilityGap,
    ResidentCapabilityOption,
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.ports.web_search import SearchResult
from ravn.resident_expert import LocalResidentDomainExpertMemory
from ravn.resident_portfolio import (
    LocalCapabilityDiscoveryBackend,
    LocalResidentWorkItemBackend,
    ResidentCapabilityDiscoveryRuntime,
    detect_capability_gaps,
    render_capability_discovery_result,
)

MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts. "
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class RecordingDiscovery:
    def __init__(self) -> None:
        self.calls: list[ResidentCapabilityGap] = []

    async def discover(
        self,
        mandate: str,
        gap: ResidentCapabilityGap,
    ) -> ResidentCapabilityDiscoveryResult:
        self.calls.append(gap)
        return ResidentCapabilityDiscoveryResult(
            gap=gap,
            capability_summary=f"Capability gap: {gap.capability}",
            why_it_matters="The resident needs a safe path before advancing.",
            known_constraints=gap.risk_boundaries,
            candidate_options=(
                ResidentCapabilityOption(
                    id="evaluate-generic-path",
                    title="Evaluate generic path",
                    summary="Inspect existing read-only paths first.",
                    required_tools=("read_only_catalog_inspection",),
                    safe_next_experiment="Run a read-only local dry-run.",
                    evidence=gap.source_evidence,
                ),
                ResidentCapabilityOption(
                    id="operate-risky-path",
                    title="Operate risky path",
                    summary="Use a bounded external capability after approval.",
                    risks=("physical_operation",),
                    approval_required=True,
                    safe_next_experiment="Ask before operating the bounded path.",
                    evidence=gap.source_evidence,
                ),
            ),
            recommended_option_id="evaluate-generic-path",
            recommended_safe_next_experiment="Run a read-only local dry-run.",
            unresolved_questions=("Which approval boundary applies?",),
        )


class StaticSearchProvider:
    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self._results = results or [
            SearchResult(
                title="Adapter integration guide",
                url="https://example.com/adapter-guide",
                snippet="Compare safe adapter approaches before automating external effects.",
            )
        ]

    async def search(self, query: str, *, num_results: int) -> list[SearchResult]:
        self.calls.append((query, num_results))
        return self._results[:num_results]


def _objective(
    objective_id: str,
    title: str,
    *,
    status: str = ResidentObjectiveStatus.CANDIDATE.value,
    required_capabilities: tuple[str, ...] = (),
    risk_boundaries: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    source_evidence: tuple[str, ...] = (),
    reasoning: str = "remembered reason",
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance {title}",
        serves_mandate_because="It advances the resident mandate.",
        expected_outcome="A proof artifact exists.",
        proof_criteria=("A proof artifact exists.",),
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
        status=status,
        required_capabilities=required_capabilities,
        risk_boundaries=risk_boundaries,
        dependencies=dependencies,
        source_evidence=source_evidence,
        reasoning=reasoning,
    )


async def _write_portfolio(
    backend: LocalResidentWorkItemBackend,
    *objectives: ResidentObjective,
) -> None:
    for objective in objectives:
        await backend.write_objective(objective)
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE, objectives=objectives))


def test_capability_gaps_are_detected_from_objective_fields_and_evidence() -> None:
    objectives = (
        _objective(
            "required",
            "Required capability",
            required_capabilities=("generic evidence review",),
        ),
        _objective(
            "evidence",
            "Evidence gap",
            source_evidence=("missing capability: generic execution review",),
        ),
        _objective("blocked", "Blocked path", dependencies=("missing-path",)),
    )

    gaps = detect_capability_gaps(objectives)

    assert {gap.source_objective_id for gap in gaps} >= {"required", "evidence", "blocked"}
    assert any(gap.capability == "generic execution review" for gap in gaps)


def test_blocked_objective_without_safe_execution_path_becomes_gap() -> None:
    objective = _objective(
        "blocked-execution",
        "Blocked execution path",
        status=ResidentObjectiveStatus.BLOCKED.value,
        reasoning="No safe execution path exists for this workflow yet.",
    )

    gaps = detect_capability_gaps((objective,))

    assert len(gaps) == 1
    assert gaps[0].source_objective_id == "blocked-execution"
    assert "execution path" in gaps[0].reason


@pytest.mark.asyncio
async def test_discovery_uses_port_abstraction(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("gap", "Gap", source_evidence=("missing capability: generic review",)),
    )
    discovery = RecordingDiscovery()

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=discovery,
    ).run(MANDATE)

    assert discovery.calls
    assert report.selected_gap is not None
    assert report.discovery_result is not None


@pytest.mark.asyncio
async def test_local_discovery_produces_structured_options(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("gap", "Gap", source_evidence=("missing capability: generic review",)),
    )

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=LocalCapabilityDiscoveryBackend(),
    ).run(MANDATE)

    assert report.discovery_result is not None
    assert report.discovery_result.candidate_options
    assert report.discovery_result.recommended_safe_next_experiment


@pytest.mark.asyncio
async def test_catalog_web_discovery_suppresses_adapter_when_capability_exists(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective("gap", "Gap", source_evidence=("missing capability: web search",)),
    )
    discovery = CatalogWebCapabilityDiscoveryBackend(
        catalog_capabilities=(
            Capability(
                capability_id="tool:web_search",
                kind=CapabilityKind.TOOL,
                name="web_search",
                description="Search the web for current information.",
                source="ravn.builtin_registry",
            ),
        ),
        search_provider_adapter=(
            "tests.test_ravn.test_resident_capability_discovery.StaticSearchProvider"
        ),
        candidate_adapter_configs=(
            {
                "adapter": "example.adapters.WebSearchAdapter",
                "kwargs": {"mode": "read_only"},
            },
        ),
    )

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=discovery,
        expert_memory=expert_memory,
    ).run(MANDATE)
    model = await expert_memory.read_domain_model(MANDATE)

    assert report.discovery_result is not None
    assert report.discovery_result.existing_capabilities
    assert report.discovery_result.duplicate_check_notes
    assert not report.discovery_result.configuration_evidence
    assert all(
        not option.required_adapters for option in report.discovery_result.candidate_options
    )
    assert model is not None
    assert any("existing capability available" in item for item in model.known_facts)
    assert any("adapter creation suppressed" in item for item in model.memory_hygiene_notes)


@pytest.mark.asyncio
async def test_catalog_web_discovery_researches_and_records_dynamic_adapter_config(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "gap",
            "Gap",
            source_evidence=("missing capability: mesh linting",),
        ),
    )
    discovery = CatalogWebCapabilityDiscoveryBackend(
        catalog_capabilities=(),
        search_provider_adapter=(
            "tests.test_ravn.test_resident_capability_discovery.StaticSearchProvider"
        ),
        candidate_adapter_configs=(
            {
                "adapter": "example.adapters.MeshLintAdapter",
                "kwargs": {"dry_run": True},
                "safe_next_experiment": "Load the adapter and lint a local fixture.",
            },
        ),
    )

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=discovery,
        expert_memory=expert_memory,
    ).run(MANDATE)
    model = await expert_memory.read_domain_model(MANDATE)

    assert report.discovery_result is not None
    assert report.discovery_result.research_evidence
    assert report.discovery_result.configuration_evidence
    assert any(option.required_adapters for option in report.discovery_result.candidate_options)
    assert "example.adapters.MeshLintAdapter" in render_capability_discovery_result(
        report.discovery_result
    )
    assert model is not None
    assert any("candidate adapter configuration" in item for item in model.known_facts)


@pytest.mark.asyncio
async def test_risky_options_create_operator_needed_objectives(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "gap",
            "Gap",
            risk_boundaries=("physical_operation",),
            source_evidence=("missing capability: bounded operation",),
        ),
    )

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=LocalCapabilityDiscoveryBackend(),
    ).run(MANDATE)
    objectives = await backend.list_objectives(MANDATE)

    assert report.operator_questions
    assert any(item.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value for item in objectives)


@pytest.mark.asyncio
async def test_safe_options_create_dry_run_evaluation_objectives(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("gap", "Gap", source_evidence=("missing capability: generic review",)),
    )

    await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=LocalCapabilityDiscoveryBackend(),
    ).run(MANDATE)
    objectives = await backend.list_objectives(MANDATE)

    assert any(
        item.status == ResidentObjectiveStatus.CANDIDATE.value
        and item.kind == ResidentObjectiveKind.VERIFICATION.value
        for item in objectives
    )


@pytest.mark.asyncio
async def test_discovery_artifacts_are_persisted_and_linked(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective("gap", "Gap", source_evidence=("missing capability: generic review",)),
    )

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=LocalCapabilityDiscoveryBackend(),
        expert_memory=expert_memory,
    ).run(MANDATE)
    restored = {item.id: item for item in await backend.list_objectives(MANDATE)}
    model = await expert_memory.read_domain_model(MANDATE)

    assert any(ref.startswith("resident/capability-discovery/") for ref in report.persisted_refs)
    assert restored["gap"].artifact_links
    assert restored["gap"].proof_progress
    assert model is not None
    assert any("recommended safe experiment" in item for item in model.known_facts)
    assert any("capability discovery recommended" in item for item in model.resident_decisions)
    assert any(item.kind == "capability_evaluation" for item in model.artifacts)


@pytest.mark.asyncio
async def test_no_gap_returns_sleep_like_report(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready"))

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=RecordingDiscovery(),
    ).run(MANDATE)

    assert report.selected_gap is None
    assert report.created_or_updated_objectives == ()


def test_resident_capability_discovery_contains_no_domain_specific_playbook_terms() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("src/ravn/resident_portfolio").glob("*.py"))
    ).casefold()

    for forbidden in (
        "kanuck",
        "inventory",
        "3d printing",
        "prd",
        "srd",
        "forge",
        "blender",
        "slicing",
        "product catalog",
    ):
        assert forbidden not in source
