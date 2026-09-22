# Direct-host session scope

September 21, 2026. Local implementation; not deployed.

## Why

A native client registers each host separately. The Niuu facade's default session
list aggregates its visible registered Forge instances. Combining that aggregate
with each member's direct response gives duplicate sessions and incorrect host
labels when the client stamps rows with its connection identity.

Previously `instance_id=<registry ID>` selected a known registered instance, but
there was no discovery-free, server-verified “this connected host only” selector.
Do not infer locality from the default instance, display name, registry ordering,
or a native host alias. Do not delete real sessions to hide this presentation bug.

## Contract

| Request | Behavior |
| --- | --- |
| `GET /api/v1/forge/sessions?scope=local` | Only the visible, enabled embedded Forge runtime; no registered-node requests |
| `GET /api/v1/forge/sessions?status=archived&scope=local` | Same scope, with the existing archive filter |
| `GET /api/v1/forge/sessions/stream?scope=local` | Only that embedded runtime's broadcaster |
| `GET /api/v1/forge/sessions/<id>?scope=local` | Resolve the row only within the embedded runtime |
| `GET/PATCH /api/v1/forge/sessions/<id>/read-state?scope=local` | Existing authenticated reader API, with ownership lookup restricted to local |
| `GET /api/v1/forge/feature-flags?scope=local` | Probe local runtime capabilities, not a possibly remote default |

Successful local **list and SSE** responses include
`X-Forge-Session-Scope: local`. The capability projection advertises
`capabilities.local_session_scope: true`. The response body shape, instance
metadata, principal forwarding and existing status/project/parent filters remain
unchanged. Archives are still opt-in. There is no migration for session scope.

The facade recognizes locality through the existing explicit
`RegisteredInstance.config.transport == "embedded"` configuration, after normal
visibility/enabled filtering. Exactly one local runtime is required. A pure registry
shell, invisible/disabled local runtime or ambiguous embedded entries returns 503.
A configured embedded transport that cannot be dispatched fails rather than
querying remote nodes. Local backend errors and malformed payloads are surfaced,
not converted to empty success. An empty **successful** local list is valid.

`scope=local&instance_id=<another instance>` returns 422. An explicit matching local
ID is allowed. Unknown scopes return 422. The ownership resolver also honors local
scope for other existing session-ID routes which use it; this is not a claim that
every Forge endpoint or session-creation operation now supports scoped routing.

A standalone Volundr server already serves only local sessions. It now accepts the
scope contract, advertises the capability and emits the same list/stream header.
An explicit `guild` request on standalone still returns its local sessions; it has
no registry to aggregate.

## Compatibility and rollout

The Niuu facade retains **guild aggregation by default**; explicit `scope=guild`
uses the same behavior. The existing `instance_id` selector still works. The web
client is unchanged. Default SSE continues to use its previous backing/default
instance (it was never a full guild fan-in).

The paired native ForgeKit change makes each iOS/macOS connection local by default
for lists, streams, incremental row/read-state reads and the liveness probe. It
requires the list/stream acknowledgement before consuming rows/events: older
servers may silently ignore unknown query parameters. An old server must produce
a readable update-needed error, not quietly recreate duplicates. Low-level SDK
callers can explicitly choose the legacy guild mode; the native mesh does not.

**Deploy this backend support on each configured host before shipping the paired
native change.** Then validate ordinary/archive/reconnect behavior on both Apple
platforms. This work has not deployed services, pushed branches, built native
apps, uploaded to TestFlight or changed live registrations. The existing unread
state branch also contains an earlier migration; that batch's rollout requirements
remain separate and must be honored when integrating the whole branch.

## Validation

- 280 focused backend tests pass on Python 3.12.3, with no pytest warnings.
- New tests cover remote-default avoidance, two direct hosts, same-named distinct
  sessions, archived/project filter forwarding, normal visibility, missing/ambiguous
  runtime, transport/backend/malformed response failures, SSE source, reader identity,
  local liveness/read-state routing and legacy guild/explicit-instance compatibility.
- Changed-file Ruff and formatting checks pass. Changed executable lines are covered;
  exact counts are in `.local/local-sessions-20260921/changed-line-coverage.json`.
- Focused whole-adapter coverage is approximately 59%; this is **not** a full-suite
  85% gate pass. No gate or repository test configuration was lowered. Full backend
  CI, native transport/runtime and real PostgreSQL validation remain unrun here.

Source: `src/niuu/adapters/inbound/rest_volundr.py`,
`src/volundr/adapters/inbound/rest.py`.
Tests: `tests/test_niuu/test_rest_local_sessions.py`, `tests/test_rest.py`, with
existing REST, ownership and reader-state regression suites.
Private test evidence: `.local/local-sessions-20260921/`.
