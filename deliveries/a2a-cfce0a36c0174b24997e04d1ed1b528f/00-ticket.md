# NIU-1114 — A2A resident tool-build validation

- **Tracker:** [NIU-1114](https://linear.app/niuu/issue/NIU-1114/a2a-validate-resident-tool-build-end-to-end-in-dev)
- **Niuu branch:** `regin` only; no pull request may target `dev` or `main`.
- **Deployment target selected by the prior GitOps handoff:** Runa,
  `valkyrie-valhalla-k8s`, on the Valhalla Teleport cluster.

## Acceptance gate

This ticket remains **In Progress** until one deployed resident has a real A2A
tool-build record containing the commission/task id, verification result,
policy-court verdict, installation id or learned-tool version, and capability
ledger entry. Source tests, a public agent card, and a local doctor invocation
do not substitute for that evidence.

## Evidence observed on 2026-10-25

- The authorized operator's read-only `kubernetes_inspect` on
  `teleport.niuu.world-ymir` reports Deployment `valkyrie-ymir-k8s` at
  generation and observed generation `89`, ready `1`, with ConfigMap and
  Fleet objectset hash `6071741d8f91dbe9a1d106514e58a63a80fae04a`. Its
  response truncates `config.yaml` before the backend declaration. This
  establishes only the reported workload/Fleet state; it neither identifies
  the selected Runa target nor proves `A2AToolBuildBackend` is configured.
- A read-only GET of `https://yggdrasil.niuu.world/.well-known/agent-card.json`
  returned HTTP 200. The card advertises JSON-RPC endpoint
  `https://yggdrasil.niuu.world/api/v1/ting/a2a`, push notifications, and
  exactly one `tool-builder` skill: `Tool & Skill Builder`
  (`3c217e0a-1ff0-56d6-ba27-54cd43bff8e6`).
- A read-only GET of Runa's configured callback
  `https://runa.valhalla.asgard.niuu.world/a2a/push` returned HTTP 405. That
  demonstrates that the public route accepts neither an unauthenticated GET
  nor proves its deployed ConfigMap or A2A authorization.
- The prior GitOps handoff remains in
  `deliveries/a2a-80c316f97d704238a3abd3f130748223/`; it identifies the
  configuration expected after Fleet reconciliation, not live build evidence.
- For request `e79e4b9fd0ce`, the recorded operator decision authorizes one
  dev `k8s-valkyrie` live proof. Deployment changes must use
  `jve/infrastructure` on infra `main` through `infra_change`; secrets stay in
  Doppler/ExternalSecret and Fleet-managed resources must not be mutated
  directly. This authorization does not identify Sigrun as a replacement for
  Runa/Valhalla.
- The separately built bounded submission/evidence route was previously
  reported as `a2a-cd26d65106d04df2ac3a4cde1de72b3f`; the operator now reports
  its successor `a2a-e7c858b5863a4e67b012baa3547400b9` as `WORKING`. Neither
  report is live-proof evidence until its records can be read.
- This session successfully exchanged its own Skuld-session workload proof for
  a short-lived `valkyrie_build` token with `ting:workflow:launch` and
  `forge:session:create` scopes, then sent a read-only `GetTask` request for
  `a2a-e7c858b5863a4e67b012baa3547400b9` to the card's A2A endpoint. Ting
  returned HTTP 403: `Scoped credential only grants operations on tasks its
  workload launched`. This proves the generic Skuld-session exchange and A2A
  endpoint are reachable, not that the selected deployed Runa resident has a
  usable identity or that the independent task can be observed. No
  `SendMessage` was issued: that workload is not the authorized deployed
  resident for this proof.

## Independently verified doctor work — 2026-10-25

The source-side A2A doctor now uses the authenticated agent-card snapshot for
skill selection and then checks the advertised same-origin JSON-RPC endpoint
with a non-commissioning `GetTask` probe for the reserved
`ravn-tool-build-doctor-probe` id. A JSON-RPC task-not-found reply is expected
and establishes endpoint/auth reachability without creating a workflow,
capability-gap request, or cross-actor read.

This does not contact or retry the independent `a2a-e7c858b5863a4e67b012baa3547400b9`
task. It remains unrelated to the one authorized live resident proof and
cannot replace its task, build, verification, court, installation, or ledger
identifiers.

## Review follow-up — 2026-10-25

The endpoint probe accepts only the A2A SDK `TaskNotFoundError` JSON-RPC code
(`-32001`) for its reserved absent task, or a task result whose id is the
reserved probe id. It rejects all other JSON-RPC errors, including
`-32601`/`method not found`; the focused regression test covers that response.
This source revision does not create or provide the still-missing live-proof
identifiers required by the acceptance gate.

## Review re-check — 2026-10-25

The review gate was re-opened and tracker status returned to **In Progress**.
This session re-ran the bounded, read-only evidence check against task
`a2a-e7c858b5863a4e67b012baa3547400b9`: workload-token exchange succeeded,
but `GetTask` at `https://yggdrasil.niuu.world/api/v1/ting/a2a` returned HTTP
403, `Scoped credential only grants operations on tasks its workload launched`.
No `SendMessage`, deployment mutation, or cross-actor retry was made. This
confirms that the available session identity cannot read the independent route's
records; it does not identify the deployed Runa configuration or satisfy any
live-proof criterion. The evidence-reader route's launching workload (or a
bounded equivalent) must publish the required identifiers.
