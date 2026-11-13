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

### Registered-host pairing codes (`compute_host_join`)

A bare-metal host joins a Forge's compute pool with a pairing code minted by
that Forge (`volundr.domain.services.compute_hosts.ComputeHostService.mint`),
through the scoped-workload-token mechanism above: `token_use:
"valkyrie_build"`, `scopes: ["compute_host_join"]`, an empty roles claim, and
single use — the code's hash, expiry and consumption live in
`compute_host_pairing_codes`, and everything the host will be (pool,
execution, connector, tags, slots) is fixed there by the admin, never taken
from the joining host. `compute_host_join` is issuer-only (`ISSUER_ONLY_SCOPES`):
the exchange never grants it. Its two routes, `GET …/compute/hosts/join.sh` and
`POST …/compute/hosts/join`, carry `require_scope("compute_host_join")`, and
the service also verifies the code's signature and looks up its hash, so a
mini-mode Forge without Envoy admits nothing else. At the edge
(`credential_allows_route`) the code is admitted on exactly those two routes.
It is signed by the workload-identity issuer when that has a configured key,
otherwise by Forge's own key file (`compute.hosts.pairing_signing_key_file`)
— and the key file only with the allow-all identity adapter of local mini
mode, where nothing but Forge checks the bearer. Any other combination is
fatal at startup (`_create_host_pairing_signer`). A joined host calls no
other route: Forge reaches it over SSH and reads its health itself, so the
node-signing exception below is not extended to hosts.

### Exception: the host connect endpoint (`ReverseSshConnector`)

A registered host behind NAT keeps one outbound SSH connection to an OpenSSH
sidecar of the Forge pod (the volundr chart's `hostConnect`) and forwards a port
on the pod's loopback to its own sshd. That endpoint is an authenticated entry
point that is not HTTP: SSH public-key authentication of a *machine* by the
Ed25519 key it registered with its `compute_host_join` pairing code. It is
sanctioned for the same reason as node signing: there is no OIDC principal for a
machine, and the key never leaves the host.

Limits of this exception, enforced together, not individually:

- **Only keys of registered, not-removed hosts whose connector is the reverse
  SSH connector are admitted, looked up live on every connection.** sshd has no
  authorized_keys file (`AuthorizedKeysFile none`); its `AuthorizedKeysCommand`
  asks Forge, on a Unix socket in a volume only Forge and the sidecar mount
  (`volundr.adapters.inbound.host_connect_keys`), never an HTTP route.
  `ComputeHostService.endpoint_key` answers only for a host of this pool whose
  connector admits it, and only for the connect user (`AllowUsers`). Removing a
  host takes effect at once (the connector stops answering before the row goes),
  and revoke also ends a live connection: the key command records sshd's
  per-connection monitor process, and the sidecar's reaper kills it when Forge
  writes the host's id to its pipe. A revoke that cannot end it is an error,
  never "done".
- **Each key may only listen on its own assigned loopback port and nothing
  else**: `restrict,port-forwarding,permitlisten="127.0.0.1:<port>",command="/usr/sbin/nologin"`,
  with `AllowTcpForwarding remote` and `PermitOpen none` in the endpoint's
  `sshd_config` so no key can open a connection anywhere (authorized_keys has no
  `permitopen="none"`; OpenSSH drops a line that carries it). No shell, PTY,
  agent, X11 or stream-local forwarding. The port is unique on the Forge
  (`idx_compute_hosts_tunnel_port`), from the connector's `port_range`; a full
  range refuses the join.
- **The endpoint listens in the Forge pod's network namespace only for this**,
  in one pod (`replicaCount: 1`, `Recreate`, no autoscaling; the chart refuses
  anything else), with its own pinned Ed25519 host key from a Secret.
- **What crosses it is an SSH session from Forge to the host's sshd, pinned to
  the same registered key**, so the endpoint never carries trust of its own:
  anything else answering on a port fails the pin.
- **Exactly one way in.** Do not extend this endpoint to another account,
  another kind of forwarding or an HTTP route without adding it to the same
  enforcement points: the connector's `endpoint_key_options`, the service's
  `endpoint_key`, and the chart's `sshd_config`.

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
  `forge:notify`, `forge:session:read`, `forge:memory` and `forge:session:secret` (ask the
  session's own owner for a secret and claim it once; supplying or declining a request stays
  the owner's alone and is closed to every scoped token) for every session, plus
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

Delivery: the local process pod manager hands the credential to the broker in
its environment (`delivers_forge_session_token`). The OpenShell pod manager
writes it to a private file (`~/.volundr/forge-session/token`, 0600) with
the sandbox's other credential files before the broker starts, and names only
the path in the broker's env (`delivers_forge_session_token` too). For Flux and Kubernetes session
pods, `forge_mcp.session_tokens.projection_adapter`
(`KubernetesExecutionCredentialProjection`, the Volundr chart's
`sessionCredentialProjection`) writes it to one Secret per session and mounts it
read-only; the HelmRelease values carry only the file path (Skuld's
`forge_mcp.token_file`), never the token, and the Secret is deleted when the
session stops. The broker uses it for Forge MCP calls, for durable
presented-file uploads, and for its session's own record (durable log, activity, usage,
and message delivery claims). The
Volundr chart turns `session_tokens` and this projection on wherever Forge has a signing key
(workload identity with `signingKey.existingSecret`, or `forge_mcp.session_tokens.signing_key_file`);
enabling them without one fails the render. When adding a NEW Forge route a session broker may call, add it
to the allow-list and give it a `require_scope(...)` naming a `forge:*` scope.

The memory gateway (the Mímir service, `docs/design/memory/README.md`) is the one
non-Forge service that accepts this credential, on exactly three routes:
`GET …/memory/tools`, `POST …/memory/tools/{name}` and `GET …/memory/context-pack`
(the only routes its identity middleware exempts). Its people routes
(`…/memory/proposals…`, `…/memory/promotions`, and `…/memory/graph`, the knowledge graph of the
layers that person reads) take a person's own login through Mímir's
identity adapter, refuse any credential carrying `workload_session_id` or that
`credential_allows_route` denies, and pass the person's `Authorization` on to Forge. Its
own service credential (`memory.service_auth`) is used only for
`POST /api/v1/forge/memory/ended`. It does not verify the token
itself. It reads the unverified `workload_session_id` only to address
`GET /api/v1/forge/sessions/{id}/memory-context` (scope `forge:memory`, the session's own
only), presents the same token there, and treats Forge's 200 as the verification; any
other answer is a 401. The broker makes these calls; the `memory` stdio server the agent
runs only reaches the broker's loopback routes, so the credential stays out of the agent.
Do not admit the credential on any other Mímir route.

### Exception: the memory gateway's service credential (`memory.gateway_service_subject`)

The memory gateway asks Forge when run and session layers ended
(`POST /api/v1/forge/memory/ended`) so it can delete them after retention. No person or
session is behind that call, so it uses the gateway's own workload-identity token: an
unscoped exchange token whose `act.sub` equals the configured
`memory.gateway_service_subject`. Forge admits only that token on that route.

- A `forge_session` credential, any scoped token or PAT, a person and any other service
  get 403. The session allow-list stays closed to the route.
- Where Forge's identity adapter is allow-all (local mini mode) nothing verifies callers,
  so the local caller passes, still never with a session or scoped credential.
- A Forge that verifies callers and has `memory.gateway_url` but no subject fails at
  startup, and the chart refuses to render.
- The exchange sets `act` only when the workload differs from the mapped owner, so map
  the gateway to an owner that is not its own subject.
- The platform front door asks every visible node and fails if one cannot answer, so an
  unreachable node never reads as "gone" (which would delete a live layer).

`GET /projects/{id}/role` and `GET /memory/runs/{id}` take a person's own login only;
they are not scoped-credential routes.

**Connector sources (`memory.connector_service_subjects`).** The memory gateway admits a
platform service's credential on exactly one more route, `POST …/memory/sources`, for
connectors such as Ting's Linear import to keep fetched material as immutable
`tool`-trust sources (author `tool:<connector>`, evidence `url:<origin_url>`) in a project
or org layer. The credential is an unscoped workload-identity token whose actor
(`act.sub`) is listed in `memory.connector_service_subjects`; an empty list admits
nobody. A person, a `forge_session` or other scoped credential and any other service get
403. A connector credential is refused on the session routes (401) and the people routes
(403). With the allow-all identity of local mini mode the local caller passes, still
never with a session or scoped credential. The route is not exempt from Mímir's identity
middleware.

### Exception: Forge session credentials at Bifröst (`forge:model`)

Bifröst accepts a `forge_session` credential carrying `forge:model` (every session has it)
on its three model routes only: `POST /v1/messages`, `/v1/chat/completions` and
`/v1/responses`, in every `auth_mode`, in front of the configured adapter. Every other
Bifröst route behaves as before. The memory gateway uses it to answer `memory_ask` with
the session's own credential, so the answer is charged to the session's owner.

- Bifröst never verifies the token itself. It reads `workload_session_id` unverified only
  to address Forge, and presents the credential to `GET /api/v1/forge/sessions/{id}`
  (`forge_session.base_url`, `.timeout_seconds`). Forge's 200 is the proof; the answer must
  name the same session, an owner and a tenant.
- Usage is charged to Forge's answer: agent = `owner_id`, tenant = `tenant_id`, session =
  the id. Attribution headers are never read for this credential.
- Nothing is cached, so a stopped session or a new launch loses access on its next call.
- Refusals are 401 naming why, a missing scope is 403, Forge unreachable is 503. Without
  `forge_session.base_url` the credential is refused.
- `credential_allows_route` admits the credential at the edge on these three routes only.

### Exception: mesh sender attestation (`mesh_peer` certificates)

The mesh transports (Sleipnir over nng, NATS or RabbitMQ; the webhook adapter; in-process)
authenticate a connection, never a sender: any member can put any session id, peer id or
reply route in what it sends. A mesh peer therefore proves who it is with a Forge-signed
certificate and an Ed25519 signature per message (`niuu.mesh.attestation`, applied by the
`AttestedMesh` decorator in `niuu.adapters.mesh.attested`). The signature, not the
transport, is what proves the sender on every transport; NATS credentials or subject
permissions, where a deployment adds them, are an extra layer and never a substitute.

The certificate is another scoped workload credential, sanctioned on the same grounds as
`forge_session`: there is no OIDC principal for "this one mesh peer", and a peer that
holds a bearer others could replay would be worse than one holding a private key that
never leaves its process.

- `token_use: "mesh_peer"`, `aud: ["niuu-mesh"]`, **no scopes, no roles, and a `sub` that
  is not a person** (`mesh-peer:<peer id>`). Its scope family is empty and deny-by-default
  (`MESH_PEER_SCOPES`), `credential_allows_route` admits it on **no route at all**, not
  even `identity/me`, and no Forge or Envoy audience list contains `niuu-mesh`. A peer
  that receives one cannot replay it against Forge. `tests/test_adapters/test_mesh_identity_rest.py` fails if any
  Forge route admits a `mesh_peer` token.
- Forge sets every claim: peer id, kind (`session`, `resident`, `authority`), the
  session and launch, the memberships (`tenant:`, `flock:`, `realm:`), and the peer's
  Ed25519 public key, which is all the peer supplies. The private key is generated in the
  peer's process memory and never leaves it.
- **One route issues and renews it:** `POST /api/v1/forge/sessions/{id}/mesh-identity`,
  scope `forge:mesh:identity` (in `FORGE_SESSION_SCOPES`), `Binding.OWN`: a session asks for
  its own, with its `forge_session` credential. Forge refuses a stopped session or a stale
  launch, and refuses a peer id another live holder has claimed (`mesh_peer_claims`).
  Its answer carries the JWKS, so a receiver verifies offline: no Forge call per message,
  none for an unknown key (it is refused as `unknown_key` until the next renewal, so a
  flood of unknown keys cannot become a flood of requests).
- **The lifetime is bounded and short** (`forge_mcp.mesh_identity.ttl_seconds`, default 15
  minutes) and renewed in the background at half of it. It bounds only how long a leaked or
  stale certificate is believed, because stopping a session also announces it:
- **Revocation is a signed event.** When a session stops or restarts, Forge publishes
  "session stopped" (session id, launch id) on `mesh.revocation`, signed with Forge's own
  `authority` certificate. Receivers drop that launch at once, and honour a revocation
  only from an `authority` certificate. The TTL is the backstop for a receiver that was
  offline for the event.
- **Membership, not ownership.** A receiver refuses a sender that shares no tenant, flock
  or realm with it. It does not compare owners, so flocks of residents and sessions with
  different owners, and shared organization spaces, work.
- **Replay.** A signature covers its purpose, recipient, signing time and the whole
  message, so it cannot be moved to another recipient, topic or message; requests and
  replies are fresh within `mesh.attestation.clock_skew_s`, events within
  `mesh.attestation.event_max_age_s`. A replay inside the window repeats what the sender
  really sent, and `message_id` and `event_id` dedup is the inbox's.
- **An event's certificate is judged at signing time**, so a bus redelivery still verifies;
  an event can therefore be accepted up to `mesh.attestation.event_max_age_s` after its
  certificate expired. The bound for a stopped or compromised session is the certificate
  lifetime plus that age, and the signed stop notice (which only reaches receivers on a
  networked bus) ends it sooner.
- **Discovery announcements are signed** by the discovery adapters that share the bus
  (`event_bus`, `sleipnir`) through the same peer identity (`MeshAttestor`), bound to the peer
  they describe and ordered per peer.
- **Platform events are signed by platform services.** Forge (its `authority` certificate) and
  every other service that publishes on the bus (Ting; Mímir and Bifröst when wired) sign them
  through `niuu.mesh.platform_events.SigningBus`, and a subscriber that *acts* on one
  (`ObservationRelay`, Ting's event triggers) checks it with `PlatformEventVerifier`. The
  certificate kind is `service` (no scopes, no memberships, peer `service:<name>`) and a service
  issues it to itself, in process, with the key it already has: the workload-identity signing key
  where the deployment configures one, else the host key file Forge signs session credentials
  with (`niuu.mesh.platform_identity`). There is no issuing route: the key is the proof that a
  service is part of the platform, so a service without it (Ting in its own pod without
  `workloadIdentity.signingKey`) cannot sign and, with signing required, fails startup naming the
  fix. A `session` or `resident` certificate is refused as `not_platform_service`. The limits: a
  platform service can publish any platform event, and the key is shared by every service that
  holds it, so it is a platform-wide secret, not a per-service one.
- **Fail closed, counted, never silent.** An unverifiable request gets a signed `refused`
  reply naming the reason; an unverifiable event is not handled; an unverifiable reply
  raises. Each counts on `niuu.mesh.attestation.refusals` by reason, direction and claimed
  sender. A mesh whose peer cannot get a certificate does not start;
  `mesh.attestation.required: false` is the operator's explicit choice to go without.
- Consumers that read Sleipnir events directly (the room's collaboration bridge) get them
  through `VerifiedSleipnirSubscriber`, which sets the sender and the projection from the
  proof, not from the envelope. Do not add another direct consumer of mesh events
  without it.

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

### Exception: VM guest metadata identities

A VM session has no kubelet and no human behind it. Its guest agent pulls the
session's credentials (projected-style ServiceAccount tokens and the Forge
session credential) from exactly one Forge route,
`POST /api/v1/internal/compute/metadata/credentials`
(`volundr.adapters.inbound.rest_compute_metadata`,
`volundr.domain.services.guest_metadata.GuestMetadataService`). The request
carries no Keycloak JWT, PAT or scoped workload token; the guest proves which
allocation it is through one configured `GuestIdentity`. This is sanctioned
because the caller is a *machine* whose only possible credential is one issued
to it at provisioning, and the alternative (a long-lived bearer embedded in the
guest) is strictly worse. Three identities exist; the compute pool configures one
and Forge never tries another.

- **Host-key signing** (`volundr.adapters.outbound.guest_identity_host_key`).
  The guest signs with the Ed25519 SSH host key Forge generated for the
  allocation; Forge verifies against the public key it stores in the
  allocation's machine bootstrap (`/etc/niuu/ssh_host_ed25519_key.pub`), so no
  new secret is stored. The signed bytes are the `niuu join` scheme
  (`niuu.adapters.node_signature.signing_message`):
  `f"{METHOD}\n{PATH}\n{timestamp_ms}\n{sha256(body).hexdigest()}"`, carried in
  `Niuu-Guest-Allocation`, `Niuu-Guest-Timestamp` and `Niuu-Guest-Signature`.
  - **Replay protection is the timestamp, advanced atomically.** It must
    strictly increase per allocation through one conditional upsert
    (`PostgresGuestRequestWatermarks.try_advance`, table
    `compute_guest_request_watermarks`); the write happens only after the
    signature and the skew check pass, so a forged request cannot burn a
    legitimate timestamp. Milliseconds, as for `niuu join`.
  - **A bounded clock-skew window** (`clock_skew_seconds` on the adapter,
    positive and finite) — an unbounded window lets a captured request replay.
  - **Every rejection is the identical 401 `{"error": "unauthorized"}`**:
    missing or malformed header, unknown allocation, missing or non-Ed25519
    stored key, bad signature, stale timestamp, replay, and a body naming a
    different allocation than the one authenticated. Nothing distinguishes an
    allocation that exists from one that does not.
  - The signature covers method, path, timestamp and body hash; omitting any
    would let a captured signature be replayed against another route or body.
  - **A registered host** (`RegisteredHostMachineProvider`, configured with
    `key_source: registered` on the identity) is the one case where Forge did
    not generate the key: it is the host's own sshd Ed25519 key, and Forge
    stores only its public half, on the `compute_hosts` row, received with a
    single-use `compute_host_join` pairing code (below). Forge never holds
    that private key. The key belongs to the host, not to one allocation, so
    the limits still bind to the allocation: the body names the allocation
    (a mismatch is the identical 401, so a captured request of an earlier
    allocation of the same host cannot be replayed under a later one), the
    watermark is per allocation, and only the allocation currently holding the
    host gets an answer. Removing the host revokes it.
- **Provider attestation** (`volundr.adapters.outbound.guest_identity_provider`,
  the Harvester default). The provider puts a token into the guest and vouches
  for it: on Harvester, the VM's virt-launcher pod ServiceAccount token shared
  over virtiofs, verified by a TokenReview and by the pod being an active pod
  (`status.activePods`, written only by virt-controller) of the allocation's
  VMI. The ServiceAccount holds no RBAC, because the token is addressed to
  Harvester's API server. A failed check is the identical 401; a TokenReview
  Forge cannot make is a provider error, never a 401.
- **SPIFFE JWT-SVIDs** (`volundr.adapters.outbound.guest_identity_spiffe`) are
  the standard path and need no custom scheme. The guest agent fetches a
  JWT-SVID from its SPIRE agent and sends `Authorization: Bearer <JWT-SVID>`;
  Forge verifies it as any OIDC token (issuer, signature against the SPIRE OIDC
  discovery keys, the configured metadata audience, expiry). The subject must
  match `spiffe_id_template` (default
  `spiffe://{trust_domain}/forge-vm/{pool_id}/{allocation_id}`), which binds the
  token to one allocation of one pool; SPIRE entries selecting that allocation's
  container decide who may hold it.

Limits of this exception, enforced together:

- **The endpoint only ever answers for the unit currently bound to one slot of
  the allocation: the bound session and, when it is a workflow run's unit, the
  members of that run.** Authentication yields an allocation, never a session or
  a slot. The request body names the slot (`{"allocation_id": ..., "slot": n}`);
  it may be omitted only on a single-slot allocation, and a missing, non-integer
  or out-of-range `slot` is a 400 after authentication. The machine must be in
  the configured pool and `ready` or `idle`, and the slot must hold a `starting`
  or `busy` binding with no stop requested; the credentials come from that
  session's own launch payload. Without more, the answer is the bound session's.
  A guest agent of a run's unit adds `"session_id"`: a **member session** of the
  unit, and the answer is that member's Forge session credential alone (the
  unit's ServiceAccount tokens stay the bound session's). A member answers only
  when the *unit's own launch payload* lists it (`members`, written by Forge
  when it started the unit, so the members are fixed at that point): a session
  that is not listed, belongs to another run or is bound to another slot gets
  the same 409 as an unbound slot, and a malformed `session_id` is a 400. The
  members of one run share its slot's one Unix user (workflow-mesh-handoff
  decision 2): they can read each other's files and credentials, which is
  accepted for now within one run of one owner, and a Unix user per member is
  future work. Between slots nothing changes: a member is only ever reachable
  through the slot its unit is bound to. Anything else is a 409 with the reason
  (after authentication) or the identical 401. A released or draining machine, or
  a slot with no bound session, gets nothing, and the Forge session credential
  carries the session's (member's) *current* launch id, so a restart revokes what
  the previous launch held. An allocation's identity vouches for every slot of
  the machine, so a process that holds it can ask for any slot: more than one
  slot per machine needs a runtime that keeps that identity out of the slots'
  reach (own user, network namespace and credential files per slot; the identity
  mounted into the slot's guest agent only). A runtime declares that with
  `VmRuntime.supports_slots`; `SshContainerVmRuntime` does only with
  `slot_isolation` and a credential delivery that serves slots. Forge refuses
  more than one slot per machine unless every configured runtime declares it.
- **Exactly one route.** Do not extend either identity to another route without
  adding it to the same enforcement points this one touches: the router, the
  Volundr chart's Envoy `jwt_authn` bypass and Cedar `ext_authz` skip
  (`charts/volundr/templates/envoy-configmap.yaml`, an exact `safe_regex` on the
  path and `POST` — never a `prefix`, which would also exempt sibling routes
  that must stay JWT-gated; enabled by `guestMetadata.enabled`), and, if Forge's
  `PATRevocationMiddleware` is ever run with `authenticate_http=True`, its
  exempt-path list.
- **Forge's authority to mint is narrow.** The ServiceAccount tokens come from
  the Kubernetes TokenRequest API under a namespaced Role allowing only `create`
  on `serviceaccounts/token` (`guestMetadata` in the Volundr chart).

### Containers in host-mode slots are rootless

A host-mode session (`SshHostVmRuntime`) runs on its VM or registered host as
its slot's user, in the slot's network namespace and cgroup
(`vm_slot_supervisor`). Sessions on one machine are kept apart by those
boundaries alone, so nothing a slot is given may be root on the machine:

- **No container daemon at all.** The runtime needs none: the slot is a named
  network namespace and a systemd slice, the session runs in a delegated scope
  of that slice, and the pod's credential agents are systemd units on overlay
  roots of their unpacked images (`linux_guest_programs`). A guest's Docker, if
  the machine has one, is never reached: no slot user joins the `docker` group
  and its socket is never mounted into a slot, since either is root and reads
  every other slot.
- **No sudo** for a slot user, and no other root-equivalent capability (a
  privileged container, `CAP_SYS_ADMIN`, a host mount).
- **Containers are rootless Podman**, inside the slot: subordinate ids per
  slot that overlap no other slot and no VM account, a cgroup delegated from
  the slot's own (so every container sits under the slot's limits), the
  slot's network namespace, and the session's PID namespace.
- **The VM's own credentials are hidden from the session's view** (an empty
  tmpfs over `/etc/niuu`), whatever their modes, because the session runs on
  the VM's filesystem.

There is no grant that lifts this, and no "machine to itself" path for a host
session. A session that needs root runs on a container runtime
(`SshContainerVmRuntime`), where root is its container's.
