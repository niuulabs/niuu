"""Module-level constants for resident portfolio management."""

from __future__ import annotations

from ravn.domain.resident_portfolio import ResidentDelegationStatus

_PORTFOLIO_PATH = "resident/portfolio/portfolio.md"
_OBJECTIVE_PREFIX = "resident/portfolio/objectives"
_DECISION_PREFIX = "resident/portfolio/decisions"
_CAPABILITY_DISCOVERY_PREFIX = "resident/capability-discovery"
_DELEGATION_PREFIX = "resident/delegations"
_DELEGATION_RESULT_PREFIX = "resident/delegation-results"
_DELEGATION_REVIEW_PREFIX = "resident/delegation-reviews"
_OPERATOR_CONTACT_PREFIX = "resident/operator-contacts"
_DOMAIN_MODEL_REF = "resident/domain-expert/domain-model.md"
_WAKE_CYCLE_PREFIX = "resident/wakeful/cycles"
_WORKSTREAM_PREFIX = "resident/domain-expert/workstreams"
_ARTIFACT_PREFIX = "resident/domain-expert/artifacts"
_CONSOLIDATION_PREFIX = "resident/domain-expert/consolidations"
_DECISION_HISTORY_LIMIT = 40

_WORKFLOW_REFERENCE_PREFIX = "workflow-ref:"

_TERMINAL_DELEGATION_RESULT_STATUSES = frozenset(
    {
        ResidentDelegationStatus.COMPLETED.value,
        ResidentDelegationStatus.BLOCKED.value,
        ResidentDelegationStatus.FAILED.value,
        ResidentDelegationStatus.CANCELLED.value,
        ResidentDelegationStatus.NEEDS_OPERATOR.value,
        ResidentDelegationStatus.UNAVAILABLE.value,
    }
)

_LOCAL_WORKER_SCRIPT = r"""
import json
import sys

brief = sys.stdin.read()
lines = [line for line in brief.splitlines() if line.strip()]
payload = {
    "summary": f"Processed resident worker brief with {len(lines)} non-empty lines.",
    "findings": [
        "worker brief was executed by a local subprocess",
        f"brief characters: {len(brief)}",
    ],
    "follow_up_suggestions": [
        "Review local worker output and decide the next bounded resident objective"
    ],
}
print(json.dumps(payload, sort_keys=True))
"""
