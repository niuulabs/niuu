"""Shared helpers for resident delegation tests.

These were extracted from the (now removed) ``scripts/prove_resident_delegation``
proof harness so the test suite owns its own fixtures.
"""

from __future__ import annotations

from ravn.domain.resident_portfolio import (
    ResidentDelegationRecord,
    ResidentDelegationStatus,
    ResidentExecutionResult,
)

_LOCAL_BACKENDS = frozenset({"local-simulated", "local-subprocess"})


def _real_delegation_records(
    delegations: list[ResidentDelegationRecord],
) -> list[ResidentDelegationRecord]:
    return [
        delegation
        for delegation in delegations
        if delegation.backend_name not in _LOCAL_BACKENDS and delegation.backend_session_id
    ]


def _observed_real_results(
    real_delegations: list[ResidentDelegationRecord],
    observed_results: list[ResidentExecutionResult],
) -> list[ResidentExecutionResult]:
    real_session_ids = {delegation.backend_session_id for delegation in real_delegations}
    return [result for result in observed_results if result.session_id in real_session_ids]


def _successful_real_results(
    real_delegations: list[ResidentDelegationRecord],
    observed_results: list[ResidentExecutionResult],
) -> list[ResidentExecutionResult]:
    return [
        result
        for result in _observed_real_results(real_delegations, observed_results)
        if result.status == ResidentDelegationStatus.COMPLETED.value
        and (result.output_refs or result.findings)
    ]


def _cancelled_real_delegation_records(
    delegations: list[ResidentDelegationRecord],
) -> list[ResidentDelegationRecord]:
    return [
        delegation
        for delegation in _real_delegation_records(delegations)
        if delegation.status == ResidentDelegationStatus.CANCELLED.value
    ]


def _sample_delegation_result_pair(
    delegations: list[ResidentDelegationRecord],
    observed_results: list[ResidentExecutionResult],
) -> tuple[ResidentDelegationRecord, ResidentExecutionResult] | None:
    result_by_session = {result.session_id: result for result in observed_results}
    for delegation in delegations:
        result = result_by_session.get(delegation.backend_session_id)
        if result is not None:
            return delegation, result
    return None


def _successful_real_source_objective_ids(
    real_delegations: list[ResidentDelegationRecord],
    observed_results: list[ResidentExecutionResult],
) -> set[str]:
    successful_session_ids = {
        result.session_id for result in _successful_real_results(real_delegations, observed_results)
    }
    return {
        delegation.source_objective_id
        for delegation in real_delegations
        if delegation.backend_session_id in successful_session_ids
    }
