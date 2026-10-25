# NIU-1114 — review

## Verdict

**needs_changes**

## Findings

### Major — the prior live-proof request remains unsatisfied

`deliveries/a2a-cfce0a36c0174b24997e04d1ed1b528f/10-implementation.md:36-38` still confirms no authorized submission route to the selected deployed Runa resident. Lines `60-67` specify the required identifiers but do not record them. The new source-side endpoint probe is non-commissioning, so it does not satisfy the previous request for a real commission/task, verification, court, installation, ledger, and deployed-owner/configuration evidence.

**Fix:** use the selected deployed Runa/Valhalla workload identity (or an equivalently bounded authorized route) to submit exactly one capability-gap request, independently inspect its deployed configuration/owner, and record the resulting task/commission ID, workflow/Forge run, verification result, court verdict, installation ID or learned-tool version, and ledger entry here.

### Major — the endpoint probe accepts unsupported A2A RPC methods

`src/ravn/valkyrie_evolution/tool_build_doctor.py:456-470` marks every syntactically valid JSON-RPC envelope as a successful endpoint check. In particular, a server replying with `{"jsonrpc":"2.0","error":{"code":-32601,"message":"method not found"}}` passes even though the build backend requires `GetTask` to poll commissioned work. This can produce a green doctor run for an endpoint that cannot execute the A2A method needed by the configured backend.

**Fix:** accept only the expected benign response for the reserved absent task (the Ting task-not-found JSON-RPC error) or a valid task result, and fail on other JSON-RPC errors, including method-not-found. Add regression coverage for that error response.

## Verification notes

- Re-check review of committed `regin` at `cdeb3c87` (`origin/regin` is the same ref); reviewed `git diff b5fb82fb..HEAD` and current files. No hosted PR is recorded.
- The prior follow-up was explicitly checked and remains unsatisfied; the handoff itself says the independently launched route is still awaited.
- `uv run --extra dev pytest -q tests/test_ravn/test_tool_build_doctor.py tests/test_ravn/test_tool_build_backends.py tests/test_ravn/test_tool_build_workflows_cli.py` — **95 passed**.
- `uv run ruff check src/ravn/valkyrie_evolution/tool_build_doctor.py tests/test_ravn/test_tool_build_doctor.py` — passed.

## Recommendation

Keep NIU-1114 in review until both the endpoint-probe validation and the independently inspectable, bounded live proof are complete.
