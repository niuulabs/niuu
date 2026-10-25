# NIU-1151 — review

## Verdict

`needs_changes`

## Re-check status

The prior requested multi-context current-context follow-up is satisfied. With
an explicit single kubeconfig, an omitted `cluster` now resolves its
`current-context`; the new regression test verifies a legacy workload request
uses `teleport.niuu.world-valhalla`. Explicit unknown clusters still fail.

## Finding

### Major — standard multi-file kubeconfig values no longer work

- `src/ravn/adapters/tools/kubernetes.py:202` treats `kubeconfig_path` (or the
  configured `KUBECONFIG` environment value) as one filesystem path and calls
  `Path(...).read_text()`. Kubernetes accepts a path-list here (colon-separated
  on this platform) and the prior implementation passed that value to
  `kubernetes_asyncio.config.load_kube_config`, whose `KubeConfigMerger`
  splits and merges it. Consequently any existing out-of-cluster caller using
  the standard `KUBECONFIG=/path/one:/path/two` form now gets
  `FileNotFoundError` for the combined literal path before any Kubernetes
  client is built. This violates the required unchanged behavior for existing
  callers.

  Preserve Kubernetes' multi-file kubeconfig parsing when listing/resolving
  contexts (for example, use `kubernetes_asyncio.config.list_kube_config_contexts`
  with the supplied config-file string rather than reading it as one `Path`),
  and add a regression using two kubeconfig files and an omitted `cluster`.
  It should select the merged kubeconfig's current context and reach the
  injected client factory.

## Verification

- Re-checked committed repo-root branch `regin` at `a42f11bb`; it matches
  `origin/regin`. Reviewed the full delivery diff `01af0978..a42f11bb` and the
  re-check commit.
- Verified the prior requested current-context behavior at
  `src/ravn/adapters/tools/kubernetes.py:216-228` and
  `tests/test_ravn/test_kubernetes_inspect_tool.py:301-313`.
- Reproduced this finding with two valid colon-separated kubeconfig files:
  `kubernetes_inspect` returned `FileNotFoundError` for their combined path
  before the injected client factory ran. `kubernetes_asyncio`'s installed
  `KubeConfigMerger` splits that same config-file value on the platform path
  separator.
- Ran `uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py`:
  **227 passed**.
- Ran `uv run ruff check src/ravn/adapters/tools/kubernetes.py
  src/ravn/config.py src/ravn/adapters/tools/builtin_registry.py
  tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py`: **All checks passed**.
- Ran a UTF-8 tail property smoke test; all selected suffixes encoded within
  their configured byte bound.
- Live cluster access remains unverified: `kubectl` and `tsh` are unavailable
  in this workspace, and the implementation makes no claim otherwise.

## Recommendation

Restore standard multi-file kubeconfig handling, cover it, and request another
review. The prior multi-context current-context follow-up is verified as
satisfied.
