"""Tests for scoped Valkyrie autonomy policy and proposal storage."""

from __future__ import annotations

import pytest

from ravn.context.autonomy import (
    AutonomyPolicy,
    JsonProposalStore,
    ProposalStatus,
    SelfImprovementProposal,
)


def _proposal(**overrides) -> SelfImprovementProposal:
    data = {
        "proposal_id": "prop-1",
        "title": "Improve k8s probe",
        "artifact_type": "skill",
        "action": "create",
        "content": "Create a better kubectl probe.",
        "scope": "environment",
        "environment_id": "cluster-a",
        "mode": "guarded",
        "risk_class": "low",
    }
    data.update(overrides)
    return SelfImprovementProposal(**data)


def test_same_proposal_changes_decision_by_mode() -> None:
    policy = AutonomyPolicy()

    guarded = policy.decide(_proposal(mode="guarded"))
    autonomous = policy.decide(_proposal(mode="autonomous"))
    yolo = policy.decide(_proposal(mode="yolo"))

    assert guarded.decision == "needs_approval"
    assert autonomous.decision == "allow"
    assert yolo.decision == "allow"


def test_yolo_allows_domain_but_gates_shared_and_global() -> None:
    policy = AutonomyPolicy()

    assert policy.decide(_proposal(mode="yolo", scope="domain")).decision == "allow"
    assert policy.decide(_proposal(mode="yolo", scope="shared")).decision == "needs_approval"
    assert policy.decide(_proposal(mode="yolo", scope="global")).decision == "needs_approval"


def test_risky_boundaries_remain_gated_without_delegation() -> None:
    policy = AutonomyPolicy()

    decision = policy.decide(
        _proposal(
            mode="yolo",
            risk_boundaries=["credentials"],
            content="Update credential handling.",
        )
    )
    delegated = policy.decide(
        _proposal(
            mode="yolo",
            risk_boundaries=["credentials"],
            delegated_capabilities=["credentials"],
        )
    )

    assert decision.decision == "needs_approval"
    assert "credentials" in decision.reason
    assert delegated.decision == "allow"


@pytest.mark.asyncio
async def test_json_store_records_apply_and_rollback(tmp_path) -> None:
    store = JsonProposalStore(tmp_path / "proposals.json")
    proposal = _proposal(mode="yolo")
    stored = store.record_decision(proposal, AutonomyPolicy().decide(proposal))

    async def _apply(_: SelfImprovementProposal):
        return {"skill": "k8s probe"}, {"before": {}}

    async def _rollback(_: SelfImprovementProposal) -> None:
        return None

    applied = await store.apply(stored.proposal_id, _apply)
    applied_status = applied.status
    rolled_back = await store.rollback(stored.proposal_id, _rollback)
    reloaded = JsonProposalStore(tmp_path / "proposals.json").get(stored.proposal_id)

    assert applied_status == ProposalStatus.APPLIED.value
    assert rolled_back.status == ProposalStatus.ROLLED_BACK.value
    assert reloaded.status == ProposalStatus.ROLLED_BACK.value
    assert reloaded.applied_artifact_refs["skill"] == "k8s probe"


def test_negated_disclaimers_do_not_trip_gated_boundaries() -> None:
    policy = AutonomyPolicy()

    decision = policy.decide(
        _proposal(
            mode="yolo",
            content=(
                "Inspect pod events and memory pressure.\n"
                "This probe performs no destructive operations, never touches "
                "credentials, and avoids any spending or external send."
            ),
        )
    )
    assert decision.decision == "allow"


def test_imperative_prose_mentions_still_gate() -> None:
    policy = AutonomyPolicy()

    decision = policy.decide(
        _proposal(mode="yolo", content="Rotate the credentials in vault, then retry.")
    )
    assert decision.decision == "needs_approval"
    assert "credentials" in decision.reason


def test_action_declared_boundary_gates() -> None:
    policy = AutonomyPolicy()

    decision = policy.decide(_proposal(mode="yolo", action="external_send"))
    assert decision.decision == "needs_approval"
    assert "external_send" in decision.reason


def test_boundary_terms_match_whole_words_only() -> None:
    policy = AutonomyPolicy()

    decision = policy.decide(
        _proposal(mode="yolo", content="Try suspending the rollout and inspect again.")
    )
    assert decision.decision == "allow"
