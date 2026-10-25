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
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py \
  tests/test_ting/test_system_workflows.py
# 113 passed

uv run --extra dev ruff check \
  src/ting/api/a2a_card.py tests/test_ting/test_a2a_card.py
uv run --extra dev ruff format --check \
  src/ting/api/a2a_card.py tests/test_ting/test_a2a_card.py
git diff --check
# all passed
```

A preliminary no-OTel invocation of the A2A test set produced one environment
failure (`ModuleNotFoundError: opentelemetry`) in the telemetry test; rerunning
with the required `otel` extra passed all 113 focused tests.

## Branch and CI evidence

- `origin/dev` and `regin` had diverged by 40/15 commits. `origin/dev` was
  merged into `regin` as `82f6384c` (`chore: merge dev into regin`), resolving
  the bundled workflow-version lock by retaining both independent version
  entries. The merge retained an older local formatting hunk in
  `test_system_workflows.py`; this delivery reapplies the exact current-dev Ruff
  formatting so repository-wide Ruff check and format verification pass.
- The latest `origin/dev` commit `f75d5b0b` had three successful GitHub Actions
  runs, including [CI](https://github.com/niuulabs/niuu/actions/runs/40153495545).
  This addressed the launch instruction to check whether the previously
  reported unrelated Ting/Ravn/lint failures were already fixed before merging.
- No hosted PR is applicable: the delivery is required to start and finish on
  `regin`, and no PR or merge targets `dev` or `main`.

## Full-command limitation

`make verify` cannot be invoked in this worker because `make` is not installed
(`/bin/bash: make: command not found`). The equivalent direct full-suite command
was attempted after repository-wide Ruff passed:

```sh
uv run pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-fail-under=85
```

Collection stopped before tests ran because the default environment does not
install the optional `k8s` dependency:
`ModuleNotFoundError: No module named 'kubernetes_asyncio'` from
`tests/test_adapters/test_user_home_files.py`. This is outside NIU-1112's
A2A/Ting scope. The focused A2A, Agent Card, and workflow suites and the
repository-wide Ruff checks above are the completed local verification.

After installing `k8s` and rerunning with `dev`, `k8s`, and `otel` extras,
the full suite collected 27,644 selected tests but had two unrelated early
failures in `tests/integration/test_http_persona_adapter.py`; the long run was
then stopped rather than consuming the worker on a known failing full suite.
The two failures reproduce independently in 0.34s: the HTTP persona adapter
tries `http://testserver/api/v1/tokens/workload/exchange`, receives 404, and
therefore returns no `integ-coder` configuration / no `alpha-agent` name.
No unrelated persona-adapter repair was made in this NIU-1112 delivery.
