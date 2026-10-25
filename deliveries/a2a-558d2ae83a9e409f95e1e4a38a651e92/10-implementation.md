# NIU-1151 — implementation handoff

## Branch and change

- Start: `regin` at `01af0978`
- Target: `regin` only
- Implementation includes the established read-only multi-cluster ConfigMap
  inspector contract and adds bounded keyed tail selection.

`kubernetes_inspect(action="configmap")` now accepts:

- `key`: optional ConfigMap data key.  When present, only that key is returned.
- `tail_bytes`: optional trailing-byte selection for `key`; it is capped by
  `tools.kubernetes.max_configmap_tail_bytes` (default `4000`).

Unfiltered calls preserve the existing per-value prefix truncation and
`truncated_keys` response.  A keyed response reports its key in
`truncated_keys` when the selected value is truncated.  Kubernetes API errors,
including a missing ConfigMap name, continue to use the tool's existing error
response path.

## Verification

Executed successfully:

```text
uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py
224 passed in 1.58s

uv run ruff check src/ravn/adapters/tools/kubernetes.py src/ravn/config.py \
  src/ravn/adapters/tools/builtin_registry.py \
  tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py
All checks passed!
```

The live authorized read was not verified: `kubectl` is unavailable in this
workspace, so no claim is made about access to
`teleport.niuu.world-ymir/nats/valkyrie-ymir-k8s-config`.

## Hosted review

Pending commit, push, and PR creation against `regin`.
