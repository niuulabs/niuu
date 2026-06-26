from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.resident_work.local import LocalResidentWorkItemBackend
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.resident_portfolio import (
    ResidentPortfolioValidator,
    prioritize_objectives,
    render_validation_report,
    select_objectives,
)

MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts. "
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


def _objective(
    objective_id: str,
    title: str,
    *,
    status: str = ResidentObjectiveStatus.CANDIDATE.value,
    dependencies: tuple[str, ...] = (),
    proof_progress: tuple[str, ...] = (),
    artifact_links: tuple[str, ...] = (),
    consolidation_links: tuple[str, ...] = (),
    pending_question: str = "",
    purpose: str | None = None,
    proof_criteria: tuple[str, ...] = ("proof exists",),
    priority_score: int = 0,
    kind: str = ResidentObjectiveKind.RESEARCH.value,
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=purpose if purpose is not None else f"Advance {title}",
        serves_mandate_because="It advances the mandate.",
        expected_outcome="A proof artifact exists.",
        proof_criteria=proof_criteria,
        kind=kind,
        dependencies=dependencies,
        status=status,
        pending_question=pending_question,
        proof_progress=proof_progress,
        artifact_links=artifact_links,
        consolidation_links=consolidation_links,
        priority_score=priority_score,
        source_evidence=(title,),
        reasoning="remembered evidence",
    )


async def _write_valid_portfolio(tmp_path: Path) -> LocalResidentWorkItemBackend:
    backend = LocalResidentWorkItemBackend(tmp_path)
    completed = _objective(
        "completed",
        "Completed milestone",
        status=ResidentObjectiveStatus.COMPLETED.value,
        proof_progress=("done",),
    )
    ready = _objective(
        "ready",
        "Ready next objective",
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
    )
    await backend.write_objective(completed)
    await backend.write_objective(ready)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=MANDATE,
            objectives=(completed, ready),
            wake_record_links=("resident/wakeful/cycles/20260620T000000Z-1.md",),
            artifact_links=("resident/domain-expert/artifacts/proof.md",),
            consolidation_links=(
                "resident/domain-expert/consolidations/20260620T000000Z-proof.md",
            ),
        )
    )
    return backend


@pytest.mark.asyncio
async def test_valid_portfolio_passes_validation(tmp_path: Path) -> None:
    backend = await _write_valid_portfolio(tmp_path)
    validator = ResidentPortfolioValidator(backend=backend)

    report = await validator.validate(MANDATE)

    assert report.verdict == "valid"
    assert report.issues == ()
    assert report.selected_objective is not None


@pytest.mark.asyncio
async def test_missing_required_fields_are_reported(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective(
            "missing-purpose",
            "Missing purpose",
            purpose="",
            proof_criteria=(),
        )
    )
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(issue.code == "missing_required_field" for issue in report.issues)


@pytest.mark.asyncio
async def test_invalid_status_is_reported(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(_objective("bad-status", "Bad status", status="waiting-ish"))
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(issue.code == "invalid_status" for issue in report.issues)


@pytest.mark.asyncio
async def test_missing_dependencies_are_reported(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective("dependent", "Dependent objective", dependencies=("missing",))
    )
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(issue.code == "missing_dependency" for issue in report.issues)
    assert any("dependencies missing" in reason for reason in report.skipped_reasons)


@pytest.mark.asyncio
async def test_completed_objective_without_proof_is_reported(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective("done", "Done", status=ResidentObjectiveStatus.COMPLETED.value)
    )
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(issue.code == "completed_without_proof" for issue in report.issues)


@pytest.mark.asyncio
async def test_needs_operator_without_question_is_reported(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective(
            "ask",
            "Ask operator",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
        )
    )
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(issue.code == "operator_question_missing" for issue in report.issues)


@pytest.mark.asyncio
async def test_active_or_paused_objective_without_resume_reason_warns(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective(
            "active-without-reason",
            "Active without reason",
            status=ResidentObjectiveStatus.ACTIVE.value,
            purpose="Advance work.",
        ).with_updates(source_evidence=(), reasoning="", priority_rationale="")
    )
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(warning.code == "resume_reason_missing" for warning in report.warnings)


@pytest.mark.asyncio
async def test_cancelled_or_superseded_objective_without_audit_context_warns(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective(
            "cancelled-without-audit",
            "Cancelled without audit",
            status=ResidentObjectiveStatus.CANCELLED.value,
        ).with_updates(source_evidence=(), reasoning="")
    )
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(warning.code == "audit_context_missing" for warning in report.warnings)


@pytest.mark.asyncio
async def test_implausible_portfolio_links_warn(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(_objective("ready", "Ready"))
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=MANDATE,
            wake_record_links=("elsewhere/wake.txt",),
            artifact_links=("resident/domain-expert/artifacts/proof.txt",),
            workstream_links=("resident/domain-expert/workstreams/proof.md",),
            consolidation_links=("resident/domain-expert/consolidations/proof.md",),
        )
    )

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert (
        sum(1 for warning in report.warnings if warning.code == "implausible_portfolio_link") == 2
    )


@pytest.mark.asyncio
async def test_duplicate_objective_ids_are_reported(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(_objective("duplicate", "First duplicate"))
    duplicate_dir = tmp_path / "resident" / "portfolio" / "objectives"
    duplicate_dir.mkdir(parents=True, exist_ok=True)
    (duplicate_dir / "second-duplicate.md").write_text(
        """# Second duplicate

- id: duplicate
- status: candidate
- kind: research
- priority_score: 0
- priority_band: normal
- budget_estimate: small

## Purpose

Duplicate.

## Serves Mandate Because

It advances the mandate.

## Expected Outcome

Proof exists.

## Proof Criteria

- proof exists
""",
        encoding="utf-8",
    )
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert any(issue.code == "duplicate_objective_id" for issue in report.issues)
    assert any(
        "duplicate objective ids" in hint for hint in report.stale_duplicate_superseded_hints
    )


@pytest.mark.asyncio
async def test_dry_run_selection_matches_portfolio_prioritization(tmp_path: Path) -> None:
    backend = await _write_valid_portfolio(tmp_path)
    objectives = tuple(await backend.list_objectives(MANDATE))
    expected = select_objectives(
        prioritize_objectives(objectives, mandate=MANDATE),
        max_selected=1,
        max_active=3,
    )[0]

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    assert report.selected_objective is not None
    assert report.selected_objective.objective_id == expected.id
    assert report.selected_objective.priority_rationale == expected.priority_rationale


@pytest.mark.asyncio
async def test_dry_run_does_not_mutate_stored_objectives(tmp_path: Path) -> None:
    backend = await _write_valid_portfolio(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "resident").rglob("*.md"))
    }

    await ResidentPortfolioValidator(backend=backend).validate(MANDATE)

    after = {
        path.relative_to(tmp_path): path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "resident").rglob("*.md"))
    }
    assert after == before


@pytest.mark.asyncio
async def test_report_explains_blocked_and_operator_needed_work(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(_objective("blocked", "Blocked", dependencies=("not-complete",)))
    await backend.write_objective(
        _objective(
            "explicitly-blocked",
            "Explicitly blocked",
            status=ResidentObjectiveStatus.BLOCKED.value,
        )
    )
    await backend.write_objective(
        _objective(
            "ask",
            "Ask operator",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
            pending_question="Which option should proceed?",
        )
    )
    await backend.write_objective(_objective("not-complete", "Not complete"))
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(backend=backend).validate(MANDATE)
    rendered = render_validation_report(report)

    assert report.blocked_objectives
    assert {item.objective_id for item in report.blocked_objectives} >= {
        "blocked",
        "explicitly-blocked",
    }
    assert report.operator_needed_objectives
    assert any("dependencies incomplete" in reason for reason in report.skipped_reasons)
    assert any("operator input needed" in reason for reason in report.skipped_reasons)
    assert "Operator Needed Objectives" in rendered


@pytest.mark.asyncio
async def test_report_explains_eligible_work_not_selected(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective(
            "high-leverage",
            "High leverage",
            kind=ResidentObjectiveKind.TOOL_BUILDING.value,
        )
    )
    await backend.write_objective(_objective("research-next", "Research next"))
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))

    report = await ResidentPortfolioValidator(
        backend=backend,
        max_selected=1,
    ).validate(MANDATE)

    assert report.selected_objective is not None
    assert len(report.eligible_objectives) == 2
    assert any(
        "not selected: dry-run selection budget filled" in reason
        for reason in report.skipped_reasons
    )


def test_resident_portfolio_validator_contains_no_domain_specific_playbook_terms() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("src/ravn/resident_portfolio").glob("*.py"))
    ).casefold()

    for forbidden in (
        "kanuck",
        "inventory",
        "3d printing",
        "forge",
        "prd",
        "srd",
        "blender",
        "slicing",
        "product catalog",
    ):
        assert forbidden not in source
