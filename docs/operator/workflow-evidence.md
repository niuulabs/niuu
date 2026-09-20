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

Skuld requires two deployment-owned dynamic adapter settings for these nodes:

- `workflow.evidence_verifier`: an `EvidenceGateVerifier` adapter. The provided
  `niuu.adapters.evidence_gate.ConfiguredEvidenceGateVerifier` accepts
  `authenticator_adapter`, `authenticator_kwargs` and `trusted_producers`.
  It reuses `RsaEvidenceAuthenticator`; a verifier needs only trusted public keys
  and producer/key bindings, not a private signing key.
- `workflow.evidence_artifacts`: an `ArtifactDigestResolver` adapter. The provided
  `niuu.adapters.artifact_digest.FilesystemArtifactDigestResolver` accepts a
  deployment-controlled `root` and optional `chunk_size_bytes`. Artifact IDs are
  confined relative file paths. It rejects traversal, symlink escapes, non-files
  and mutation during hashing. Other artifact stores can implement the same port.

In the Skuld and Skuld Planner Helm charts these are configured through
`workflow.evidenceVerifier` and `workflow.evidenceArtifacts`, rendered into the
runtime YAML. Imported graph content never supplies these adapter settings or keys.
An evidence graph without both adapters fails startup with a configuration error.

The runtime reserves the gate's output events: agent outcomes and external event
injection cannot claim them. The human approval endpoint cannot resolve an
evidence gate. A rejected bundle follows the declared rejection edge; it does not
silently become accepted or downgrade to a human gate.

## Existing developer receipts

Developer delivery projects its Git candidate, test and review receipts into the
shared verifier. It authenticates the original signed receipt bytes, preserving
historical evidence and proof compatibility. Repository/base/candidate/merge
checks remain in the developer contract and source-control adapters. The new
gate does not remove those checks or replace the PR/MR publication controls.
