# Architecture Rules

## Hexagonal Architecture

Every package under `src/` (`volundr`, `skuld`, `ravn`, `ting`, `niuu`,
`bifrost`, `mimir`, `sleipnir`, `observatory`, …) follows the same shape.
Domain logic depends on **ports** (interfaces), adapters implement them, and
each package's composition root wires the implementations together.

```
src/<package>/
├── domain/     # Models and services (business logic)
├── ports/      # Interfaces (abstract base classes / protocols)
├── adapters/   # Implementations of ports (inbound: REST, WebSocket; outbound: DB, HTTP, K8s…)
└── main.py     # Composition root (some packages use app.py)
```

## Layer Rules

- **Domain** imports from `ports/` only, never from `adapters/`
- **Adapters** import from `ports/` for the interfaces they implement
- **Composition roots** (`main.py`, `app.py`, CLI builders) import from everywhere
- Package-to-package imports follow `module-boundaries.md` and
  `ravn-niuu-boundary.md`
- New adapters are selected dynamically from configuration
  (`dynamic-adapters.md`)

## Communication

The mechanisms are distinct; see `ravn-niuu-boundary.md` for ownership:

- **Service APIs** (REST, WebSocket, SSE) between services and clients
- **Event bus** (Sleipnir) for events between services, over configured
  transports
- **Collaboration rooms** for shared conversation among agents and humans
- **Flokk mesh** for direct communication among members of a flock
- **A2A** for discovery and task interaction with external agents and workflows

Persistence is PostgreSQL through raw SQL (`database.md`), plus files where a
component owns durable file-backed state (for example Mímir sources and pages).

## Authentication & Authorization

- **Never build custom auth/token layers** — always delegate to standard OIDC/OAuth2 flows
- **IDP-agnostic** — code must not be coupled to a specific identity provider (Keycloak, Entra ID, Okta, etc.). Use the identity adapter pattern to abstract the IDP
- All authentication goes through Envoy + the configured IDP in production
- Service-to-service auth uses standard OIDC flows (e.g. `client_credentials` grant), not internal bypasses or custom tokens

### Exception: Personal Access Tokens (PATs)

PATs are an intentional exception to the "no custom tokens" rule. Ting's autonomous
dispatcher must call Volundr as a specific user without an active browser session.
PATs are long-lived JWTs signed with the same symmetric key that Envoy validates,
so they integrate with the existing infrastructure without requiring IDP changes.
The shared PAT code lives in `src/niuu/` (service, port, adapter, model).

### Exception: scoped workload tokens (workload identity)

The same sanctioned exception covers the short-lived tokens minted by the
workload-identity exchange (`POST /api/v1/tokens/workload/exchange`). When the
exchange request asks for `scopes`, the issued JWT carries
`token_use: "valkyrie_build"` and a `scopes` claim bounded to
`KNOWN_WORKLOAD_SCOPES` (`src/niuu/domain/services/token_scope.py`) — a
least-privilege credential that can do one named thing and nothing else,
enforced fail-closed at that entry point (Forge session create, Ting workflow
launch, Observatory topology push). Tokens without
`token_use == "valkyrie_build"` are never scope-checked, so human sessions and
ordinary PATs are unaffected.

The `valkyrie_build` claim value is historical — builds were the first use.
It means "this credential is scoped", not "this credential builds"; the
mechanism is general and scopes are not limited to builds.

`KNOWN_WORKLOAD_SCOPES` is deliberately a constant, not configuration: it is
the allowlist that stops a caller self-granting privilege, so anything able to
edit it can mint authority. A scope is also only real because code enforces
it — `require_scope("x")` on a route is what gives `"x"` meaning, so a
config-only scope would grant nothing while reading as protection.

Known limitation (deliberate, for now): PATs themselves cannot carry scopes —
a leaked PAT retains its owner's full authority. Off-cluster Valkyries using
a PAT via `external_token_env` therefore do not get least-privilege; scoped
PAT minting is future work. When adding a NEW scoped entry point, add its
scope to `KNOWN_WORKLOAD_SCOPES` and a `require_scope(...)` dependency on the
route — scoped tokens are only as narrow as the enforcement coverage.
`tests/test_niuu/test_token_scope.py` fails if the two drift apart in either
direction.

### Exception: session-bound Forge credentials (`forge_session`)

Forge mints a second kind of scoped workload token itself, each time it
launches a session, and hands it to that session's broker for the Forge MCP
(`niuu.domain.services.forge_session_token.ForgeSessionTokenService`). It is
sanctioned on the same grounds as `valkyrie_build`: a broker acting for a
session needs a credential narrower than its owner's, and there is no OIDC
principal for "this one session".

- The JWT carries `token_use: "forge_session"`, `sub` = the session owner,
  `workload_session_id` = the one session it is bound to and
  `workload_launch_id` = that launch. It is signed by the workload-identity
  issuer when that has a configured key, otherwise (mini mode) by Forge's own
  key file (`forge_mcp.session_tokens`). Enabled with no usable key is fatal at
  startup, never "mint nothing".
- Its `scopes` are bounded to its own family, `FORGE_SESSION_SCOPES`:
  `forge:notify` and `forge:session:read` for every session, plus
  `forge:session:message` / `forge:session:lifecycle` only through an operator
  grant. The family is part of `KNOWN_WORKLOAD_SCOPES` and keyed by
  `SCOPES_BY_TOKEN_USE` (`src/niuu/domain/services/token_scope.py`) — a
  constant, for the same reason as above.
- Enforcement is deny-by-default: at the edge a `forge_session` token is
  admitted only on the Forge session route allow-list
  (`niuu.domain.services.forge_session_policy`); at a route, `require_scope(...)`
  admits it only with one of the named scopes of its own family, and a check
  naming none of them denies it (unlike `valkyrie_build`, where such a check
  does not apply). Forge then verifies every presented token against the live
  session row (`volundr.adapters.inbound.forge_session_auth`): the session must
  exist, be on the same launch, and not be stopped or archived, so restarting a
  session revokes the previous launch's token.

Known gap (deliberate, for now): only the local process pod manager delivers
the credential to the broker (`delivers_forge_session_token`). The Kubernetes,
Flux and OpenShell pod managers do not, so sessions they launch have no Forge
MCP credential. The Volundr chart therefore leaves `session_tokens` off unless
workload identity signs with a configured key. When adding a NEW Forge route a
session broker may call, add it to the allow-list and give it a
`require_scope(...)` naming a `forge:*` scope.

### Exception: Ed25519 node-request signing (`niuu join`)

A joined node's heartbeat/leave calls (`POST /api/v1/niuu/guild/nodes/{id}/heartbeat`,
`POST /api/v1/niuu/guild/nodes/{id}/leave`) carry no bearer token of any
kind — not a JWT, not a PAT, not a scoped workload token. A node signs
`f"{method}\n{path}\n{timestamp_ms}\n{sha256(body).hexdigest()}"` with an
Ed25519 keypair generated on first `niuu join` and never uploaded (only the
public key is; see `cli.auth.node_key.NodeIdentity`), and Guild verifies
that signature against the stored public key
(`niuu.ports.node_verifier.RegisteredNodeVerifier`, implemented by
`niuu.adapters.node_signature.Ed25519NodeVerifier`). This is sanctioned
because these two endpoints authenticate a *machine*, not a human or a
service acting on a human's behalf — there is no OIDC principal to obtain a
token for, and the alternative (a long-lived bearer credential embedded in
every joined machine) is strictly worse than a private key that never
leaves the host.

Limits of this exception, enforced together, not individually:

- **Replay protection is the timestamp, and only the timestamp.** It must
  strictly increase per node, advanced by a single atomic conditional
  `UPDATE ... WHERE last_request_at IS NULL OR last_request_at < $timestamp`
  (`PostgresNodeRepository.try_advance_watermark`) — a read-then-write check
  is not atomic and admits a race where two concurrent requests both pass.
  Milliseconds, not seconds: two signed requests in the same second must
  both be able to advance the watermark.
- **A bounded clock-skew window** (`niuu.node_join.clock_skew_seconds`) —
  an unbounded window would let a captured signed request replay
  indefinitely up to the node's next legitimate call.
- **Every rejection reason returns the identical 401** (unknown node id,
  malformed node id, missing headers, bad signature, stale timestamp,
  failed replay check) — distinguishing them would let a caller probe which
  node ids exist.
- **The signing scheme covers method, path, timestamp, and a body hash** —
  omitting any of them (e.g. signing only the body) would let a captured
  signature be replayed against a different route or a different node.
- **This exception is scoped to exactly these two routes.** `POST
  /api/v1/niuu/guild/join` is a *human-authorized* action gated by the
  ordinary `node_join` scoped-workload-token mechanism above, not this one —
  a node has no signing key yet when it joins. Do not extend Ed25519
  signing to a new route without adding it to the same enforcement points
  this one touches: the FastAPI dependency, the JWT-identity middleware
  exemption (`niuu.adapters.pat_revocation_middleware`), and the Guild
  chart's Envoy `jwt_authn` bypass (`charts/guild/templates/envoy-configmap.yaml`,
  an exact `safe_regex` match — never a `prefix`, which would also exempt
  routes that must stay JWT-gated).
