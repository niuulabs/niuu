# NIU-1114 — review

## Verdict

**needs_changes**

## Finding

### Major — live acceptance proof is still absent

`deliveries/a2a-cfce0a36c0174b24997e04d1ed1b528f/10-implementation.md:36-38` confirms there is no authorized submission route to the selected deployed Runa resident. Lines `60-66` enumerate the required records but do not provide any of them. Consequently, the change cannot prove the ticket's required end-to-end commission, verification, court decision, installation, and ledger recording.

**Fix:** use the selected deployed Runa/Valhalla workload identity (or an equivalently bounded authorized route) to submit exactly one capability-gap request. Record the resulting task/commission ID, workflow/Forge run, verification result, court verdict, installation ID or learned-tool version, and capability-ledger entry in the delivery artifacts. Independently read-only inspect the deployed Runa configuration/owner before closing. Do not treat local doctor output or the public card as a substitute.

## Verification notes

- Reviewed the committed `regin` branch at `b5fb82fb` (also `origin/regin`); no hosted PR is recorded and `git diff $(git merge-base origin/regin HEAD)..HEAD` is empty.
- Reviewed the NIU-1114 delivery ticket and implementation handoff, plus the updated operator runbook and delivery-history diff.
- Re-ran focused source checks: `uv run --extra dev pytest -q tests/test_ravn/test_tool_build_doctor.py tests/test_ravn/test_tool_build_backends.py tests/test_ravn/test_tool_build_workflows_cli.py` — **93 passed**.
- The delivery's evidence explicitly establishes only local diagnostics and inaccessible/insufficient live routes; it contains no task, verification, court, installation, or ledger identifiers.

## Recommendation

Keep NIU-1114 in review/in progress until the bounded live proof above is durably recorded and can be independently inspected.
