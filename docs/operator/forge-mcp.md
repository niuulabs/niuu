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
| `scopes` | `forge:notify` and `forge:session:read` always, plus one scope per grant |
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
- **Kubernetes, Flux and OpenShell pod managers:** not delivered yet. Forge does not
  mint for a pod manager that cannot hand the token over privately, because a plain
  env value in a Deployment manifest would be readable by anyone who can read
  Deployments. Those sessions keep their broker credential and have no grants.

## Grants

| Grant | Adds scope | Tools |
|---|---|---|
| none | `forge:notify`, `forge:session:read` | `environment`, `notify`, `list_notifications`, `list_sessions`, `get_session`, `session_transcript` |
| `message` | `forge:session:message` | `send_message`, `message_status` |
| `lifecycle` | `forge:session:lifecycle` | `create_session`, `start_session`, `stop_session` (never delete or archive) |

A session's grants are the union of three sources:

1. **The session-create request.** For example
   `POST /api/v1/forge/sessions {"name": "…", "forge_mcp": {"grants": ["message"]}}`.
   Putting `forge_mcp` inside the free-form `workload_config` is refused with 422.
2. **The launch spec** each start applies, through its
   `workload_config: {forge_mcp: {grants: [...]}}`. An unknown grant name fails the
   start instead of silently granting less.
3. **The node default**, `forge_mcp.default_grants` (empty by default). It is applied
   at every mint.

Grants from the first two sources are stored on the session row
(`workload_config.forge_mcp.grants`), so they survive restarts and are re-minted on
every start. A caller holding a session credential cannot give a session a grant it
does not hold itself. That covers creating a session with more grants, and starting
a peer with a launch spec that adds some. Either is refused with 403 before anything
changes.

Skuld reads the grants from the token's `scopes` claim. It decodes the claim without
verifying it, only to decide which tools to offer, because Forge enforces. An explicit
`SKULD__FORGE_MCP__GRANTS` / `forge_mcp.grants` can only narrow those grants, never
widen them. Without a token, no grant is offered. The `environment` tool reports the
effective `grants`, the offered `tools` and the credential in use
(`forge_credential.kind`: `forge_session` or `broker`), never the token itself.

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
| `POST /sessions/{id}/start`, `/resume`, `/stop` | `forge:session:lifecycle` | peer |
| `POST /sessions/{id}/log`, `/activity`, `/usage` | `forge:notify` | own |
| `POST /mcp` | none. Every tool call re-enters this table. | none |

Everything else is denied to session credentials. That includes notification rules,
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
  on the session (create request or launch spec) and restart it.
