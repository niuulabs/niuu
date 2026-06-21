from __future__ import annotations

from pathlib import Path

import pytest

from ravn.domain.resident_portfolio import (
    ResidentCapabilityDiscoveryResult,
    ResidentCapabilityGap,
    ResidentCapabilityOption,
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.resident_portfolio import (
    LocalCapabilityDiscoveryBackend,
    LocalResidentWorkItemBackend,
    ResidentCapabilityDiscoveryRuntime,
    detect_capability_gaps,
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
    await _write_portfolio(
        backend,
        _objective("gap", "Gap", source_evidence=("missing capability: generic review",)),
    )

    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=LocalCapabilityDiscoveryBackend(),
    ).run(MANDATE)
    restored = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert any(ref.startswith("resident/capability-discovery/") for ref in report.persisted_refs)
    assert restored["gap"].artifact_links
    assert restored["gap"].proof_progress


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
    source = Path("src/ravn/resident_portfolio.py").read_text(encoding="utf-8").casefold()

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
