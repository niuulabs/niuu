# Review — NIU-1150 (re-check)

## Verdict

**needs_changes**

## Findings

1. **Major — `src/ravn/adapters/tools/a2a_task.py:339` fingerprints starts using the caller's URL instead of the resolved canonical agent id.**
   A URL-form start resolves to the canonical Guild entry and persists that canonical
   id (`:408`), but its active-task lookup and persisted `request_fingerprint` still
   contain the URL. A subsequent identical start using the canonical id computes a
   different fingerprint and bypasses the existing duplicate-start guard, creating a
   second live task for the same peer/skill/prompt. This is reachable in the resident
   daemon, which provides `activity_finder`. Normalize the fingerprint input to
   `agent.id` after resolution, and add a regression that starts via a visible card URL
   then asserts an equivalent canonical-id start is rejected without a second
   `SendMessage`.

## Verification notes

- Re-checked the actual repository-root integration branch: `HEAD` and
  `origin/regin` are `7bea8c2836f7abd46c2d2759ea5fff0e4e878da3`; no hosted PR is
  applicable because both required source and target are `regin`.
- Read `00-ticket.md`, `10-implementation.md`, and this prior review artifact before
  inspecting the new diff. The prior request is satisfied: URL resolution now uses
  `_visible_guild_page()` (`src/ravn/adapters/agent_directory.py:95-100`) rather than
  `list_agents()`, and `test_configured_agent_card_url_does_not_bypass_guild_visibility`
  proves configured-only URLs cannot bypass Guild visibility.
- Independently passed: `uv run --extra dev pytest -q
  tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py`
  (20 passed); `uv run --extra dev ruff check src/ravn/adapters/agent_directory.py
  src/ravn/adapters/tools/a2a_task.py src/ravn/ports/agent_directory.py
  tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py`;
  and `git diff --check fd3fff95..HEAD`.
- Reproduced the new finding with the live resident activity finder: starting once by
  visible card URL and again by its canonical id yielded `task-url task-canonical` and
  two `SendMessage` RPCs. The same logical request should have been blocked by the
  active-task guard.

## Recommendation

Do not approve until the request fingerprint is canonicalized and the alias-deduplication
regression passes.
