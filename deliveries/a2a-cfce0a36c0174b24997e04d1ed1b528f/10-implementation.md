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
the selected deployed resident, and read-only inspection cannot do so. Do not
close NIU-1114 from this handoff.

For request `e79e4b9fd0ce`, the recorded operator decision authorizes one dev
`k8s-valkyrie` proof and requires GitOps changes through
`jve/infrastructure` infra `main` and `infra_change`; no direct Fleet mutation
is authorized. It does not establish that Sigrun/`valkyrie-ymir-k8s` supersedes
the selected Runa/Valhalla target. Deployed configuration and owner evidence
must identify the resident before that conclusion can be made.

The bounded submission/evidence route under separate delivery was previously
reported as `a2a-cd26d65106d04df2ac3a4cde1de72b3f`; the operator now reports
`a2a-e7c858b5863a4e67b012baa3547400b9` as `WORKING`. To investigate it without
launching a second build, this session exchanged its Skuld-session workload
proof for a short-lived `valkyrie_build` token with the standard A2A build
scopes and called `GetTask` on the public Ting A2A endpoint. The exchange
succeeded (HTTP 201), but `GetTask` returned HTTP 403: `Scoped credential only
grants operations on tasks its workload launched`. This is the expected
actor-bound restriction: the Skuld session's identity cannot read another
workload's task. No `SendMessage` was issued, because this session is not the
selected deployed Runa resident and a launch would not satisfy the ticket's
one-resident proof.

The concrete outstanding authority is therefore a deployed Runa/Valhalla
resident workload identity (or an equivalently bounded read authority) that
can both submit exactly one capability-gap request and read that request's
resulting task, workflow/Forge run, verification result, court verdict,
installation id or learned-tool version, and capability-ledger entry. The
independent task's launching workload can alternatively publish those records
for this ticket. Record the identifiers before closure. File any protocol or
facade finding as a follow-up issue rather than changing it in this ticket.

## Hosted review

No new hosted PR was created: the small documentation and delivery handoff is
committed directly on the required `regin` branch.

## Review revision

The doctor now retains the authenticated agent-card response for skill
selection and, after a unique builder skill is selected, sends only a
JSON-RPC `GetTask` request for the reserved nonexistent
`ravn-tool-build-doctor-probe` id to the advertised same-origin endpoint. This
is a non-commissioning diagnostic: it validates the endpoint and its
authenticated request path without issuing `SendMessage` or creating a second
capability-gap build.

Focused verification after this revision passed:

```text
uv run --extra dev pytest -q tests/test_ravn/test_tool_build_doctor.py \
  tests/test_ravn/test_tool_build_backends.py \
  tests/test_ravn/test_tool_build_workflows_cli.py
95 passed in 0.96s
```

This source-level diagnostic improvement does not address the live acceptance
gate above. At the operator's direction, no cross-actor retry or broader
credential request was made; the bounded submission/evidence-reader task
`a2a-e7c858b5863a4e67b012baa3547400b9` remains the route expected to publish
the deployed Runa/Valhalla proof identifiers.

## Review follow-up: JSON-RPC method validation

The endpoint probe now treats only the A2A SDK's task-not-found JSON-RPC code
`-32001` for the reserved absent task as benign. A successful task result must
identify that reserved probe id. Every other JSON-RPC error, including
`-32601` (`method not found`), fails hop 6; focused regression coverage proves
that an endpoint without `GetTask` cannot produce a green doctor report.

Focused verification for this revision passed:

```text
uv run ruff check src/ravn/valkyrie_evolution/tool_build_doctor.py \
  tests/test_ravn/test_tool_build_doctor.py
All checks passed!

uv run --extra dev pytest -q tests/test_ravn/test_tool_build_doctor.py
26 passed in 0.56s
```

No available session capability can inspect the authorized Teleport workload or
submit/read the one deployed resident's actor-bound A2A task. The required
resident/configuration, commission/task, workflow/Forge run, verification,
court, installation/version, and ledger evidence remains absent. This revision
therefore leaves NIU-1114 In Progress rather than claiming the live acceptance
gate.
