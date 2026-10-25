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
- The separately built bounded submission/evidence route is tracked as
  `a2a-cd26d65106d04df2ac3a4cde1de72b3f` and is push-registered `WORKING`, but
  no endpoint, scoped identity, or successful capability-gap submission has
  been demonstrated. It is not live-proof evidence.
