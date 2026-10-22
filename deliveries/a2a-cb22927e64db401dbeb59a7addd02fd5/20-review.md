# Review — NIU-1140

## Verdict

`needs_changes`

## Findings

### Critical

- **src/ting/workflows/tracker-delivery-flow.yaml:83** — The PR is not mergeable into its
  declared `regin` target. GitHub reports `mergeable: false`, `mergeable_state: dirty`;
  a three-way merge against `origin/regin` conflicts in this workflow, `src/skuld/broker.py`,
  and the corresponding tests. `regin` already contains commit `6ef7b724` implementing the
  same blocked-outcome path. Rebase on the current `origin/regin`, resolve the conflicts while
  retaining the accepted path, then update the PR (or close it as superseded if no unique change
  remains).

### Major

- **src/skuld/broker.py:2555** — This replaces the existing gate-summary support for
  `fields.rationale` with `fields.reason` only. Generic workflow outcomes that still emit
  `rationale` will now reach commissioners without their explanation; the adjacent regression
  test was changed to stop exercising that contract. Preserve both fields (prefer the new
  `reason` if present, otherwise `rationale`) and add assertions for each representation.

## Verification

- Reviewed PR #1166 at head `3a460cd6` against current `origin/regin` (`6ef7b724`).
- Ran `git merge-tree $(git merge-base origin/regin HEAD) origin/regin HEAD`; it reported the
  conflicts above.
- GitHub PR metadata reports `mergeable: false` and `mergeable_state: dirty`.
- `uv run pytest -q tests/test_ting/test_system_workflows.py tests/test_ravn/test_drive_loop_outcome_contract.py tests/test_skuld/test_broker.py`: 426 passed, with 11 existing dependency deprecation warnings.
- `uv run ruff check src/skuld/broker.py tests/test_ting/test_system_workflows.py tests/test_ravn/test_drive_loop_outcome_contract.py tests/test_skuld/test_broker.py`: passed.

## Recommendation

Do not merge this PR until it is rebased and the reason/rationale regression is corrected.
