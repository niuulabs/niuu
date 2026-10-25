# NIU-1114 — implementation and live-validation handoff

## Result of the GitOps coordination pass

No Niuu code or infrastructure change was needed: the reviewed GitOps source
already contains the complete A2A resident configuration required for the
selected `valkyrie-ymir-k8s` target. The infrastructure checkout is clean at
`e211e1c427906dcb921bffdf02180a77e3ccaa9d`; therefore this pass opened no
infrastructure PR and did not mutate Kubernetes or merge infrastructure.

The required Niuu delivery record is committed directly to `regin`; no Niuu PR
targets `dev` or `main`.

## Evidence collected

- The prior live `ravn tool-build doctor --json` report is green for all five
  A2A hops: configured adapter, backend construction, workload-token exchange,
  agent-card reachability, and a single `Tool & Skill Builder` selector result.
- The live card advertises `Tool & Skill Builder`
  (`3c217e0a-1ff0-56d6-ba27-54cd43bff8e6`) and push notifications.
- The selected Sigrun public callback responds `405` to a HEAD request with
  `Allow: POST`, which proves the published path reaches the resident gateway;
  it is not proof of a deployed ConfigMap revision or of build completion.
- The unauthenticated HUD data endpoint timed out during this pass. It is not
  evidence of an unavailable resident and must not be reported as live success.

The Mímir handoff was published and read back at
`resident/niu-1114-gitops-handoff-2026-10-25.md` on `mimir-yggdrasil`.

## Required Regin live-validation sequence

Regin must use **read-only** `kubernetes_inspect` against the named `ymir`
Teleport cluster to verify the Fleet-applied ConfigMap, Deployment revision,
resident readiness, and callback route before sending work to Sigrun. No direct
Kubernetes mutation is authorized.

Then instruct Sigrun to handle one bounded, read-only capability gap and retain:

1. the A2A task/campaign id and Forge session id;
2. the doctor output from the resident configuration;
3. the verification result and court gate/review verdict;
4. the installed learned-tool name/version and realm capability-ledger entry.

If the deployed state differs from the reviewed source, create an
`infra_change` review branch from infrastructure `main`; do not merge it without
review. Only after that evidence exists may NIU-1114 move beyond `In Progress`.

## Focused regression verification

The source-side A2A doctor/backend/workflow suites are rerun for this handoff.
Their passing result validates the code contract, not the missing live
commission/install acceptance.
