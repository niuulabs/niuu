# NIU-1112 reconciliation handoff

## Existing implementation

The existing Ting A2A adapter in `src/ting/api/a2a.py` selects the pending
workflow gate, maps `approve` to `APPROVE` and `request_changes` to
`CHANGES_REQUESTED`, and sends the text-part content as `notes` through the
Volundr port. Skuld's workflow runtime resolves the configured
`approvalEvent` or `changesRequestedEvent`. Invalid task states and invalid
or ambiguous gate selections return JSON-RPC invalid-params errors.

The committed `TestGateContinuation.test_a2a_gate_reply_reaches_completed_campaign_via_skuld`
parameterizes both decisions and exercises Ting's A2A HTTP adapter, the Skuld
workflow runtime, and the campaign projector until `TASK_STATE_COMPLETED`.

## Verification at reconciled head

At `b746f9d5bfbf261156cc5e2255684b4dc810c814`:

```sh
uv run --extra dev --extra otel pytest -q \
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py \
  tests/test_ting/test_system_workflows.py
# 116 passed

uv run --extra dev ruff check src/ting/api/a2a.py src/ting/api/a2a_card.py \
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py
uv run --extra dev ruff format --check src/ting/api/a2a.py src/ting/api/a2a_card.py \
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py
git diff --check
# all passed
```

The last full-suite evidence at `b6639c1c` is still non-green despite 88.47%
coverage: 6 failures after `27,596 passed, 46 skipped, 232 deselected, 1
xfailed`. The named failures and raw causes are recorded in the prior delivery
handoff at `deliveries/a2a-2e777829d1544fcc8f0cafc4e901d9cb/10-implementation.md`.
They are outside the NIU-1112 implementation and no unrelated failures were
silenced. This reconciliation cannot truthfully mark the ticket done until the
PR checks are green.

## PR and status

This reconciliation uses a new branch based on `regin` and a PR targeting
`regin`; it does not push another implementation commit directly to `regin`.
Tracker remains `In Progress` pending green required checks and review.
