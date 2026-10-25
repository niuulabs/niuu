# NIU-1151 — closure

- Tracker identifier: `NIU-1151`
- Merged branch: `regin` (direct landing; no hosted PR)
- Target branch: `regin`
- Merge commit SHA: `4d389922`
- Final tracker status: `Done`

## Landed summary

`kubernetes_inspect(action="configmap")` now supports a bounded, optional
single-key trailing-byte read. This allows the backend declaration in a large
`config.yaml` to be retrieved without disclosing unrelated ConfigMap keys,
while preserving existing unfiltered truncation and missing-name behavior.
The final implementation also preserves default kubeconfig, current-context,
and path-list semantics. Focused regression tests (228), Ruff, and diff checks
passed; live cluster verification was not attempted because `kubectl` and
`tsh` are unavailable in this workspace.
