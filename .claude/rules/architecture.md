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
