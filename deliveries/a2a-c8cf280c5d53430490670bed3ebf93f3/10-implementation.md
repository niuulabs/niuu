# NIU-1112 reconciliation verification

## Acceptance evidence

At `cbcd21fdf56152fcb1b201495b2c5fb53966af6d` (`origin/regin`), the focused
current-head command completed successfully:

```sh
uv run --extra dev --extra otel pytest -q \
  tests/test_ting/test_a2a_tasks.py::TestGateContinuation \
  tests/test_ting/test_a2a_card.py::TestAgentCard::test_skill_description_explains_input_required_gate_replies
# 11 passed in 3.32s
```

The parameterized completion test exercises both `approve` and
`request_changes` through Ting's A2A HTTP adapter and Skuld's
`human_approval` gate to `TASK_STATE_COMPLETED`. The same suite also asserts
an invalid JSON-RPC reply for a task that is not awaiting input. Ruff check and
format check passed for the Ting A2A source and these tests.

## Hosted CI evidence

PR #1173's final tested head was
`079b2be10ca7c922c8fb71831a3b76163e33865a`; its pull-request CI completed
successfully before merge. The direct GitHub check-runs response contains 56
completed checks: 54 `success` and two intentionally `skipped` manifest jobs.
The full per-check status, timestamps, and log URLs are preserved in
[`ci-checks-pr-1173-head.json`](ci-checks-pr-1173-head.json).

Relevant completed job logs:

- [CI / Test Ting](https://github.com/niuulabs/niuu/actions/runs/40172479480/job/121106183110) — success
- [CI / Integration Test Ting](https://github.com/niuulabs/niuu/actions/runs/40172479480/job/121106182844) — success
- [CI / ci-gate](https://github.com/niuulabs/niuu/actions/runs/40172479480/job/121107508943) — success
- [Forge Stability](https://github.com/niuulabs/niuu/actions/runs/40172479456) — four completed success jobs

The merge commit has zero check-runs because these CI runs were triggered by
`pull_request` on the final PR head, not because an aggregate status is still
pending. The `regin` branch has no GitHub required-status-check protection
endpoint configured. The PR's final tested head, not the merge commit, is the
relevant required-check evidence for #1173.

## Tracker correction

The earlier `In Progress` tracker state was stale after the merged and green
reconciliation PR. This delivery records the live evidence before moving
NIU-1112 to Done; no new product code or unrelated CI change was made.
