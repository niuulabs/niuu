# NIU-1112 review

## Verdict

**needs_changes**

## Findings

### Major — no green required verification exists for the reviewed head

The delivery requires a green current-head CI run before NIU-1112 can be
completed. `10-implementation.md:203-222` records the latest full command and
six failures; it predates the reviewed commit `5a6f2b00`. GitHub has no
workflow run for `5a6f2b00`, and the focused suites do not replace the required
full verification. Commit the outstanding test work, then run the required
current-head CI/full command successfully (or resolve its blockers with their
owners) before closing this ticket.

### Major — the end-to-end acceptance test is not committed on `regin`

The prior requested ambiguous-gate follow-up is satisfied in committed code:
`src/ting/api/a2a.py:1613-1642` rejects an unqualified reply with multiple
pending gates, and `tests/test_ting/test_a2a_tasks.py:1646-1659` covers it.

The other prior request is now implemented in the working tree at
`tests/test_ting/test_a2a_tasks.py:1666-1816`: it exercises both decisions via
Ting's HTTP adapter, Skuld's runtime, and the campaign projector through
`TASK_STATE_COMPLETED`. However, `git status` shows that file as modified and
`HEAD` does not contain the test. Commit it before treating that acceptance
criterion as delivered; the test cannot be release evidence while it is only a
local worktree change.

## Verification

Reviewed the live `regin` branch at `5a6f2b00e7875678c9cc62023c160f9ba6aa489d`,
equal to `origin/regin`; `origin/dev` (`f75d5b0b`) is an ancestor. Inspected
`git diff origin/dev..HEAD`, the current implementation, and the prior review
artifact.

Passed locally:

```sh
uv run --extra dev --extra otel pytest -q \
  tests/test_ting/test_a2a_card.py tests/test_ting/test_a2a_tasks.py \
  tests/test_ting/test_system_workflows.py
# 114 passed

uv run --extra dev ruff check src/ting/api/a2a.py tests/test_ting/test_a2a_tasks.py
uv run --extra dev ruff format --check src/ting/api/a2a.py tests/test_ting/test_a2a_tasks.py
git diff --check
# all passed
```

`gh run list --repo niuulabs/niuu --branch regin --commit
5a6f2b00e7875678c9cc62023c160f9ba6aa489d` returned no runs.

## Recommendation

Do not close NIU-1112. Commit the new end-to-end test and provide a green
current-head full-suite/CI result; then re-request review.
