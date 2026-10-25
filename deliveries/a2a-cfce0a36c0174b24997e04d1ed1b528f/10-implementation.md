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

An authorized operator has now supplied read-only `kubernetes_inspect`
evidence for `valkyrie-ymir-k8s` on `teleport.niuu.world-ymir`: generation
and observed generation `89`, ready `1`, and matching ConfigMap/Fleet
objectset hash `6071741d8f91dbe9a1d106514e58a63a80fae04a`. The inspector
truncates before the backend declaration, and the reported resident differs
from the Runa/Valhalla target named by the prior handoff. It is therefore not
proof of the selected resident's A2A configuration.

No authorized submission route has been provided to send a capability gap to
the deployed Sigrun resident, and read-only inspection cannot do so. Do not
close NIU-1114 from this handoff.

The remaining authorization must grant a real submission route to the selected
deployed resident: either an approved human/operator invocation endpoint or a
scoped workload identity that may submit exactly one bounded, read-only
capability-gap request through its configured A2A path. It must also permit
reading the resulting task, workflow/Forge run, verification, court,
installation, and capability-ledger records. Record those identifiers. File
any protocol or facade finding as a follow-up issue rather than changing it in
this ticket.

## Hosted review

No new hosted PR was created: the small documentation and delivery handoff is
committed directly on the required `regin` branch.
