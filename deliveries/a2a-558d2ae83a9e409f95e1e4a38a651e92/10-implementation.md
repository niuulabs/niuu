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

No PR was opened.  The task explicitly required landing directly on `regin` and
prohibited opening or targeting a PR to `dev` or `main`; a hosted PR cannot use
`regin` as both source and target.  Commits `6470b1f6` and `3eb3dd42` were
pushed to `origin/regin`.

## Review revision (2026-10-25)

The tail selector now discards leading UTF-8 continuation bytes when its byte
window begins in the middle of a code point, then decodes the remaining complete
suffix strictly. This avoids replacement characters that could expand the
returned UTF-8 payload beyond the configured/requested byte cap.

A focused ConfigMap test uses a tail window that starts in `€` and confirms the
returned suffix is `tail` and its UTF-8 encoding is no larger than the cap.

Revision verification:

```text
uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py
225 passed in 1.41s

uv run ruff check src/ravn/adapters/tools/kubernetes.py src/ravn/config.py \
  src/ravn/adapters/tools/builtin_registry.py \
  tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py
All checks passed!
```

## Review revision — default kubeconfig compatibility (2026-10-25)

Out-of-cluster callers with neither `tools.kubernetes.kubeconfig_path` nor
`KUBECONFIG` now again delegate to `kubernetes_asyncio` with
`config_file=None` and `context=None`. Kubernetes therefore performs its
standard default kubeconfig lookup and uses its current context, as it did
before the multi-cluster inspector change. Explicit kubeconfig paths and
named-cluster context resolution remain unchanged; named clusters without an
explicit path resolve through Kubernetes' default config contexts.

A regression test unsets `KUBECONFIG`, exercises an out-of-cluster workload
inspection, and asserts that the Kubernetes client factory receives both
`config_file=None` and `context=None`.

Verification:

```text
uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py
226 passed in 1.73s

uv run ruff check src/ravn/adapters/tools/kubernetes.py src/ravn/config.py \
  src/ravn/adapters/tools/builtin_registry.py \
  tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py
All checks passed!
```

Live authorization verification remains unavailable: `kubectl` and `tsh` are
not installed in this workspace. No hosted PR was opened; this ticket lands
directly on `regin` only.

## Review revision — multi-context current-context compatibility (2026-10-25)

The out-of-cluster inspector again preserves the kubeconfig's legacy
current-context behavior when `cluster` is omitted, including for a
multi-context kubeconfig. An explicit `cluster` continues to select a matching
context, and an unknown explicit value remains an error.

A regression test uses the multi-context Teleport fixture with
`current-context: teleport.niuu.world-valhalla`, performs a legacy workload
request without `cluster`, and verifies that the client is built for Valhalla.

Verification:

```text
uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py
227 passed

uv run ruff check src/ravn/adapters/tools/kubernetes.py src/ravn/config.py \
  src/ravn/adapters/tools/builtin_registry.py \
  tests/test_ravn/test_kubernetes_inspect_tool.py \
  tests/ravn/unit/test_builtin_registry.py
All checks passed!
```

Live authorization verification remains unavailable: neither `kubectl` nor
`tsh` is installed in this workspace. No hosted PR was opened; this ticket
lands directly on `regin` only.
