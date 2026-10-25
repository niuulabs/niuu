# NIU-1151 — ticket record

- Tracker: [NIU-1151](https://linear.app/niuu/issue/NIU-1151/kubernetes-inspect-configmap-action-truncates-configyaml-before)
- Status at implementation start: In Progress
- Starting branch: `regin` at `01af0978`
- Target branch: `regin` only

## Scope

Extend the read-only `kubernetes_inspect` ConfigMap contract so a caller can
select one data key and request a bounded trailing byte window.  This lets an
authorized caller inspect a backend declaration that is beyond the ordinary
per-value ConfigMap response budget without returning unrelated ConfigMap keys.

The implementation remains within the Ravn Kubernetes inspection adapter and
its public tool contract.  It does not change resident backend configuration,
Fleet or Helm resources, Kubernetes RBAC, or cluster state.

## Acceptance record

The focused regression fixture models `nats/valkyrie-ymir-k8s-config` on the
`teleport.niuu.world-ymir` context.  Its `config.yaml` is longer than the
ordinary ConfigMap value budget and ends with a backend declaration.  The test
verifies that a keyed, bounded-tail request returns that declaration intact,
reports `config.yaml` in `truncated_keys`, and does not expose the unrelated
key.  Existing unfiltered truncation and missing-name error behavior are also
covered.

## Review revision (2026-10-25)

Reviewer feedback identified that decoding an arbitrary UTF-8 byte suffix with
replacement characters could exceed `tail_bytes`. The revision retains the
same bounded keyed read contract while removing a partial leading UTF-8
sequence before decoding, so returned text is valid UTF-8 and never encodes to
more than the selected byte cap.
