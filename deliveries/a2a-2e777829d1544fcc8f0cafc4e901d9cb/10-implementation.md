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

## 2026-10-25 persona-adapter failure diagnosis

The original failure is reproducible in this coding worker on the exact two
reported node IDs, before the test fixture correction:

```sh
env -u NIUU_WORKLOAD_IDENTITY_TOKEN_FILE \
  uv run --extra dev --extra k8s --extra otel pytest -q \
  tests/integration/test_http_persona_adapter.py::test_round_trip_load \
  tests/integration/test_http_persona_adapter.py::test_round_trip_list_names
```

```text
FAILED tests/integration/test_http_persona_adapter.py::test_round_trip_load
E   assert None is not None
WARNING ravn.adapters.personas.http:http.py:209 HttpPersonaAdapter: request failed
for 'integ-coder': Client error '404 Not Found' for url
'http://testserver/api/v1/tokens/workload/exchange'

FAILED tests/integration/test_http_persona_adapter.py::test_round_trip_list_names
E   AssertionError: assert 'alpha-agent' in []
WARNING ravn.adapters.personas.http:http.py:241 HttpPersonaAdapter: list_names request failed:
Client error '404 Not Found' for url
'http://testserver/api/v1/tokens/workload/exchange'

2 failed in 0.36s
```

This worker has a non-empty projected service-account file at
`/var/run/secrets/kubernetes.io/serviceaccount/token` and an ambient
`NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL`. `WorkloadIdentityBearerTokenAuthAdapter`
uses that default path even when `NIUU_WORKLOAD_IDENTITY_TOKEN_FILE` is unset.
The in-process persona test application deliberately mounts only the personas
router, not the workload-token exchange route, so the ambient credential turns
an otherwise unauthenticated local ASGI request into the 404 above. This is a
test-fixture isolation failure, not a production persona-adapter change.

`tests/integration/test_http_persona_adapter.py` now explicitly removes the
ambient identity variables and replaces only the default token path for that
module. The fixture preserves tests which deliberately exercise explicit bearer
tokens or workload identity. With both ambient settings deliberately supplied,
the focused module passes:

```sh
NIUU_WORKLOAD_IDENTITY_TOKEN_FILE=/var/run/secrets/kubernetes.io/serviceaccount/token \
NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL=https://volundr.valhalla.asgard.niuu.world/api/v1/tokens/workload/exchange \
uv run --extra dev --extra k8s --extra otel pytest -q \
  tests/integration/test_http_persona_adapter.py
# 6 passed in 0.43s
```

### Clean-regin and dev comparison

Before this fixture-only correction:

```text
HEAD          66c3d35ee6beffee978ee28ee6a5adb64888b851
origin/regin  66c3d35ee6beffee978ee28ee6a5adb64888b851
origin/dev    f75d5b0b824df5e11741b4d9c2f5c64852b531fe
origin/dev is an ancestor of HEAD: yes

HEAD/dev blobs (all equal):
src/ravn/adapters/personas/http.py             d77d3054dcb6c961d61647443a22bfbc93b19716
src/niuu/adapters/outbound/http_auth.py        9b7fd3a06e911d9caa8d20e9795ef99054fcde14
tests/integration/test_http_persona_adapter.py 6d95a14ada16156899667d8aff0d562c202402b2
```

Therefore there is no `dev` repair missing from `regin` to merge. The
operator's clean-regin pass is consistent with a worker that has no mounted
service-account token; this worker does have one. Only `dev -> regin` has been
merged; nothing was merged outward.

### Current-head full-suite evidence

The runner initially lacked Helm, so chart tests failed with `FileNotFoundError`
for `helm`; Helm v3.14.0, the version installed by the repository CI workflow,
was then made available at `/tmp/niu-1112-tools/helm`. The full command was
rerun at current head with `dev`, `k8s`, and `otel` extras:

```sh
PATH=/tmp/niu-1112-tools:$PATH \
env -u NIUU_WORKLOAD_IDENTITY_TOKEN_FILE -u NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL \
  uv run --extra dev --extra k8s --extra otel pytest tests/ -q --tb=short \
  --cov=src --cov-report=term-missing --cov-fail-under=85
```

It reached the coverage gate (88.20%) but was not green: `101 failed, 27501
passed, 46 skipped, 232 deselected, 1 xfailed` in 30:12. The repaired persona
integration module passed in that run. The remaining failures include the
separate ambient-default-token assumptions in
`tests/test_ravn/test_platform_tools.py` (79 failures attempting the unmocked
`http://localhost:8080/api/v1/tokens/workload/exchange`), plus independent
SSH-lock, Niuu-shared-chart namespace, CLI Docker-availability, and static-tool
verification failures. They are outside NIU-1112 and were neither silenced nor
changed. NIU-1112 remains In Progress until current-head CI is green.

### 2026-10-25 unit-fixture follow-up

The full-suite log also showed that `tests/test_ravn/test_personas/test_http.py`
had the same ambient mounted-service-account leak as the ASGI integration
module. The persona unit fixture now removes ambient workload-identity settings
and redirects only the default service-account path. The platform-tools unit
fixture bypasses only its implicit default path while retaining its explicit
workload-token exchange test. Neither production adapter changed.

With a deliberately supplied `NIUU_WORKLOAD_IDENTITY_TOKEN_FILE` and exchange
URL, the affected suites pass, proving that the test fixtures—not the process
environment—control their credentials:

```sh
NIUU_WORKLOAD_IDENTITY_TOKEN_FILE=/tmp/niu-1112-workload-proof \
NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL=https://ambient.example/api/v1/tokens/workload/exchange \
  uv run --extra dev pytest -q \
  tests/test_adapters/test_http_auth.py \
  tests/test_ravn/test_personas/test_http.py \
  tests/test_ravn/test_platform_tools.py \
  tests/integration/test_http_persona_adapter.py
# 147 passed
```

The required full command was then rerun with the same hermetic environment:

```sh
PATH=/tmp/niu-1112-tools:$PATH \
env -u NIUU_WORKLOAD_IDENTITY_TOKEN_FILE -u NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL \
  uv run --extra dev --extra k8s --extra otel pytest tests/ -q --tb=short \
  --cov=src --cov-report=term-missing --cov-fail-under=85
```

It passed all formerly ambient-identity-affected suites and coverage (88.47%),
but is still not green: `6 failed, 27596 passed, 46 skipped, 232 deselected,
1 xfailed` in 29:28. Exact remaining node IDs are
`test_supervisor_releases_lock_at_deadline_after_controller_death`,
`test_native_login_runner_has_dedicated_namespace_and_scoped_rbac`, both
parameterizations of `TestStackUp::test_uses_native_lan_unless_external_host_is_configured`,
`TestRavnAgentToolUse::test_build_tool_reuses_persisted_commission_after_restart`,
and `TestStaticDefects::test_correct_code_is_clean`. Their raw errors are,
respectively: missing `ProcessLookupError`; `skuld` versus expected `default`
namespace; absent Docker; a persisted-operation-ID mismatch; and an unrelated
`__classdict__` static-defect report. No unrelated behavior was changed.

### 2026-10-25 review follow-up: ambiguous gates

Unqualified A2A replies no longer resolve an arbitrary gate when more than one
workflow gate is pending. `_select_pending_gate_id` now rejects that input with
JSON-RPC invalid params and directs callers to supply `metadata.gateId` or
`metadata.nodeId`; selector-qualified replies retain their existing behavior.

```sh
uv run --extra dev --extra otel pytest -q tests/test_ting/test_a2a_tasks.py
# 78 passed
uv run --extra dev ruff check src/ting/api/a2a.py tests/test_ting/test_a2a_tasks.py
uv run --extra dev ruff format --check src/ting/api/a2a.py tests/test_ting/test_a2a_tasks.py
git diff --check
# all passed
```

The ticket remains **In Progress**: the review-requested A2A-to-terminal-state
integration proof and a green current-head full suite are still outstanding.

### 2026-10-25 review revision: committed end-to-end acceptance coverage

The review-requested test is committed and pushed as
`06c298ac21b59e71d7f6d8bd55c956b202ffc667`
(`test(ting): cover A2A gate completion`). It parametrizes `approve` and
`request_changes`, activates a `human_approval` gate, sends the reply through
Ting's A2A HTTP adapter to Skuld's configured workflow gate, emits the terminal
workflow outcome, projects the activity back into the campaign, and asserts
the resulting A2A task is `TASK_STATE_COMPLETED`.

At that commit, focused verification is green:

```sh
uv run --extra dev --extra otel pytest -q \
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py \
  tests/test_ting/test_system_workflows.py
# 116 passed

uv run --extra dev ruff check src/ting/api/a2a.py tests/test_ting/test_a2a_tasks.py
uv run --extra dev ruff format --check src/ting/api/a2a.py tests/test_ting/test_a2a_tasks.py
git diff --check
# all passed
```

`CI` is configured for `pull_request` events only. Because this delivery is
required to be integrated directly on `regin`, no PR-backed GitHub Actions run
exists for `06c298ac`; `gh run list --repo niuulabs/niuu --branch regin --commit
06c298ac21b59e71d7f6d8bd55c956b202ffc667 --limit 10` returned no runs. The
recorded full suite remains non-green with the six unrelated failures above.
No unrelated repair or validation bypass was made, so green required
current-head verification remains a blocker and the ticket stays **In
Progress**.
