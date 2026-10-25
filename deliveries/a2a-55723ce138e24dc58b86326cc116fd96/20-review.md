# Review — NIU-1150

## Verdict

**needs_changes**

## Findings

1. **Major — `src/ravn/adapters/agent_directory.py:101` resolves configured-only cards from a URL.**
   `get_agent()` searches `page.items` after `list_agents()`, but `list_agents()` appends
   every configured `a2a_agent_card_urls` entry to the principal-filtered Guild response.
   Thus, an Agent Card URL that Guild did not return for the caller (including when Guild
   returns an empty directory) is resolved and used merely because it is configured locally.
   That violates NIU-1150's requirement that URL-form handles resolve only to a registered,
   principal-visible Guild agent and contradicts the stated compatibility contract. Preserve
   existing canonical configured-agent-id behavior, but perform URL matching against the raw,
   unaugmented Guild page only; add a regression test proving a configured-only card URL
   cannot resolve through the URL path.

## Verification notes

- Reviewed the actual committed implementation from repository-root `regin`: `acd2ad1d3827e687986424c2f0bbaab5d012fdb9` plus the delivery record at `24fd947d`; both `HEAD` and `origin/regin` resolve to `24fd947d`.
- Read `deliveries/a2a-55723ce138e24dc58b86326cc116fd96/00-ticket.md` and `10-implementation.md`; no prior `20-review.md` existed before this pass. Source/target is `regin`; no hosted PR applies.
- Independently passed: `uv run --extra dev pytest -q tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py` (19 passed).
- Independently passed: `uv run --extra dev ruff check src/ravn/adapters/agent_directory.py src/ravn/adapters/tools/a2a_task.py src/ravn/ports/agent_directory.py tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py`.
- Independently passed: `git diff --check 51428495..HEAD`.
- Reproduced the finding using an empty Guild `items` response and one configured card: `GuildAgentDirectoryAdapter.get_agent(configured_card_url)` returned an `agent-*` entry. This is not covered by the new tests.

## Recommendation

Do not approve until URL-form resolution is restricted to raw principal-filtered Guild entries and the configured-only regression case passes.
