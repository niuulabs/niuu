# NIU-1150 implementation

## Change

`GuildAgentDirectoryAdapter.get_agent` now accepts an Agent Card URL only when
that exact URL belongs to an entry returned by the principal-filtered Guild
directory. It returns the corresponding canonical directory entry; it does not
fetch a caller-provided URL or change the directory's visibility behavior.

Consequently, `A2ATaskTool` resolves URL-invoked starts to the canonical Guild
id before emitting resident activity, so the durable handle stores that id and
can be supplied directly to `a2a_task.get`. Existing legacy URL-form handles
remain readable: they resolve only while their URL still matches a currently
visible directory entry. No stored handles are rewritten, preserving the audit
record. The operator documentation records this compatibility contract.

## Verification

- Passed: `uv run --extra dev pytest -q tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py` — `19 passed`.
- Passed: `uv run --extra dev ruff check src/ravn/adapters/agent_directory.py src/ravn/adapters/tools/a2a_task.py src/ravn/ports/agent_directory.py tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py`.
- Passed: `git diff --check`.

The URL round-trip regression starts a task through an Agent Card URL, persists
its resident handle, and gets the live task with the exact stored `agent_id`.
It also gets the same task through the legacy URL form. Adapter coverage proves
unknown URLs are not fetched and do not resolve. Existing A2A tests retain the
canonical-id get and invisible-agent denial cases.

## Hosted delivery

No hosted PR is applicable: this ticket requires direct integration from and
onto `regin`. No PR, merge, or push targets `dev` or `main`.

## Integration evidence

Implementation commit `acd2ad1d3827e687986424c2f0bbaab5d012fdb9`
(`fix(ravn): resolve durable A2A card URL handles`) is pushed to `origin/regin`.
GitHub reports no Actions run for that `regin` push, so CI is not asserted; the
focused local verification above is the available evidence.
