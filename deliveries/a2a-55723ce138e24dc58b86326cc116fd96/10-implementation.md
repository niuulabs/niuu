# NIU-1150 implementation

## Change

`GuildAgentDirectoryAdapter.get_agent` now accepts an Agent Card URL only when
that exact URL belongs to an entry returned by the principal-filtered Guild
directory. It returns the corresponding canonical directory entry; it does not
fetch a caller-provided URL or change the directory's visibility behavior.

The URL lookup reads the unaugmented Guild response directly. Configured Agent
Cards are still available by their deterministic configured canonical IDs, but
their URLs cannot be used to bypass principal visibility. A configured-only
URL therefore resolves as not visible rather than causing an Agent Card fetch.

Consequently, `A2ATaskTool` resolves URL-invoked starts to the canonical Guild
id before emitting resident activity, so the durable handle stores that id and
can be supplied directly to `a2a_task.get`. Existing legacy URL-form handles
remain readable: they resolve only while their URL still matches a currently
visible directory entry. No stored handles are rewritten, preserving the audit
record. The operator documentation records this compatibility contract.

## Verification

- Passed: `uv run --extra dev pytest -q tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py` — `20 passed`.
- Passed: `uv run --extra dev ruff check src/ravn/adapters/agent_directory.py src/ravn/adapters/tools/a2a_task.py src/ravn/ports/agent_directory.py tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py`.
- Passed: `git diff --check`.

The URL round-trip regression starts a task through an Agent Card URL, persists
its resident handle, and gets the live task with the exact stored `agent_id`.
It also gets the same task through the legacy URL form. Adapter coverage proves
unknown URLs are not fetched and do not resolve. Existing A2A tests retain the
canonical-id get and invisible-agent denial cases.

The review-revision adapter test proves a configured local card whose URL is
absent from Guild's raw response cannot resolve through the URL path. The
existing configured canonical-ID test proves that compatibility path remains
available separately.

## Hosted delivery

No hosted PR is applicable: this ticket requires direct integration from and
onto `regin`. No PR, merge, or push targets `dev` or `main`.

## Integration evidence

Implementation commit `acd2ad1d3827e687986424c2f0bbaab5d012fdb9`
(`fix(ravn): resolve durable A2A card URL handles`) is pushed to `origin/regin`.
Review revision commit `d5296cf352798790bc4d03603461db28ae136a76`
(`fix(ravn): preserve Guild visibility for card URLs`) is also pushed to
`origin/regin`.
GitHub reports no Actions run for that `regin` push, so CI is not asserted; the
focused local verification above is the available evidence.

## Alias idempotency revision

After resolving an input identifier through the visible Guild directory,
`A2ATaskTool` fingerprints a start request with the returned canonical
`agent.id`, not the caller's alias. Thus starting through an Agent Card URL
and retrying through the equivalent canonical id is one active request and the
second start is rejected before another `SendMessage` RPC is made.

The URL durable-handle regression now also starts via the visible URL, retries
through the canonical id with the same request, and verifies that the existing
task is reported with only one `SendMessage`. It continues to verify exact
stored-handle get, legacy URL get, and invisible URL denial.

Revision commit `662a7f5d35750eae29a8fa1ab99f1d0640a6e68c`
(`fix(ravn): deduplicate A2A starts across agent aliases`) is pushed to
`origin/regin`.
