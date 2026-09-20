"""Engine code must stay free of any one workflow's vocabulary.

Fan-out and join, durable waits, result gates and session continuation are
generic workflow building blocks. A workflow's domain lives in its graph and its
personas, never in Python: a persona name, contract name or event type written
into engine code means the next workflow cannot reuse the block without copying
it. The first workflow built on these blocks was code delivery, so that is the
vocabulary this test looks for.

BURN_DOWN lists the files that still carry it. It may only shrink: a file
that is clean must be removed from the list, and no file may be added.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

_FEATURE_VOCABULARY = re.compile(
    r"developer[_.-]?(execution|deliver|workstream|integration|coordinat|child|review"
    r"|evidence|snapshot|completion|credential|a2a|plan|candidate)"
    r"|Developer(Execution|Delivery|Child|Coordinat|Credential|A2A|Evidence|Review"
    r"|Integration|Snapshot|Completion)"
    r"""|["']developer[-.][a-z]"""
)

BURN_DOWN: frozenset[str] = frozenset(
    {
        "src/niuu/domain/services/token_scope.py",
        "src/ravn/adapters/developer_a2a.py",
        "src/ravn/adapters/developer_delivery_http.py",
        "src/ravn/adapters/tools/builtin_registry.py",
        "src/ravn/adapters/tools/developer_delivery.py",
        "src/ravn/cli/tool_builders.py",
        "src/ravn/config.py",
        "src/ravn/domain/developer_delivery.py",
        "src/ravn/drive_loop.py",
        "src/ravn/ports/developer_delivery.py",
        "src/skuld/broker.py",
        "src/skuld/collaboration_adapter.py",
        "src/skuld/workflow_runtime.py",
        "src/ting/adapters/developer_delivery_observer.py",
        "src/ting/adapters/developer_evidence.py",
        "src/ting/adapters/developer_execution_worker.py",
        "src/ting/adapters/developer_integration_reviews.py",
        "src/ting/adapters/developer_reviews.py",
        "src/ting/adapters/parent_workflow_continuation.py",
        "src/ting/adapters/postgres_a2a_launches.py",
        "src/ting/adapters/postgres_developer_delivery_waits.py",
        "src/ting/adapters/postgres_developer_executions.py",
        "src/ting/api/a2a.py",
        "src/ting/api/developer_executions.py",
        "src/ting/api/workflows.py",
        "src/ting/config.py",
        "src/ting/domain/developer_delivery_wait.py",
        "src/ting/domain/developer_execution.py",
        "src/ting/domain/developer_execution_trace.py",
        "src/ting/domain/developer_snapshot.py",
        "src/ting/domain/services/activity_subscriber.py",
        "src/ting/domain/services/developer_completion.py",
        "src/ting/domain/services/developer_delivery_wait.py",
        "src/ting/domain/services/developer_execution.py",
        "src/ting/domain/services/workflow_campaign_projector.py",
        "src/ting/domain/workflow_document.py",
        "src/ting/main.py",
        "src/ting/ports/developer_delivery_wait.py",
        "src/ting/ports/developer_evidence.py",
        "src/ting/ports/developer_execution.py",
        "src/ting/system_workflows.py",
        "src/volundr/adapters/outbound/contributors/developer_execution_credentials.py",
        "src/volundr/adapters/outbound/contributors/ravn_flock.py",
        "src/volundr/adapters/outbound/delivery_authorization.py",
        "src/volundr/adapters/outbound/developer_credential_file.py",
        "src/volundr/adapters/outbound/developer_credential_k8s.py",
        "src/volundr/composition_builders.py",
        "src/volundr/config.py",
        "src/volundr/domain/services/developer_execution_credentials.py",
        "src/volundr/domain/services/session.py",
        "src/volundr/main.py",
        "src/volundr/ports/developer_execution_credentials.py",
    }
)


def _offenders() -> set[str]:
    return {
        path.relative_to(SRC.parent).as_posix()
        for path in SRC.rglob("*.py")
        if _FEATURE_VOCABULARY.search(path.read_text(encoding="utf-8"))
    }


def test_no_new_engine_file_carries_workflow_specific_vocabulary() -> None:
    unexpected = sorted(_offenders() - BURN_DOWN)
    assert not unexpected, (
        "Workflow-specific vocabulary in engine code. Move it into the workflow graph, "
        f"a persona document, or configuration: {unexpected}"
    )


def test_burn_down_list_only_names_files_that_still_offend() -> None:
    cleaned = sorted(BURN_DOWN - _offenders())
    assert not cleaned, f"These files are clean now; remove them from BURN_DOWN: {cleaned}"
