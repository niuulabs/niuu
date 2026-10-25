# Review — NIU-1150 (final re-check)

## Verdict

**pass**

## Findings

No blocking findings.

## Verification notes

- Reviewed the entire committed delivery diff from `fd3fff955f3953a7769c7099229a576cc408c908..2c0956053f14445b7069ed5f3b7c643eb15dbfae` on repository-root branch `regin`; `HEAD` equals `origin/regin` at `2c0956053f14445b7069ed5f3b7c643eb15dbfae`. No hosted PR applies because source and target are both the required `regin` branch.
- Read `00-ticket.md`, `10-implementation.md`, and the prior review artifact before re-checking. The prior requested alias idempotency follow-up is satisfied at `src/ravn/adapters/tools/a2a_task.py:339`: the fingerprint uses the resolved canonical `agent.id`. `tests/test_ravn/test_a2a_task_tool.py:146-223` starts through a visible Agent Card URL, verifies the durable handle stores the canonical id, and rejects an equivalent canonical-id duplicate before a second `SendMessage`.
- URL lookup remains constrained to the raw principal-filtered Guild page at `src/ravn/adapters/agent_directory.py:95-100`; it neither fetches caller-supplied URLs nor allows configured-only URLs to bypass visibility. The adapter tests at `tests/test_ravn/test_agent_directory_adapter.py:165-204` cover visible URL resolution, invisible URL denial, and configured-only URL denial. Canonical unknown-agent denial remains covered at `tests/test_ravn/test_a2a_task_tool.py:535-555`.
- Independently passed `uv run --extra dev pytest -q tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py` (20 passed), `uv run --extra dev ruff check src/ravn/adapters/agent_directory.py src/ravn/adapters/tools/a2a_task.py src/ravn/ports/agent_directory.py tests/test_ravn/test_agent_directory_adapter.py tests/test_ravn/test_a2a_task_tool.py`, and `git diff --check fd3fff95..HEAD`.
- CI is not asserted: this direct integration has no hosted PR and the delivery records no GitHub Actions run for the push.

## Recommendation

Approve the `regin` delivery. Newly emitted handles carry a usable canonical Guild id, legacy URL handles resolve only while currently visible, and alias starts share the active-task guard.
