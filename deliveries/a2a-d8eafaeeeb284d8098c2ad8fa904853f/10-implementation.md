# NIU-1147 implementation record

## Change

- Bumped Tool & Skill Builder from `1.0.3` to `1.0.4`.
- Added the `capability-blocked` human-approval gate and a `capability.blocked` edge from `capability-publish`.
- Retained the `capability.ready` edge to `capability-complete`.
- Added an execution regression that passes the real publisher persona outcome through Ravn's resolver, Skuld's workflow broker, and Ting's campaign-to-A2A projection.
- Added the new bundled workflow document revision to the version lock.

## Verification

Passed:

```text
uv run pytest -q tests/test_ting/test_tool_skill_builder_execution.py tests/test_ting/test_system_workflows.py
26 passed

uv run ruff check tests/test_ting/test_tool_skill_builder_execution.py tests/test_ting/test_system_workflows.py
All checks passed!
```

The execution test specifically proves both outcomes:

- A `blocked` publisher result with the original Mímir workload-identity diagnosis publishes `capability.blocked`, activates the bundled `capability-blocked` help-needed gate, retains the causal text, and projects to `TASK_STATE_INPUT_REQUIRED` with no A2A artifact or `outcome_rejected` error.
- A `published` result still publishes `capability.ready` and reaches the bundled `capability-complete` terminal event.

Known pre-existing verification failure:

```text
uv run pytest -q tests/test_ting/test_bundled_workflow_versions_lock.py
1 failed, 2 passed
Bundled workflow content changed for an already-released version:
['3f444455-6b63-53c2-b057-3205345a56da@1.0.0']
```

This failure is for the unrelated Tracker Delivery Flow workflow (`3f444455-6b63-53c2-b057-3205345a56da`) and is present without changes to that workflow in this delivery.

## Lock-test provenance

The untouched parent of the original implementation commit was checked in a detached worktree:

```text
parent: dee8c8519318ac45eaa2d168f30e3d921140adab (0feba9c6^)
uv run pytest -q tests/test_ting/test_bundled_workflow_versions_lock.py
2 failed, 1 passed
```

Its first failure was the same unrelated Tracker Delivery Flow mismatch:

```text
Bundled workflow content changed for an already-released version:
['3f444455-6b63-53c2-b057-3205345a56da@1.0.0']
```

The parent's second failure was the expected absence of the then-current Tool & Skill Builder `1.0.3` lock entry; this delivery bumps that workflow to `1.0.4` and adds its new revision. On the amended `regin` commit, the complete lock test is `1 failed, 2 passed`, with only the pre-existing Tracker Delivery Flow mismatch remaining.

## Branch and PR

Implementation was made directly on the checked-out `regin` branch as required. GitHub cannot create a pull request with `regin` as both head and base; no other source branch was created to preserve the branch constraint. This delivery is therefore a direct `regin` commit, not a hosted PR.
