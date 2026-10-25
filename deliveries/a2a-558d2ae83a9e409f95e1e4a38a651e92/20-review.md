# NIU-1151 — review

## Verdict

`needs_changes`

## Finding

### Major — default kubeconfig callers no longer work

- The prior UTF-8 byte-bound finding is fixed: `src/ravn/adapters/tools/kubernetes.py:388-395`
  trims leading continuation bytes, and the new regression asserts the result
  is no larger than the selected cap.
- `src/ravn/adapters/tools/kubernetes.py:198-205` now rejects every
  out-of-cluster invocation when both `kubeconfig_path` and `KUBECONFIG` are
  unset.  Before this change `config.load_kube_config(config_file=None)` used
  Kubernetes' normal default kubeconfig lookup.  This is a backwards-incompatible
  change to existing callers, contrary to the ticket's preservation requirement.
  Preserve the prior default-kubeconfig behavior (while still resolving contexts
  for explicit multi-cluster selection), and add coverage for an unset
  `KUBECONFIG` using the default config location.

## Verification

- Re-checked committed repo-root diff `01af0978..6989e44d` on branch `regin`;
  `HEAD` and `origin/regin` are both `6989e44d`.
- Ran `uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py`:
  **225 passed**.
- Ran `uv run ruff check src/ravn/adapters/tools/kubernetes.py
  src/ravn/config.py src/ravn/adapters/tools/builtin_registry.py
  tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py`: **All checks passed**.
- Directly verified that the current out-of-cluster path rejects an unset
  `KUBECONFIG` rather than allowing the prior default lookup. Live cluster
  verification remains unavailable because neither `kubectl` nor Teleport is
  installed in this workspace.

## Recommendation

Restore default-kubeconfig compatibility and add a regression test, then
request re-review. The prior UTF-8 bounded-tail follow-up is satisfied.
