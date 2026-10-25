# NIU-1111 implementation

## Change

Extracted `_load_campaign_artifact_pages` from Ting's research artifact loader. It centralizes authorized Mímir page loading, campaign-prefix discovery, explicit workflow artifact paths, missing-file handling, deterministic ordering, and service credential forwarding for scoped A2A callers.

`WorkflowTaskHandler._attach_artifacts` now uses that loader. Declared `artifactPaths` remain an exact ownership boundary; legacy campaigns retain only their campaign prefix and configured machine-artifact probes. The existing authenticated campaign-artifact route remains the sole URL target and no authorization behavior was changed.

The configured `a2a.inline_artifact_max_chars` setting continues to select inline `text` Parts at the inclusive boundary and URL Parts above it. The existing tool-builder `learned_tool.json` output is returned from a completed task.

## Tests

- Added an a2a-sdk fixture using an ASGI transport; it reads both inline and URL Artifact Parts from a completed tool-builder task.
- Changed artifact-size coverage to test exactly at and one character over the configured threshold.
- Passed focused verification:

```text
.venv/bin/pytest -q tests/test_ting/test_a2a_tasks.py tests/test_ting/test_research_api.py tests/test_ting/test_gbrain_session_well.py
113 passed in 5.05s

uv run --extra dev --extra otel ruff check src/ tests/
All checks passed!
uv run --extra dev --extra otel ruff format --check src/ tests/
2813 files already formatted
```

- `git diff --check` passed.

## Handoff

PR: https://github.com/niuulabs/niuu/pull/1174 (base `regin` only).
