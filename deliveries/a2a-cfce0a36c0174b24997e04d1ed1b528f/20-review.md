# NIU-1114 — re-check review

## Verdict

**needs_changes**

## Findings

### Major — live end-to-end acceptance evidence is still absent

`deliveries/a2a-cfce0a36c0174b24997e04d1ed1b528f/10-implementation.md:118-123`
states that this branch cannot inspect the authorized resident or submit/read its
actor-bound task, so it records none of the required resident/configuration,
commission/task, workflow/Forge run, verification, court, installation/version,
or ledger evidence. This is the ticket's merge gate, as specified in
`deliveries/a2a-cfce0a36c0174b24997e04d1ed1b528f/00-ticket.md:10-14`; source
tests and the non-commissioning doctor probe cannot substitute for it.

**Fix:** use the selected deployed Runa/Valhalla workload identity (or an
equivalently bounded authorized evidence route) for exactly one capability-gap
build, independently verify the deployed owner/configuration, and record the
resulting task/commission ID, workflow/Forge run, verification result, court
verdict, installation ID or learned-tool version, and ledger entry.

## Prior follow-up verified

The prior method-validation request is satisfied. The new endpoint handling in
`src/ravn/valkyrie_evolution/tool_build_doctor.py:466-499` accepts only the
expected `-32001` task-not-found response or a result for the reserved probe
task and rejects other JSON-RPC errors. The regression at
`tests/test_ravn/test_tool_build_doctor.py:556-586` verifies that `-32601`
(`method not found`) fails the doctor. No additional source-code blocker was
found in this re-check.

## Verification notes

- Reviewed committed `regin` at `5829c0d5` (`origin/regin` matched) and the
  complete re-check diff `ddfe1e3b..HEAD`; no hosted PR is recorded.
- Confirmed the prior review artifact before inspecting the diff, then verified
  its method-validation request in both current source and regression coverage.
- `uv run ruff check src/ravn/valkyrie_evolution/tool_build_doctor.py tests/test_ravn/test_tool_build_doctor.py` — passed.
- `uv run --extra dev pytest -q tests/test_ravn/test_tool_build_doctor.py tests/test_ravn/test_tool_build_backends.py tests/test_ravn/test_tool_build_workflows_cli.py` — **96 passed**.

## Recommendation

Keep NIU-1114 in review until the authorized bounded route publishes the
independently inspectable live proof listed above.
