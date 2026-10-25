# NIU-1107 review — changes requested

## Verdict

`needs_changes` — the directory's new Agent Card resolver enables an
untrusted workflow-session configuration to make Observatory perform arbitrary
HTTP(S) requests. This is a blocking SSRF and must be corrected before the
NIU-1107 reconciliation can be approved.

## Finding

### Critical — untrusted card URLs are fetched by the Observatory process

`src/observatory/a2a_cards.py:210-237` accepts every syntactically valid
HTTP(S) `card_url` and fetches it. The URL originates from the unvalidated
`SessionCreate.workload_config` map (`src/volundr/adapters/inbound/rest.py:608-624`)
and is republished as `a2aCardUrl` (`src/volundr/adapters/inbound/rest.py:1089-1093`)
into the Volundr discovery entity (`src/observatory/entity_discovery.py:752-760`).
`_public_workload_url` excludes credentials and non-HTTP schemes but admits
loopback, link-local, RFC1918, and arbitrary external hosts.

A session owner can therefore publish, for example,
`http://169.254.169.254/...` or an internal service URL and invoke the local
`/api/v1/observatory/agents` endpoint to make the Observatory pod request it.
The response may be parsed and exposed as directory metadata when it resembles
an Agent Card. The same resolver follows a signed card's arbitrary `jku` at
`src/observatory/a2a_cards.py:314-331`, which has the same trust-boundary gap.

**Fix:** introduce an explicit deployment-owned allowed-origin policy for
Agent Card retrieval (and JWKS retrieval), default-deny URLs outside that
policy before any request. Do not reuse `authenticatedCardOrigins` as the
only protection: it currently controls credential forwarding, not whether an
origin is reachable. Validate both `a2aCardUrl` and signed-card `jku` against
the policy, including a test proving loopback/link-local/private and
non-allowlisted origins result in a bounded directory warning without a
network request. If arbitrary cards must be supported, use a dedicated,
network-restricted egress proxy/allowlist rather than direct pod egress.

## Verification notes

- Reviewed the declared repository root on `regin` at `490c5f90` and the
  reconciliation artifact diff (`1d3dbc1e`). The product directory change is
  present on `regin` in `4d4abb0f`, `3a2c083d`, and `a992872f`; `origin/dev` is
  an ancestor of `regin` (`0 36` for `origin/dev...regin`).
- Read this delivery's `00-ticket.md` and `10-implementation.md`; no previous
  `20-review.md` existed for this delivery.
- Ran `uv run --extra dev pytest -q tests/test_observatory/test_agent_directory.py
  tests/test_niuu/test_agent_directory.py tests/test_volundr/test_session_response_a2a.py`:
  **20 passed**. These tests confirm functional behavior but do not test the
  untrusted-origin SSRF boundary.
- PR #1171 targets `regin` and its reported CI is green, but its source diff is
  reconciliation documentation; it does not remediate this product finding.

## Recommendation

Do not approve or merge NIU-1107 yet. Add the fail-closed origin/egress policy
and regression coverage, then request a re-review of the updated `regin` diff.
