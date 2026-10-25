# NIU-1112 review

## Verdict

**needs_changes**

## Findings

### Critical — required current-head verification is not green

`10-implementation.md:203-222` records the required full backend command at
current `regin` head and its result: six failures remain after 27,596 passes.
The delivery requires a green current-head CI run before completion, so focused
A2A success and 88.47% coverage cannot satisfy the merge/closure criterion.
Resolve the six failures in scope with their owners (or obtain a new green
current-head run after the approved repairs); do not mark this delivery done
until that evidence exists.

### Major — an ambiguous reply silently resolves the first pending gate

`src/ting/api/a2a.py:1613-1635` returns the first pending gate when neither
`metadata.gateId` nor `metadata.nodeId` is supplied. The documented reply
convention requires only `taskId` and `metadata.gateDecision`, so a workflow
with concurrent pending human-approval gates can apply a reviewer decision to
the wrong gate. Enumerate matching gates and reject ambiguous unqualified
replies with `InvalidParamsError`, requiring `gateId` or `nodeId`; add a
regression test with two pending gates and no selector.

### Major — the required A2A-to-completion proof is absent

`tests/test_ting/test_a2a_tasks.py:1563-1584` invokes a
`RecordingVolundrPort` and asserts only the optimistic `TASK_STATE_WORKING`
response. It does not exercise the production Ting HTTP adapter, Skuld's gate
translation, projector, or a terminal `COMPLETED` task. Add an integration/e2e
test that drives a `human_approval` workflow using only A2A messages through
completion for approval and covers the request-changes route; retain the
existing invalid-state JSON-RPC assertion.

## Verification

Reviewed `regin` at `b6639c1c7dc5b7f2107d9e8a716a8fc57545d47f`, equal to
`origin/regin`; `origin/dev` (`f75d5b0b`) is an ancestor. `git diff
origin/dev..HEAD` was inspected. The ticket-focused suites and changed
identity-isolation suites passed locally:

```sh
uv run --extra dev --extra otel pytest -q \
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py \
  tests/test_ting/test_system_workflows.py
# 113 passed

PATH=/tmp/niu-1112-tools:$PATH \
env -u NIUU_WORKLOAD_IDENTITY_TOKEN_FILE -u NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL \
  uv run --extra dev --extra k8s --extra otel pytest -q --tb=short \
  tests/test_ravn/test_platform_tools.py tests/test_ravn/test_personas/test_http.py \
  tests/integration/test_http_persona_adapter.py tests/test_ting/test_a2a_card.py \
  tests/test_ting/test_a2a_tasks.py tests/test_ting/test_system_workflows.py
# 246 passed
```

`ruff check`, `ruff format --check`, and `git diff --check` passed for the
reviewed A2A files. The full-suite result above remains the blocking evidence.

## Recommendation

Do not close or merge NIU-1112 yet. Address the ambiguous-gate handling and
add the end-to-end proof, then provide a green current-head full-suite/CI run.
