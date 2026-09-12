# Review — NIU-1119

## Verdict

needs_changes

## Findings

### Major

- `src/ravn/adapters/tools/build_tool.py:487` and `src/ravn/adapters/tool_build/a2a.py:1278` both increment `ravn_tool_build_total` / `ravn_tool_build_duration_seconds` for the same A2A build. The public metric is named as the total build count by backend/outcome, so A2A commissions will be double counted whenever the `BuildTool` wrapper invokes `A2AToolBuildBackend.build()`. Fix by recording the public `ravn_tool_build_*` metrics in exactly one layer (prefer the lifecycle wrapper for all backends, or remove/scope the backend-specific duplicate) and keep backend-internal diagnostics on a distinct metric name if needed.

## Verification notes

- Confirmed review branch `feat/niu-1119-a2a-observability` at commit `63b1f42d` with base `origin/dev` (`a09cf950`).
- Read `00-ticket.md` and `10-implementation.md`; no prior `20-review.md` existed.
- Moved tracker issue `NIU-1119` (`55616900-a2b6-48cf-9902-312418faf8c7`) to `In Review` via `./.skuld-tools/bin/tracker_issue update-status`.
- Inspected `git diff origin/dev..HEAD` and changed source/tests/charts.
- Attempted focused pytest suite with both `pytest` and `uv run pytest`; this runtime lacks both executables, so tests could not be rerun here.

## Recommendation

Address the duplicate public tool-build metric emission, then rerun the focused suite from the implementation notes before re-review.
