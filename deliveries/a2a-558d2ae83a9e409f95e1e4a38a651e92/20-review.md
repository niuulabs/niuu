# NIU-1151 — review

## Verdict

`needs_changes`

## Re-check status

The prior requested default-kubeconfig follow-up is satisfied. With neither an
explicit kubeconfig path nor `KUBECONFIG`, the out-of-cluster path now calls
Kubernetes with `config_file=None` and `context=None`; the added regression
covers that behavior.

## Finding

### Major — multi-context kubeconfigs break existing calls without `cluster`

- `src/ravn/adapters/tools/kubernetes.py:217-220` now rejects an out-of-cluster
  request with no `cluster` whenever its kubeconfig has more than one context.
  Before this change, `load_kube_config(..., context=None)` selected the
  kubeconfig's `current-context`, so existing callers of every pre-existing
  action (`inspect_pod`, events, logs, workloads, and nodes) worked without a
  new argument. The newly added test at
  `tests/test_ravn/test_kubernetes_inspect_tool.py:301-312` codifies that
  backwards-incompatible error. This violates the ticket requirement that
  existing callers remain unchanged.

  Preserve the default/current-context behavior when `cluster` is omitted;
  use the new `cluster` parameter only to override that selection. Add a
  regression test using a multi-context kubeconfig with `current-context`
  asserting a legacy no-`cluster` request reaches that context. Explicit
  unknown clusters should remain errors.

## Verification

- Re-checked the committed repo-root branch `regin` at `8aeca20d`; it matches
  `origin/regin`. Full delivery diff reviewed: `01af0978..8aeca20d`.
- Ran `uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py`:
  **226 passed**.
- Ran `uv run ruff check src/ravn/adapters/tools/kubernetes.py
  src/ravn/config.py src/ravn/adapters/tools/builtin_registry.py
  tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py`: **All checks passed**.
- Live cluster access remains unverified: `kubectl` and `tsh` are unavailable
  in this workspace, and the implementation makes no claim otherwise.

## Recommendation

Restore legacy current-context selection for omitted `cluster`, cover it, and
request another review. The prior default-kubeconfig follow-up is verified as
satisfied.
