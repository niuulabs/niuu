"""Workflow execution building blocks know nothing about what a workflow delivers.

Fan-out and join, durable waits, result gates and continuation serve any
workflow. Repositories, commits, merges and forge checks belong to the delivery
pack in ``ting.delivery``, which composes these blocks. A generic module that
mentions them cannot be reused by a workflow that produces anything else.

``BURN_DOWN`` lists the generic modules that still do. It may only shrink.
"""

from __future__ import annotations

import re
from pathlib import Path

TING = Path(__file__).resolve().parents[2] / "src" / "ting"

_GENERIC_MODULE = re.compile(
    r"(workflow_execution|workflow_wait|wait_condition|workflow_continuation"
    r"|execution_snapshot|child_evidence|child_reviews|parent_workflow_continuation)"
)

_DELIVERY_CONCEPT = re.compile(
    r"\b(repository_(url|path|root)|base_sha|base_ref|baseSha|baseRef|head_sha|headSha|merge[a-z_]*"
    r"|candidate[a-zA-Z_]*|integration[a-zA-Z_]*|forge[a-zA-Z_]*|review_number|reviewNumber"
    r"|target_branch|targetBranch|workspace[a-zA-Z_]*|commit[a-z_]*|allowed_paths"
    r"|allowedPaths|test_contract[a-z_]*|testContractIds)\b"
    r"""|["']repository["']|\.repository\b(?!\.)|\brepository: str"""
)

BURN_DOWN: frozenset[str] = frozenset(
    {
        "adapters/child_evidence.py",
        "adapters/parent_workflow_continuation.py",
        "adapters/postgres_workflow_executions.py",
        "adapters/postgres_workflow_waits.py",
        "adapters/wait_condition_observer.py",
        "api/workflow_executions.py",
        "domain/services/workflow_wait.py",
        "domain/workflow_continuation_events.py",
        "domain/workflow_execution_trace.py",
        "domain/workflow_wait.py",
        "ports/workflow_wait.py",
    }
)


def _generic_modules() -> list[Path]:
    return [
        path
        for path in TING.rglob("*.py")
        if "delivery" not in path.relative_to(TING).parts
        and not path.name.startswith("delivery_")
        and _GENERIC_MODULE.search(path.name)
    ]


def _offenders() -> set[str]:
    return {
        path.relative_to(TING).as_posix()
        for path in _generic_modules()
        if _DELIVERY_CONCEPT.search(path.read_text(encoding="utf-8"))
    }


def test_no_new_generic_module_carries_delivery_concepts() -> None:
    unexpected = sorted(_offenders() - BURN_DOWN)
    assert not unexpected, (
        f"Delivery concepts in a generic execution module; move them to ting.delivery: {unexpected}"
    )


def test_burn_down_list_only_names_modules_that_still_offend() -> None:
    cleaned = sorted(BURN_DOWN - _offenders())
    assert not cleaned, f"These modules are clean now; remove them from BURN_DOWN: {cleaned}"
