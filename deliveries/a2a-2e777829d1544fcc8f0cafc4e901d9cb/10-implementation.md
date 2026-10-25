# NIU-1112 implementation handoff

## Delivered behavior

`ting.api.a2a` rejects replies unless the persisted workflow campaign projects
to `TASK_STATE_INPUT_REQUIRED`. On a valid A2A reply it maps
`metadata.gateDecision` to `APPROVE` or `CHANGES_REQUESTED`, extracts text
parts as the reviewer comment, and calls `VolundrPort.resolve_workflow_gate`
with source `a2a`. The production HTTP adapter forwards that call to the
workflow-gate resolve route; Skuld selects the configured `approvalEvent` or
`changesRequestedEvent` from its workflow graph and publishes the resolved
outcome.

The existing focused JSON-RPC tests cover both decisions, review-note handling,
pending-gate selection, and clear invalid-state rejection. The A2A operator
guide documents the same contract. This delivery adds the previously missing
contract to every advertised Agent Card *skill* description, not only the card
summary, with a regression test.

## Verification

Passed at the integrated `regin` head:

```sh
uv run --extra dev --extra otel pytest -q \
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py
# 89 passed

uv run --extra dev ruff check \
  src/ting/api/a2a_card.py tests/test_ting/test_a2a_card.py
uv run --extra dev ruff format --check \
  src/ting/api/a2a_card.py tests/test_ting/test_a2a_card.py
git diff --check
# all passed
```

A preliminary no-OTel invocation of the same test set produced one environment
failure (`ModuleNotFoundError: opentelemetry`) in the telemetry test; rerunning
with the required `otel` extra passed all 89 tests.

## Branch and CI evidence

- `origin/dev` and `regin` had diverged by 40/15 commits. `origin/dev` was
  merged into `regin` as `82f6384c` (`chore: merge dev into regin`), resolving
  only the bundled workflow-version lock by retaining both independent version
  entries.
- The latest `origin/dev` commit `f75d5b0b` had three successful GitHub Actions
  runs, including [CI](https://github.com/niuulabs/niuu/actions/runs/40153495545).
  This addressed the launch instruction to check whether the previously
  reported unrelated Ting/Ravn/lint failures were already fixed before merging.
- No hosted PR is applicable: the delivery is required to start and finish on
  `regin`, and no PR or merge targets `dev` or `main`.
