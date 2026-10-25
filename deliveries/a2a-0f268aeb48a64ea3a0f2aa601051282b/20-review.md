# NIU-1111 review

## Verdict

**pass**

## Findings

No blocking, major, minor, or suggestion findings.

## Verification

- Reviewed the complete committed diff from fetched `origin/regin` at
  `08141a9339be1843c0f2ac1fbcc84f80a1e3c15d` to PR #1174 head
  `07e5bfeeee13bd3f1bb40b0f135e6c29ce293ce6` on
  `feat/niu-1111-a2a-task-artifacts`. The canonical PR base is `regin` and
  GitHub reports it mergeable.
- Confirmed the shared loader in `src/ting/api/research.py` is used by A2A and
  the existing research listing path. Completed tasks map each returned page to
  one Artifact and one Part; text at the configured inclusive limit is inline,
  while larger content is represented by the existing owner-authorized campaign
  artifact route. Declared paths remain exact; legacy campaigns retain scoped
  prefix discovery and configured machine-artifact probes.
- Passed locally at the reviewed head:

  ```text
  .venv/bin/pytest -q tests/test_ting/test_a2a_tasks.py \
    tests/test_ting/test_research_api.py tests/test_ting/test_gbrain_session_well.py
  113 passed in 5.52s

  uv run --extra dev --extra otel ruff check src/ting/api/a2a.py \
    src/ting/api/research.py tests/test_ting/test_a2a_tasks.py
  All checks passed!

  uv run --extra dev --extra otel ruff format --check src/ting/api/a2a.py \
    src/ting/api/research.py tests/test_ting/test_a2a_tasks.py
  3 files already formatted

  git diff --check origin/regin...HEAD
  ```

- The added a2a-sdk ASGI fixture reads both an inline `learned_tool.json` Part
  and an oversized URL Part. The threshold tests cover equality and one
  character above the configured limit. GitHub PR #1174 has successful CI,
  including `Python / Python: Test Ting`, `Python: Integration Test Ting`, and
  `ci-gate`.
- Tracker NIU-1111 was moved to **In Review** at review start.

## Recommendation

Approve PR #1174 for merge into `regin` only. Do not merge or promote this
change toward `dev` or `main`.
