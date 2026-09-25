# Joining machines to a Guild

Niuu treats every machine in a house — Kubernetes clusters, DGX Sparks, laptops in
mini/docker mode — as one environment, joined through **Guild** (the shared instance
registry and aggregate router in `src/niuu`). `niuu join` is the one-command way to add a
new machine: an operator mints a short pairing code on Guild, and the new machine trades
it for a registration.

This page covers the first slice of that flow. Explicitly **not** built yet:

- a web UI for pairing (it is CLI-only today — `niuu guild pair` / `niuu join` / `niuu leave`)
- TLS for mini/docker mode (joined instances are registered over plain HTTP on the LAN,
  same as every other instance registered today)
- host tags
- a bundled Keycloak for a brand-new house with no IdP yet

## The flow

1. **An operator with `volundr:admin` runs `niuu guild pair <guild-url>`** on a machine
   already authenticated against Guild (`niuu login`). This calls
   `POST /api/v1/niuu/guild/pairing-codes` and prints a single-use, short-TTL pairing code.

2. **On the new machine, an operator runs `niuu join <guild-url> --code <code>`.** The CLI:
   - generates an Ed25519 keypair on first use (`~/.niuu/node_key`, mode `0600`) or loads
     the existing one;
   - computes the instance(s) this host offers from its running config/mode (mini/docker
     today offer one `volundr` instance at `server.external_host or server.host` on
     `server.port`);
   - calls `POST /api/v1/niuu/guild/join`, authenticated with the pairing code itself as
     the bearer token;
   - persists the returned node id and the Guild URL into `~/.niuu/config.yaml` under
     `guild:`.

3. **Guild validates and registers.** The join route requires the `node_join` scope (the
   pairing code is a scoped workload JWT — see below), atomically consumes the pairing
   code exactly once, applies the same LAN transport-security rules that already gate
   ordinary instance registration (`allow_plaintext` / `transport_security`, see
   `niuu.domain.transport_security`), creates a `RegisteredNode` row, and registers the
   offered instances (tenant-scoped to the admin who minted the code). The response
   includes the node id and the identity config (`auth_mode` + trusted OIDC issuers) the
   node should adopt — joining never invents a second human-auth path; under
   `host_auth.mode: oidc` the node trusts the exact same issuer(s) Guild does, and under
   `host_auth.mode: none` that is reported as an explicit, honest empty trust list.

4. **The node stays present with signed heartbeats**, and can leave cleanly.

## Pairing codes are scoped workload JWTs, not a bespoke secret

A pairing code is minted through the existing scoped-workload-token mechanism (see
`.claude/rules/architecture.md`): `token_use: valkyrie_build`, `scopes: ["node_join"]`.
`node_join` is a member of `KNOWN_WORKLOAD_SCOPES`
(`src/niuu/domain/services/token_scope.py`) and the join route carries a
`require_scope("node_join")` dependency, exactly like every other scoped entry point
(Forge session create, Ting workflow launch, Observatory topology push).

A JWT alone is reusable until it expires, so single-use is layered on top: Guild records
the code's *hash*, its expiry, and whether it has been consumed in `niuu_pairing_codes`.
`POST /guild/join` consumes that row with one atomic
`UPDATE ... WHERE consumed_at IS NULL AND expires_at > NOW()`, so two joins racing on the
same code can never both succeed — the first commit wins, the second gets
"invalid, expired, or already used". The code's TTL is not a separate config knob; it
inherits `workload_identity.token_ttl_seconds`, the same TTL every other scoped workload
credential uses.

## Node identity and signed requests

Each machine holds its own Ed25519 keypair, generated on first `niuu join` and stored
under `~/.niuu/node_key` with `0600` permissions. Guild only ever sees the public key
(submitted at join time and stored on the `RegisteredNode` row); the private key never
leaves the host.

Node-originated calls — heartbeat and leave — carry **no bearer JWT at all**. They are
authenticated by an Ed25519 signature over the request instead:

```
message = f"{method}\n{path}\n{timestamp}\n{sha256(body).hexdigest()}"
signature = base64(Ed25519_sign(private_key, message))
```

sent as three headers: `x-niuu-node-id`, `x-niuu-timestamp` (Unix seconds), and
`x-niuu-signature`. Guild verifies this through the `RegisteredNodeVerifier` port
(`niuu.ports.node_verifier`), implemented by the `Ed25519NodeVerifier` adapter
(`niuu.adapters.node_signature`) — a bounded clock-skew window
(`niuu.node_join.clock_skew_seconds`, default 30s) and a strictly-increasing
per-node timestamp watermark (`niuu_nodes.last_request_at`) are the only replay
protection; both fail closed with a precise 401. Because these two endpoints use a
different authentication mechanism entirely, they are the only paths exempted from the
platform's bearer-JWT identity check (`niuu.adapters.pat_revocation_middleware`), the same
way the workload JWKS endpoint already is.

## Endpoints

| Method | Path | Auth |
|---|---|---|
| `POST` | `/api/v1/niuu/guild/pairing-codes` | human bearer JWT, `volundr:admin` |
| `POST` | `/api/v1/niuu/guild/join` | pairing code as bearer JWT, `node_join` scope |
| `POST` | `/api/v1/niuu/guild/nodes/{node_id}/heartbeat` | Ed25519 signature |
| `POST` | `/api/v1/niuu/guild/nodes/{node_id}/leave` | Ed25519 signature |

## CLI commands

```bash
niuu guild pair <guild-url>                       # operator: mint a pairing code
niuu join <guild-url> --code <code> [--name NAME]  # new machine: join
niuu leave                                         # this machine: deregister
```

`niuu join` fails loudly (no fallback) when the Guild rejects the code, the offered
instance's transport does not meet the LAN transport-security policy, or the request is
otherwise malformed — an operator re-mints a new pairing code rather than retrying a spent
or expired one. `niuu leave` deregisters every instance this node offered (matched by
`config.node_id` on the registered instance) and then the node itself.

## Schema

`migrations/000083_guild_node_join.up.sql` adds two Guild-owned tables:

- `niuu_pairing_codes` — `code_hash`, `created_by`, `tenant_id`, `expires_at`,
  `consumed_at`, `consumed_by_node_id`.
- `niuu_nodes` — `name`, `public_key`, `tenant_id`, `created_by`, `last_seen_at`,
  `last_request_at`.

Instances a node offers are ordinary `niuu_instances` rows (tenant-visibility, same table
every other instance uses); the link back to the owning node lives in that row's
`config.node_id`, not a new foreign-key column — `niuu leave` matches on it directly.
