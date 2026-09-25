# Joining machines to a Guild

Niuu treats every machine in a house — Kubernetes clusters, DGX Sparks, laptops in
mini/docker mode — as one environment, joined through **Guild** (the shared instance
registry and aggregate router in `src/niuu`). `niuu join` is the one-command way to add a
new machine: an operator mints a short pairing code on Guild, and the new machine trades
it for a registration.

This page covers the first slice of that flow. Explicitly **not** built yet:

- a web UI for pairing (it is CLI-only today — `niuu guild pair` / `niuu join` / `niuu leave`)
- TLS for mini/docker mode (a plaintext instance URL is possible, but only with the
  minting admin's explicit consent — see "Transport and auth consent" below)
- host tags
- a bundled Keycloak for a brand-new house with no IdP yet
- automatic wiring of `niuu guild heartbeat` into `niuu platform up`'s process
  supervision — today an operator runs it themselves under whatever supervisor
  (systemd, Docker restart policy, cron with `--once`) their deployment already uses

## The flow

1. **An operator with `volundr:admin` runs `niuu guild pair <guild-url>`** on a machine
   already authenticated against Guild (`niuu login`). This calls
   `POST /api/v1/niuu/guild/pairing-codes` and prints a single-use, short-TTL pairing code.
   `--allow-plaintext` and `--allow-untrusted-node-auth` grant the two consents below;
   both default to refused.

2. **On the new machine, an operator runs `niuu join <guild-url> --code <code>`.** The CLI:
   - generates an Ed25519 keypair on first use (`~/.niuu/node_key`, created atomically at
     mode `0600`) or loads the existing one;
   - computes the instance(s) this host offers from its running config — mini/docker today
     offer one `volundr` instance at `server.external_host` (required; there is no fallback
     to `server.host`, which is only a bind address, not one another machine can reach) on
     `server.port`;
   - calls `POST /api/v1/niuu/guild/join`, authenticated with the pairing code itself as
     the bearer token, reporting this host's own `host_auth.mode`;
   - persists the returned node id and Guild URL into `~/.niuu/config.yaml` under `guild:`,
     and **applies** the returned identity trust config to `host_auth:` in the same file —
     this host now verifies the same OIDC issuer(s) Guild does (or none, under
     `host_auth.mode: none`), not just a recorded-but-unused value.

3. **Guild validates, then atomically registers.** Transport policy and the offered
   config's key allowlist (see below) are checked before anything is written; consuming
   the pairing code, creating the node, and registering its instances then happen in one
   database transaction (`GuildJoinRepository.consume_and_register`) — a name/key conflict
   or any other failure after that point rolls the whole thing back, so the code is never
   burned for nothing and no half-registered node is left behind. The response includes
   the node id and the identity config (`mode` + trusted OIDC issuers) the node should
   adopt.

4. **The node stays present via `niuu guild heartbeat`**, run under a supervisor
   (`--once` for a single heartbeat, e.g. from cron); the interval comes from
   `guild.heartbeat_interval_seconds`. A single failed heartbeat is logged and retried
   next tick rather than crashing the process. `niuu leave` deregisters cleanly — its
   instances cascade at the database layer.

5. **An admin can revoke a node at any time** — `GET /api/v1/niuu/guild/nodes` lists
   nodes in the caller's tenant, `DELETE /api/v1/niuu/guild/nodes/{id}` removes one and
   every instance it registered, cutting off a stolen node key immediately.

## Pairing codes are scoped workload JWTs, not a bespoke secret

A pairing code is minted through the existing scoped-workload-token mechanism (see
`.claude/rules/architecture.md`): `token_use: valkyrie_build`, `scopes: ["node_join"]`,
minted with an **empty roles claim** — the code proves "this is a valid code", never "act
as the admin who minted it". `node_join` is a member of `KNOWN_WORKLOAD_SCOPES`
(`src/niuu/domain/services/token_scope.py`) and the join route carries a
`require_scope("node_join")` dependency, exactly like every other scoped entry point
(Forge session create, Ting workflow launch, Observatory topology push). Minting when
`workload_identity.enabled: false` returns `503`, not a bare `500` — the remedy is in the
response.

A JWT alone is reusable until it expires, so single-use is layered on top: Guild records
the code's *hash*, its expiry, and whether it has been consumed in `niuu_pairing_codes`.
The code's TTL is its own knob, `niuu.node_join.pairing_code_ttl_seconds` (default 300s) —
deliberately shorter and separate from `workload_identity.token_ttl_seconds`, which every
other scoped credential shares; the stored code is never valid for longer than the lesser
of the two.

## Transport and auth consent — never a node self-grant

Two risks a joining node must never be able to grant itself, both recorded on the pairing
code at mint time by the admin instead:

- **Plaintext transport.** An offered instance's `config` is allowlisted
  (`ALLOWED_OFFERED_CONFIG_KEYS` in `niuu.domain.services.guild_join`) — a node cannot set
  `allow_plaintext` or `transport: embedded` (which would fake being the trusted embedded
  Local Forge target) in its own payload. A plaintext `http://` instance URL is only
  accepted when the pairing code was minted with `--allow-plaintext`, in which case Guild
  itself injects `config.allow_plaintext: true` into the instance it writes.
- **Untrusted node identity.** A node reports its own `host_auth.mode` at join time. If
  Guild runs `host_auth.mode: oidc` and the node reports `none`, the join is refused —
  Guild would otherwise forward real user bearer tokens to an instance that trusts every
  caller — unless the pairing code was minted with `--allow-untrusted-node-auth`.

## Node identity and signed requests

Each machine holds its own Ed25519 keypair, generated atomically (`O_CREAT | O_EXCL`,
mode `0600`) on first `niuu join` and stored under `~/.niuu/node_key`. Guild only ever
sees the public key (submitted at join time and stored on the `RegisteredNode` row); the
private key never leaves the host. `niuu leave` **refuses** to generate a new key if the
file is missing — signing with a fresh key would never match what Guild has on file — and
directs the operator to an admin revoke instead.

Node-originated calls — heartbeat and leave — carry **no bearer JWT at all**. They are
authenticated by an Ed25519 signature over the request instead:

```
message = f"{method}\n{path}\n{timestamp_ms}\n{sha256(body).hexdigest()}"
signature = base64(Ed25519_sign(private_key, message))
```

sent as three headers: `x-niuu-node-id`, `x-niuu-timestamp` (Unix **milliseconds** — a
heartbeat and a leave in the same second must both be able to advance the replay
watermark), and `x-niuu-signature`. Guild verifies this through the `RegisteredNodeVerifier`
port (`niuu.ports.node_verifier`), implemented by the `Ed25519NodeVerifier` adapter
(`niuu.adapters.node_signature`): a bounded clock-skew window
(`niuu.node_join.clock_skew_seconds`, default 30s) plus a strictly-increasing per-node
timestamp watermark (`niuu_nodes.last_request_at`), advanced only by a single atomic
conditional `UPDATE ... WHERE last_request_at IS NULL OR last_request_at < $timestamp` — a
separate check-then-write would race under concurrent requests. Every rejection reason
(unknown node, malformed node id, missing headers, bad signature, stale timestamp, failed
replay check) returns the identical 401, so probing node ids never becomes an existence
oracle. See `.claude/rules/architecture.md` for the full sanctioned-exception writeup and
its limits.

Because these two endpoints use a different authentication mechanism entirely, they are
the only paths exempted both from the platform's bearer-JWT identity check
(`niuu.adapters.pat_revocation_middleware`) and from the Guild chart's Envoy `jwt_authn`
filter (`charts/guild/templates/envoy-configmap.yaml`) — an exact `safe_regex` match on
just these two routes, never a `prefix`, which would also exempt the admin `GET`/`DELETE
/api/v1/niuu/guild/nodes` routes that must stay JWT-gated.

## Endpoints

| Method | Path | Auth |
|---|---|---|
| `POST` | `/api/v1/niuu/guild/pairing-codes` | human bearer JWT, `volundr:admin` |
| `GET` | `/api/v1/niuu/guild/nodes` | human bearer JWT, `volundr:admin` |
| `DELETE` | `/api/v1/niuu/guild/nodes/{node_id}` | human bearer JWT, `volundr:admin` |
| `POST` | `/api/v1/niuu/guild/join` | pairing code as bearer JWT, `node_join` scope |
| `POST` | `/api/v1/niuu/guild/nodes/{node_id}/heartbeat` | Ed25519 signature |
| `POST` | `/api/v1/niuu/guild/nodes/{node_id}/leave` | Ed25519 signature |

## CLI commands

```bash
niuu guild pair <guild-url> [--allow-plaintext] [--allow-untrusted-node-auth]
niuu join <guild-url> --code <code> [--name NAME]
niuu guild heartbeat [--once]
niuu leave
```

## Schema

`migrations/000083_guild_node_join.up.sql` adds two Guild-owned tables and one column:

- `niuu_pairing_codes` — `code_hash`, `created_by`, `tenant_id`, `allow_plaintext`,
  `allow_untrusted_node_auth`, `expires_at`, `consumed_at`, `consumed_by_node_id`.
- `niuu_nodes` — `name` (unique per tenant), `public_key` (globally unique), `tenant_id`,
  `created_by`, `allow_plaintext` (copied once from the admitting pairing code),
  `last_seen_at`, `last_request_at` (milliseconds).
- `niuu_instances.node_id` — a real foreign key to `niuu_nodes(id)` (`ON DELETE CASCADE`),
  never exposed on `InstanceCreateRequest`/`InstanceUpdateRequest` and excluded from the
  `ON CONFLICT ... DO UPDATE SET` clause in `PostgresInstanceRepository.save_instance`, so
  it is immutable once written and cannot be set or cleared through the ordinary
  authenticated instance API. This is also what closes the instance-hijack risk: a joining
  or heartbeating node's instances are looked up and updated by this column
  (`InstanceRepository.list_for_node`), never by a slug derived from the node's
  operator-chosen *name* — a node cannot "adopt" an existing instance by naming itself to
  collide with it, because node_id is a Guild-assigned UUID the node never chooses.
