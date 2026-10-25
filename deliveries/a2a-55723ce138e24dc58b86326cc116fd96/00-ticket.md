# NIU-1150 — Resolve durable A2A handle Agent Card URLs

- Delivery slug: `a2a-55723ce138e24dc58b86326cc116fd96`
- Tracker: [NIU-1150](https://linear.app/niuu/issue/NIU-1150/platform-defect-durable-a2a-handles-write-agent-card-url-as-agent-id)
- Required source and target: `regin`
- Integration branch: `regin`
- Tracker status at implementation start: `In Progress`

## Acceptance

A durable A2A task handle's `agent_id` must be usable verbatim with
`a2a_task.get`, including tasks started with an Agent Card URL. Canonical Guild
id lookup and principal visibility denial must remain intact. Legacy URL-form
handles must be handled safely without changing directory visibility.

## Delivery constraints

This delivery changes only Ravn's Guild-backed A2A directory resolution and
the resident A2A task path. It does not modify Guild visibility rules,
NIU-1107/NIU-1116 directory behavior, public REST APIs, migrations, or
unrelated workflows. The required direct integration is `regin`; no branch or
PR targets `dev` or `main`.

## Integration result

The implementation is directly integrated on `regin` in
`acd2ad1d3827e687986424c2f0bbaab5d012fdb9`. No hosted PR is created because
source and target are both the required integration branch.

## Review revision

The review finding that locally configured Agent Card URLs could bypass the
principal-filtered Guild response is in scope for this delivery. URL-form
lookup now uses only the raw Guild page, while configured canonical IDs retain
their existing explicit-card behavior. The regression test denies a
configured-only URL. Revision commit
`d5296cf352798790bc4d03603461db28ae136a76` is pushed to `origin/regin`.
