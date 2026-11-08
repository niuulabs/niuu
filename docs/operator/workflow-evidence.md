# Reusable evidence gates

An evidence gate is an executable graph node. It invokes deterministic services,
emits a public acceptance/rejection report and routes the next event without a
model turn or human approval. It uses the same evidence rule engine as developer
delivery, with an artifact-neutral identity instead of Git-only fields.

## Graph contract

This node checks the current `report.md` artifact. Connect one incoming event
carrying the complete evidence bundle, and route both named output events to
existing nodes:

```yaml
id: verify-report
kind: gate
mode: evidence
label: Verify report evidence
condition: Accept only verified evidence for the current report
artifact:
  kind: document
  id: report.md
approvalEvent: report.accepted
changesRequestedEvent: report.rejected
evidencePolicy:
  required_result_contract_ids: [report-structure]
  result_producers:
    report-structure: [document-validator]
  required_review_roles: [accuracy]
  review_producers:
    accuracy: [editorial-review-service]
```

Policy keys use the evidence contract's snake_case spelling. Result-only,
review-only and named-check policies are supported; an empty policy is rejected.
Optional named checks use `required_check_names`, `require_checks` and
`check_producers`. Producer IDs must identify real, deployment-authorized
services. A workflow cannot introduce a trusted signing identity by naming it.

The incoming outcome must be valid and carry `fields.evidence`, a serialized
`niuu.domain.evidence.EvidenceBundle`. Its signed identity includes:

- `artifact_kind` and `artifact_id`, matching the graph's artifact declaration;
- `artifact_digest`, matching the resolver's SHA-256 of the actual current artifact;
- `execution_id`, matching the current runtime session;
- `attempt_id`, matching the workflow activation.

Receipts are made by configured producers from actual checks or authenticated
review outcomes. They are not strings composed by the coordinator. Producers sign
the canonical `evidence_payload(receipt)` using the existing evidence authenticator.
Result receipts, review receipts and optional named-check receipts each bind the
full identity. The generic verifier checks provenance, producer authorization,
required contracts/roles, independence, duplicate evidence, unresolved blocking
findings and result status. Domain-specific publication still performs its own
checks at the point of mutation: a gate attests the artifact version it observed,
not arbitrary future edits.

## Deployment adapters

Skuld no longer evaluates evidence gates. A workflow's steps run on ordinary harness
sessions and Ting owns the run; Skuld holds a step and reports how it ended, and has
no gate runtime. The `workflow.evidence_verifier` and `workflow.evidence_artifacts`
settings, and the matching `workflow.evidenceVerifier` / `workflow.evidenceArtifacts`
chart values of the Skuld and Skuld Planner charts, were removed with it. Ting's step
engine does not evaluate `mode: evidence` gates yet, so a workflow that declares one
is not evaluated until that is built there.

The verifier and resolver adapters stay in `niuu`
(`niuu.adapters.evidence_gate.ConfiguredEvidenceGateVerifier`, which reuses
`RsaEvidenceAuthenticator`, and `niuu.adapters.artifact_digest.FilesystemArtifactDigestResolver`)
for the services that do evaluate evidence: Ting's delivery and research paths.

## Existing developer receipts

Developer delivery projects its Git candidate, test and review receipts into the
shared verifier. It authenticates the original signed receipt bytes, preserving
historical evidence and proof compatibility. Repository/base/candidate/merge
checks remain in the developer contract and source-control adapters. The new
gate does not remove those checks or replace the PR/MR publication controls.
