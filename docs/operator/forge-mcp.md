# Forge MCP: session credentials, grants and the HTTP endpoint

The Forge MCP gives an agent a small set of Forge tools. It can `notify` the user,
learn its `environment`, read other sessions and notifications, and, with an operator
grant, message peers or start and stop them. Agents reach it in two ways:

| Who | Transport | Credential |
|---|---|---|
| A coding session run by Forge (Claude Code, Codex, Grok under Skuld) | The built-in `forge` stdio server, which calls its own broker over loopback | The session's scoped **session credential**, held by the broker |
| An external agent (Lexi, OpenClaw, a Claude Code or Codex outside Forge) | `POST /api/v1/forge/mcp` (Streamable HTTP) | The agent's own identity: a PAT, an Envoy-verified user, or a session credential |

The tool contract is in
[`docs/forge/session-notifications-contract.md`](../forge/session-notifications-contract.md)
§8 and §9. Notifications themselves are covered in
[forge-notifications.md](forge-notifications.md).

## The session credential

Each time Forge starts or resumes a session, it mints a credential for that launch
and hands it to the session's broker as `SKULD__FORGE_MCP__TOKEN`. It also passes the
grants as `SKULD__FORGE_MCP__GRANTS`. The broker uses the credential only for Forge
MCP calls. Its own log, activity and usage reports keep the broker's own credential.
Skuld removes the token from the environment of every agent process it starts, so the
model never sees it. The token never appears in argv, logs or the local process state
file.

It is a workload JWT signed by the workload-identity issuer, with these claims:

| Claim | Value |
|---|---|
| `sub` / `tenant_id` | The session owner and their tenant |
| `token_use` | `forge_session` |
| `scopes` | `forge:notify`, `forge:session:read` and `forge:session:secret` always, plus one scope per grant |
| `workload_session_id` | The one session it is bound to (the same claim OpenShell session tokens use) |
| `workload_launch_id` | The launch it belongs to |
| `resource_access.*.roles` | `volundr:developer`. A session credential is never an admin, whatever its owner's roles. |

### Lifetime, refresh and revocation

The credential's lifetime is tied to the **session launch**, not to a clock:

- Every start mints a new token with a new launch id, which Forge records on the
  session row (`sessions.workload_config.forge_mcp.launch_id`).
- Forge checks every presented token against the live session row. The token is
  revoked once any of these is true:
  - the session is gone, stopped or archived;
  - the session has a different owner;
  - the session has been started again since the token was issued (its launch no
    longer matches).
- `exp` is only a backstop: `forge_mcp.session_tokens.ttl_seconds`, 30 days by default.

There is **no refresh route**, and this is deliberate. A sliding refresh would make
every broker renew its token in the background. A broker that could not reach Forge
for longer than the TTL (a Forge upgrade, a sleeping host) would come back with a dead
credential and no way to recover short of a restart. Short expiry exists to limit a
leaked token, and the launch binding already does that: a leaked token dies at the
session's next stop, restart, archive or deletion.

A session that runs longer than the backstop without a restart loses its MCP access
to Forge. `list_sessions` and friends then return `Forge answered 401`; restart the
session, or raise `ttl_seconds`.

### Signing key

| Forge configuration | Key used |
|---|---|
| `workload_identity.enabled: true` with a configured key (`signing_key_pem` or `signing_key_env`) | The workload-identity key. It is the one Envoy validates through `/api/v1/tokens/workload/jwks`. |
| Anything else, including local mini mode | `forge_mcp.session_tokens.signing_key_file` (default `~/.niuu/forge-session-signing-key.pem`) |

The key file is generated once as a 2048-bit RSA key. It is written atomically with
mode 0600 in a 0700 directory, and is never overwritten. Forge refuses a key file that
other users can read (`chmod 600` it), is owned by someone else, or is not an RSA PEM.
Because the key persists, tokens held by running brokers keep verifying after a Forge
API restart.

When session credentials are enabled but no key is usable, Forge refuses to start and
the error names the remedy. Set `forge_mcp.session_tokens.enabled: false` to turn
session credentials off on purpose: sessions then run on their broker credential and
get no Forge MCP grants, while read tools and `notify` still work through the broker.
The Volundr Helm chart leaves them off unless `workloadIdentity` is enabled with
`signingKey.existingSecret` (the pod's root filesystem is read-only, so the key file
cannot be created there); set `forgeMcp.session_tokens.enabled` to override.
`GET /api/v1/forge/feature-flags` reports `capabilities.forge_session_tokens` and
`capabilities.forge_session_token_key` (`workload_identity` or `key_file`).

### Where the credential is delivered

- **Local process mode (mini mode):** the broker's process environment.
- **Flux and Kubernetes pod managers:** a Secret per session, when
  `forge_mcp.session_tokens.projection_adapter` is set (the Volundr chart's
  `sessionCredentialProjection.enabled`). `KubernetesExecutionCredentialProjection`
  writes `forge-session-<session hex>` in the session namespace and mounts it
  read-only; the HelmRelease values carry only the file path (Skuld's
  `forge_mcp.token_file`), never the token, because values are readable by anyone who
  can read HelmReleases. The Secret is re-written on every start and deleted when the
  session stops.
- **OpenShell pod manager, or Flux/Kubernetes without a projection:** not delivered.
  Forge does not mint for them; those sessions keep their broker credential and have
  no grants.

## Grants

| Grant | Adds scope | Tools |
|---|---|---|
| none | `forge:notify`, `forge:session:read`, `forge:session:secret` | `environment`, `notify`, `describe_session`, `list_notifications`, `list_sessions`, `get_session`, `session_transcript` |
| `message` | `forge:session:message` | `send_message`, `message_status` |
| `lifecycle` | `forge:session:lifecycle` | `create_session`, `start_session`, `stop_session` (never delete or archive) |

A session's grants are the union of four sources:

1. **The session-create request.** For example
   `POST /api/v1/forge/sessions {"name": "…", "forge_mcp": {"grants": ["message"]}}`.
   Putting `forge_mcp` inside the free-form `workload_config` is refused with 422.
2. **The launch spec** each start applies, through its
   `workload_config: {forge_mcp: {grants: [...]}}`. An unknown grant name fails the
   start instead of silently granting less.
3. **The node default**, `forge_mcp.default_grants` (empty by default). It is applied
   at every mint.
4. **The project role.** A session filed in a project carries the grants of its role
   (`coordination.role`) from `forge_mcp.role_grants`, applied at every mint. By
   default a `coordinator` gets `lifecycle` and `message`, so it can create, start,
   stop and steer its workers. Workers, any role not listed and sessions in no project
   get none. Set `role_grants: {}` to grant nothing by role.

Grants from the first two sources, and those an operator sets (below), are stored on
the session row (`workload_config.forge_mcp.grants`), so they survive restarts and are
re-minted on every start. The node default and role grants are not copied onto the
row: changing the configuration or the session's role changes the next mint.

A caller holding a session credential cannot give a session a grant it does not hold
itself. That covers creating a session with more grants, starting a peer with a launch
spec that adds some, and creating a session filed under a role whose `role_grants` it
does not hold (a coordinator without `message` cannot create another coordinator).
Each is refused with 403 before anything changes, including the project dispatch claim.

### Changing an existing session's grants (operators)

```bash
# Read the grants by source and what the next start's credential carries
curl -s http://127.0.0.1:8080/api/v1/forge/sessions/<id>/forge-mcp

# Replace the session's stored grants; [] removes them
curl -s -X PUT http://127.0.0.1:8080/api/v1/forge/sessions/<id>/forge-mcp \
  -H 'Content-Type: application/json' -d '{"grants": ["message", "lifecycle"]}'
```

Both answer:

```json
{
  "session_id": "…",
  "grants": ["lifecycle", "message"],
  "default_grants": [],
  "role": "worker",
  "role_grants": [],
  "effective_grants": ["lifecycle", "message"],
  "applies_at": "next_start"
}
```

- `PUT` replaces `workload_config.forge_mcp.grants` and nothing else. The running
  launch keeps the credential it has. **Restart the session to apply**: the next
  start mints a credential with `effective_grants`, and the previous one is revoked.
- Only an operator may call either route: a person, a PAT, or the anonymous local user
  in allow-all mini mode. **Any scoped credential gets 403**, a session's own
  (`forge_session`, whatever grants it holds) or a build token (`valkyrie_build`), so
  a session can never widen its own grants or a peer's. The routes are not on the
  session-credential allow-list either.
- Every change is audited as a `volundr.session.forge_mcp_grants_changed` event in the
  audit log (`GET /api/v1/audit/events?event_type=volundr.session.forge_mcp_grants_changed`),
  with the session, owner, acting user, previous grants and new grants. If the audit
  write fails, the request fails (500); the row may already be changed, so read it back
  with `GET` and repeat the `PUT`.
- A launch spec's grants are added back at its next start, and the node default and
  role grants always apply. Removing a grant here does not remove those.
- On a Guild host the routes forward to the session's owning Forge.

Skuld reads the grants from the token's `scopes` claim. It decodes the claim without
verifying it, only to decide which tools to offer, because Forge enforces. An explicit
`SKULD__FORGE_MCP__GRANTS` / `forge_mcp.grants` can only narrow those grants, never
widen them. Without a token, no grant is offered. The `environment` tool reports the
effective `grants`, the offered `tools` and the credential in use
(`forge_credential.kind`: `forge_session` or `broker`), never the token itself. Those
are the grants actually minted into this launch's token, from all four sources. After
an operator change they move only once the session restarts.

## Secret requests (Forge REST)

An agent that needs a secret (a token, a password) asks its session's **owner**; the
value never enters the transcript, the event log, the chat WebSocket or the model's
context. The owner types it into a masked card, which posts it straight to Forge with
their own login. The agent claims it once and gets a file path, not the value.

All routes are under `/api/v1/forge/sessions/{id}/secret-requests`:

| Route | Caller | Does |
|---|---|---|
| `POST` | the session's token (scope `forge:session:secret`), or the owner | Open a request `{name, purpose, target?}`: 201, or 200 with the request already open for that name |
| `GET`, `GET /{rid}` | same | List (newest first, `?status=`) or read |
| `POST /{rid}/fulfill` | **the owner only** | `{value, keep}`; `keep` is `session` or `saved` |
| `POST /{rid}/decline` | **the owner only** | Pending requests only |
| `POST /{rid}/claim` | the session's token, or the owner | Returns `{name, value}` once, `Cache-Control: no-store` |
| `POST /{rid}/cancel` | the session's token, or the owner | Withdraws a pending or fulfilled request |

- **Name** is `^[A-Z_][A-Z0-9_]*$`, at most 255 characters (it becomes a file name); anything else is 422.
- **Status** is `pending`, `fulfilled`, `delivered`, `declined` or `cancelled`. A wrong-state action is 409.
- **Fulfil and decline refuse every scoped credential** (a session token, a build token, a scoped
  PAT) with 403, and are not on the session-credential allow-list, so an agent can never answer
  its own request.
- **Where the value lives.** Only in the credential store (OpenBao on a cluster), under the owner
  (`user`, the session's owner), as `{"value": …}` with metadata `source`, `session_id` and
  `request_id`. The `session_secret_requests` table holds no value. `keep: session` stores it as
  `session-secret-<request id>` and deletes it at claim, cancel or the end of the session.
  `keep: saved` stores it as the kebab-case name (`GITLAB_TOKEN` becomes `gitlab-token`),
  overwriting, and keeps it.
- **Session end.** When a session stops (or is found dead), is archived or is deleted, its open
  requests become `cancelled` and their session-only values are deleted. A deletion that fails
  fails the stop or delete, so a lingering secret is never silent; a failed stop leaves the
  session `failed`, which can be stopped again.
- Values are never logged, never in an error message, and returned by no route except `claim`.
  Malformed fulfil bodies get a 422 that does not echo the input.

## Questions from agents

An agent's question reaches the user as a notification they can answer in one tap. The
`notify` tool, the Forge MCP server instructions (stdio and HTTP), the session prompt and
the `forge-notify` skill all state the same standard (`niuu.question_standard`):

> A question to the user is a closed question they can answer in one word or one tap: it
> names the choice, gives at most two sentences of context, lists 2–4 options with the
> recommendation marked, and says what happens if they do not answer (the default and
> when it applies). Send it as `kind=decision` (they must choose) or `kind=attention`
> (they must act or unblock) with `question=true` and an `ask`: options with one
> `recommended=true`, and a `default {option, after}`. Progress, findings and "here is
> where I am" are not questions: send them as milestone or info. Never two questions in
> one notification.

```json
{"kind": "decision", "title": "Postgres or SQLite for the cache?", "question": true,
 "ask": {"items": [{"id": "database", "kind": "question",
   "text": "Which database for the cache?",
   "options": [{"label": "Postgres", "description": "Already deployed", "recommended": true},
               {"label": "SQLite"}],
   "default": {"option": "Postgres", "after": "PT4H"}}]}}
```

- `options[].recommended`: at most one per item. A label ending in `(Recommended)`
  still counts, for older agents.
- `default`: `option` is an option label or `none`. `after` is an ISO-8601 timestamp
  with a timezone, or a duration from the notification (`PT4H`, `P1D`). Forge only
  records it: the asking session carries out its own default.
- `deadline`: optional ISO-8601 timestamp.

Every field is served as sent on `GET /notifications`, the per-session list and the
`session_notification` event. The field rules are in
`docs/forge/session-notifications-contract.md` §12.

**Lint.** `notify` checks a `decision` or `attention` before recording it:

| The notification | Recorded as | Tool result |
|---|---|---|
| has an `ask` with one recommended option and a `default` per item | sent | as usual |
| has an `ask`, but a choice has no recommended option or an item has no `default` | sent | `warnings` with the standard |
| has no `ask`, but its title asks (`?`) or its body lists two or more options | sent | as usual (a free-text question) |
| has no `ask`, no question in the title and no options in the body | **`info`** | `kind: "info"`, `requested_kind`, and `warnings` quoting the standard |

The downgrade is also logged at warning level by the MCP server. Direct REST submits
are not linted.

## What a session credential may call

Forge verifies a `forge_session` bearer in every identity mode, allow-all/mini mode
included. It accepts the token in the `Authorization` header or in `?token=`, and a
valid one takes precedence over `x-auth-*` headers and the anonymous dev principal.
An invalid, expired or revoked one gets **401**, never anonymous access.

Only the routes below accept a session credential. **Every other route answers 403**,
so a route added later stays closed until it is added here. The allow-list lives in
`niuu.domain.services.forge_session_policy`.

In the binding column: *own* means the path's session must be the token's own
session. *owned* means any session of the same owner. *peer* means any **other**
session of the same owner.

| Route family (under `/api/v1/forge`) | Scope | Binding |
|---|---|---|
| `GET /notifications`, `GET`/`PUT /notifications/read-state` | `forge:session:read` | owner's feed |
| `GET /sessions/{id}/notifications` | `forge:session:read` | owned |
| `POST /sessions/{id}/notifications` (direct submit, recorded as `source=agent`) | `forge:notify` | own |
| `GET /sessions` | `forge:session:read` | owner's sessions only |
| `GET /sessions/{id}`, `/conversation`, `/conversation/turns/{turn}`, `/log`, `/log/head`, `/transcript`, `/transcript/download`, `/message-deliveries/{request_id}` | `forge:session:read` | owned |
| `POST /sessions/{id}/messages` | `forge:session:message` | peer |
| `POST /sessions` | `forge:session:lifecycle` | acts as the owner |
| `GET /projects/{id}` | `forge:session:lifecycle` | the project a new session joins: the Guild facade reads it on the node before the create; the owner's access still applies, and listing, creating or changing projects stays denied |
| `POST /sessions/{id}/start`, `/resume`, `/stop` | `forge:session:lifecycle` | peer |
| `POST /sessions/{id}/log`, `/activity`, `/usage` | `forge:notify` | own |
| `PUT /sessions/{id}/description` (never `PUT /sessions/{id}`) | `forge:notify` | own |
| `POST /mcp` | none. Every tool call re-enters this table. | none |

Everything else is denied to session credentials. That includes the Forge MCP grants
routes (`GET`/`PUT /sessions/{id}/forge-mcp`), notification rules,
sinks and delivery rows, delete, archive and restore, session updates, files, logs,
the session stream, admin settings, credentials, PATs, integrations and the session
proxy.

The same scopes are also checked by `require_scope(...)` on each route. Scopes come in
families: a `valkyrie_build` token is not affected by the session scopes, and a
session token is refused wherever no session scope applies.

## The Guild facade: local node only

A session credential is local to the node that minted it. The Guild facade therefore:

- applies the same route and scope allow-list before forwarding (403 at the edge);
- forwards the credential **only to the local (embedded) Forge**, which verifies it
  and checks the bindings;
- reads only the local node when a session credential lists sessions or
  notifications. That node's refusal (a revoked token, for example) is returned,
  not hidden as an empty page;
- refuses to send the credential to any other node, with **403**: "Forge session tokens
  are local credentials: peer supervision is limited to the session's own Forge
  node". This applies to an explicit `instance_id`, a remote default target and a
  remote-only session. A session that exists only on another node is simply not
  found.

To supervise sessions across nodes, use a PAT or your own identity against the Guild,
or connect to that node's own endpoint.

### Messages to the parent or project on another node (LEX-1710)

One exception, for `send_message` only: a session with the `message` grant can message
**its parent session and the sessions of its own project** on another node of its Guild.
Nothing wider crosses nodes.

1. The session sends `POST /sessions/{id}/messages` to its own node, as usual. That
   node has verified the credential. When the target is not local, it relays the
   message **without the credential** to
   `POST /api/v1/forge/sessions/{id}/messages/relayed` on the owning node. The body
   carries the sender, as the home node attests it: session id and name, node name,
   project, parent.
   - The owning node is found among this host's registry, else through the Guild
     this host belongs to (`guild.url`).
   - A host with neither answers 404 and says to set `guild.url`.
2. The owning node checks the relation against its own copy of the target session:
   the target is the sender's `coordination.parent`, or it is in the sender's
   `coordination.project_id`.
   - Otherwise it refuses with 403 and the reason.
   - If the relation holds, it delivers through the normal route, with the same
     delivered / pending / failed answer. The message is prefixed with
     `[Message from session <name> (<id>) on <node>, relayed through the Guild]`.
3. A session credential cannot call the relay route itself (403); it is not on the
   session allow-list.

The relay carries no credential, so it works only where the owning node accepts an
unauthenticated caller: mini-mode hosts (`AllowAll` identity) on a tailnet. On a node
that requires a login, the relay is refused with 403: "… requires a login … need a node
credential, which is not configured". A node credential for relays is the planned
follow-up (signed node requests, as `niuu join` uses for heartbeats; it needs a decision
recorded in `.claude/rules/architecture.md`).

**Setup on a host that is not the Guild** (e.g. mini-m6 in Thor's Guild): set
```yaml
guild:
  url: http://thor.tail737f2a.ts.net:8080   # the Guild's facade
```
Leave `guild.instance_*` empty unless this host's notifications should carry the Guild's
instance ids. The Guild itself needs nothing: its registry already lists every node.

Outside `/api/v1/forge` the Guild host refuses session credentials outright.

## The Forge-hosted HTTP endpoint

`POST /api/v1/forge/mcp` serves the same tools over MCP Streamable HTTP in JSON
response mode. It lives under `/api/v1/forge`, not the contract's provisional
`/mcp/forge`, so the Guild host's Forge route domain dispatches it and it sits behind
the same Envoy rules as the rest of the Forge API.

- **Protocol.** One JSON-RPC 2.0 message per POST: `initialize`, `ping`,
  `tools/list` and `tools/call`.
  - A request gets `200 application/json`; a notification or response gets `202`.
    Batches are refused.
  - `initialize` echoes a supported `protocolVersion` (2025-11-25, 2025-06-18 or
    2025-03-26) and otherwise offers 2025-11-25.
  - An unsupported `MCP-Protocol-Version` header is `400`.
  - The server is stateless: it issues no `Mcp-Session-Id`. `GET` and `DELETE`
    answer `405`.
- **Transport checks.**
  - `Content-Type` must be `application/json` (415).
  - `Accept` must allow JSON (406).
  - The body is bounded by `forge_mcp.http.max_body_bytes` (413).
  - A request with an `Origin` header is refused (403) unless the origin is listed in
    `forge_mcp.http.allowed_origins` (empty by default). This guards against DNS
    rebinding and CSRF. Agents and CLIs send no `Origin`.
- **Authentication.** The caller's own credential. Each tool call is an in-process
  REST call into the same Forge app, carrying the caller's `Authorization` and
  `x-auth-*` headers, so Forge's normal authentication, owner checks and the session
  allow-list apply to every call.
  - A user (PAT or Envoy identity) is offered every tool. Forge still authorizes each
    call as that user.
  - A session credential is offered its own grants.
  - In allow-all mini mode, anonymous callers are the local dev user, as with the
    REST API.
- **`notify`.** A feed-only direct submit. Here it takes `idempotency_key` (required),
  plus `session_id` and optionally `instance_id`.
  - A session credential notifies its own session by default and is recorded as
    `source=agent`.
  - Anyone else must name a session they own and is recorded as `source=operator`.
  - Retrying with the same key returns the same notification.
- **Scope.** The endpoint acts on the node that serves it. Tools refuse an
  `instance_id`, because the endpoint cannot silently act on its own node instead.
  - On a Guild host, `/api/v1/forge/mcp` reaches the local node.
  - A user can add `?instance_id=<node>` to reach another node's endpoint through
    the facade. A session credential cannot.

### Client configuration

Claude Code (`.mcp.json`, or `claude mcp add --transport http …`):

```json
{
  "mcpServers": {
    "forge": {
      "type": "http",
      "url": "https://forge.example.com/api/v1/forge/mcp",
      "headers": { "Authorization": "Bearer ${FORGE_PAT}" }
    }
  }
}
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.forge]
url = "https://forge.example.com/api/v1/forge/mcp"
bearer_token_env_var = "FORGE_PAT"
```

Lexi, OpenClaw and any other Streamable HTTP MCP client:

```json
{
  "name": "forge",
  "transport": "streamable-http",
  "url": "http://127.0.0.1:8080/api/v1/forge/mcp",
  "headers": { "Authorization": "Bearer <PAT>" }
}
```

Create a PAT with `POST /api/v1/tokens` (or the web settings). On a local mini-mode
host the endpoint also works without a token, as the local dev user.

## Configuration

```yaml
forge_mcp:
  default_grants: []                 # message, lifecycle: added to every session
  role_grants:                       # by project role (coordination.role), at every mint
    coordinator: [lifecycle, message]   # the default; {} grants nothing by role
  session_tokens:
    enabled: true                    # false: no session credentials, no grants
    ttl_seconds: 2592000             # backstop; revocation is tied to the launch
    signing_key_file: ~/.niuu/forge-session-signing-key.pem
    signing_key_bits: 2048
    issuer: niuu-forge-session       # for tokens signed with the key file
    key_id: niuu-forge-session
    audiences: [volundr-api]
  http:
    enabled: true
    allowed_origins: []              # browser origins allowed to call /api/v1/forge/mcp
    list_default_limit: 20
    list_max_limit: 50
    transcript_default_turns: 10
    transcript_max_turns: 30
    transcript_turn_max_chars: 2000
    output_max_chars: 24000
    request_timeout_seconds: 20
    max_body_bytes: 1048576
```

Skuld (normally set by Forge, not by hand):

| Setting | Meaning |
|---|---|
| `SKULD__FORGE_MCP__TOKEN` | The session credential. It is only the bearer for MCP-proxied Forge calls. |
| `SKULD__FORGE_MCP__GRANTS` | Optional narrowing of the token's grants (`message,lifecycle` or a JSON list). Unset keeps the token's grants. |

## Threat model

In local mode every session runs as the same Unix user. A deliberately malicious
local process can read another process's environment, or call Forge anonymously in
allow-all mode. The session credential stops tool-level spoofing and accidental
cross-session actions: a model can only notify as its own session, cannot message
itself, and cannot act on another owner's sessions. It does not sandbox a hostile
local user. With Envoy and an IDP, anonymous access is gone, and the session
credential is then the only authority a session's MCP holds.

## Troubleshooting

- **`401 Forge session token revoked: the session has been restarted`.** A broker from
  an earlier launch is still running. Stop it, or restart the session.
- **`401 … not enabled on this Forge`.** Forge has no session-credential issuer. Check
  the startup log and `capabilities.forge_session_tokens`.
- **`403 Forge session tokens may not call …`.** The route is not on the allow-list.
  This is by design.
- **`403 Token is missing the required scope: forge:session:message`.** Grant `message`
  on the session (`PUT /sessions/{id}/forge-mcp`, the create request or a launch spec),
  or file it as a project coordinator, and restart it. `environment` shows the grants
  the running launch holds.
- **`403 A forge_session credential cannot change Forge MCP grants`.** By design: ask
  an operator to set them.
