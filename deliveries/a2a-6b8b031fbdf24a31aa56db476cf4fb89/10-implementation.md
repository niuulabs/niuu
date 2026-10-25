# NIU-1107 reconciliation implementation and verification

## Reconciliation result

At inspection, `origin/dev` was `f75d5b0b824df5e11741b4d9c2f5c64852b531fe` and `origin/regin`
was `5863eb613ef05c2427010d0a3c01cf80af15d10a`; `git merge-base --is-ancestor origin/dev
origin/regin` succeeded and `git rev-list --left-right --count origin/regin...origin/dev` returned
`44 0`. The completed implementation is therefore already present on `regin`; no dev-to-regin
merge was performed.

PR #1171 had become conflicting because its old merge base was
`b746f9d5bfbf261156cc5e2255684b4dc810c814`. This branch merged current `origin/regin`, resolving
the sole documentation conflict by retaining the current `regin` default-deny Agent Card origin
policy. The reconciled revision is `5f6e1c88` before this delivery-record commit.

## Focused verification on the reconciled revision

```text
uv run --extra dev pytest -q \
  tests/test_observatory/test_agent_directory.py \
  tests/test_observatory/test_app.py \
  tests/test_observatory/test_entity_discovery.py \
  tests/test_observatory/test_plugin.py \
  tests/test_niuu/test_agent_directory.py \
  tests/test_niuu/test_rest_instances.py \
  tests/test_volundr/test_session_response_a2a.py \
  tests/test_config.py \
  tests/test_charts/test_service_chart_entrypoints.py
313 passed in 5.47s

uv run ruff check [directory source, Volundr boundary, config, and focused test paths]
All checks passed!

cd web-next && corepack pnpm install --frozen-lockfile
cd web-next && corepack pnpm exec vitest run \
  packages/plugin-observatory/src/adapters/http.test.ts \
  packages/plugin-observatory/src/application/useAgents.test.tsx \
  packages/plugin-observatory/src/domain/agentDirectory.test.ts \
  apps/niuu/src/services.test.ts --coverage=false
4 files passed, 101 tests passed in 2.86s
```

The backend suite verifies unchanged entity registry behavior; local/Guild list-detail/filter
contracts; owner, tenant and Environment isolation; principal cache isolation; multi-instance
provenance and collision handling; partial warnings; Agent Card validation; and workflow-session
topology identity. The UI suite verifies aggregate decoding and topology association with
cluster/instance context. The first UI attempt found missing workspace dependencies (`vitest` not
found); the lockfile-respecting install above resolved that environmental prerequisite and the
rerun passed.

## PR and CI gate

Before reconciliation, PR head `a7f8a7022ac48990859d4596df94b52c768bfab8` had 54 successful and
two intentionally skipped checks, including `ci-gate`; its old evidence and full changed-file list
are published in [PR comment 6397210163](https://github.com/niuulabs/niuu/pull/1171#issuecomment-6397210163).
Those checks are not evidence for the reconciled head. This delivery pushes a new PR head and
requires its CI and reviewer gate before merge. No merge has been performed.
