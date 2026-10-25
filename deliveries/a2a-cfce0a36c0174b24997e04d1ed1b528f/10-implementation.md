# NIU-1114 — implementation and validation handoff

## Source verification

`ravn tool-build doctor --config /tmp/niu-1114-ravn-a2a-doctor.yaml --json`
exited 0 on 2026-10-25 with all five configured A2A checks passing: adapter,
backend construction, workload-identity configuration, agent-card GET, and a
unique `Tool & Skill Builder` selector result. The ephemeral config used the
Runa GitOps handoff's card URL, projected-token path, exchange URL, audiences,
and `tool-builder` selector. This is a local diagnostic run, not evidence that
Runa's deployed ConfigMap or workload token was used.

Focused source verification also passed:

```text
uv run --extra dev pytest -q tests/test_ravn/test_tool_build_doctor.py \
  tests/test_ravn/test_tool_build_backends.py \
  tests/test_ravn/test_tool_build_workflows_cli.py
93 passed in 1.41s
```

The operator runbook now explicitly keeps `ForgeSessionToolBuildBackend` as
the non-A2A path and prohibits removal based on configuration or doctor output
alone.

## Blocking live evidence

This session has no `kubernetes_inspect`, `kubectl`, or `tsh` capability, so
it cannot independently read the Fleet-applied Valhalla Deployment/ConfigMap
or address a build to the deployed Runa resident. Do not close NIU-1114 from
this handoff.

The next authorized live-validation pass must, using read-only cluster access,
verify the deployed Runa configuration and readiness, then submit one bounded
read-only capability gap to that resident. Record the resulting A2A task id,
workflow/Forge run id, verification result, court verdict, installation
identifier or tool version, and realm capability-ledger entry. File any
protocol or facade finding as a follow-up issue rather than changing it in
this ticket.

## Hosted review

No new hosted PR was created: the small documentation and delivery handoff is
committed directly on the required `regin` branch.
