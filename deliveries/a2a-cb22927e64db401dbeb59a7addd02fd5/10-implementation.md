# NIU-1140 implementation

## Change

Rebased the delivery branch on `origin/regin`, which already supplies the
`delivery-blocked` gate and its `code.blocked -> code.blocked` edge. This retains the
accepted blocked-outcome route without duplicating or conflicting with the existing
`code.changed` review, merge, and delivery-record publication path.

Updated Skuld's workflow-gate summary extraction to use `fields.reason` when provided,
or `fields.rationale` when `reason` is absent. This preserves generic workflows that
emit the established `rationale` field while surfacing the newer delivery-blocked
reason to the commissioner. A parameterized broker regression test covers reason-only,
rationale-only, and both-field outcomes, asserting that reason wins when both exist.

## Verification

- Passed `uv run pytest -q tests/test_skuld/test_broker.py::TestBroker::test_routing_only_peer_outcome_can_activate_native_workflow_gate tests/test_ting/test_system_workflows.py tests/test_ravn/test_drive_loop_outcome_contract.py` (105 passed; 10 existing dependency deprecation warnings).
- Passed `uv run ruff check src/skuld/broker.py tests/test_skuld/test_broker.py tests/test_ting/test_system_workflows.py tests/test_ravn/test_drive_loop_outcome_contract.py`.
- Passed `git diff --check`.

## Handoff

PR: https://github.com/niuulabs/niuu/pull/1166

The PR is updated from a branch rebased on `regin`; its target remains `regin` only.
No merge was performed.
