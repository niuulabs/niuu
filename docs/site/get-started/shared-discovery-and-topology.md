# Find service instances and inspect topology

Use Guild when more than one instance of a service is available. Use Observatory
to understand relationships and activity across those instances.

## Inspect the registered instances

With a local platform running:

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/niuu/instances
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/niuu/targets/volundr
```

Open **Guild** in the UI and match the service instance, endpoint, and location to
the workload you intend to use. Guild groups instances of the same service; it is
not the agent's tool or capability registry.

An instance record is not proof that its endpoint can be reached by every caller.
Check reachability from the host that will route the request, and check the
identity expected by the destination.

## How Guild probes health

Guild probes each registered instance on registration and on a periodic interval
(`niuu.health.interval_seconds`), and records `health` (`unknown` / `ok` /
`unreachable`), `lastSeenAt`, `lastCheckedAt`, and `lastError` on the instance —
visible in Guild's instance detail view and over the instances API.

The probe never guesses `{base_url}/health`. Two real deployment shapes break on a
bare `/health`:

- A standalone service whose ingress only routes its own `/api/v1/<service>`
  prefix — a bare `/health` never reaches the pod, so a healthy instance reports
  `unreachable` forever.
- A host shared with the web-next SPA, whose nginx also answers a bare `/health`
  itself (`containers/niuu-web/nginx.conf`) — the probe gets a 200 from the
  frontend's static health check and reports `ok` regardless of whether the actual
  backend is up.

Instead, the probe resolves a path per instance **kind**, appended to
`instance.base_url` — the path under that service's own API prefix, so it reaches
the real backend through either ingress shape:

| Kind | Default health path |
| --- | --- |
| `volundr` | `/api/v1/forge/health` |
| `ting` | `/api/v1/ting/health` |
| `mimir` | `/api/v1/mimir/health` |
| `bifrost` | `/api/v1/bifrost/health` |
| `ravn` | `/api/v1/ravn/health` |
| `observatory` | `/api/v1/observatory/health` |
| `generic`, or any kind not listed above | `/health` |

(`niuu.adapters.outbound.http_instance_probe.DEFAULT_HEALTH_PATHS` is the source of
truth — this table mirrors it.)

### Overriding a path

Two levels, evaluated in order:

1. **Per-kind, cluster-wide** — `niuu.health.probe.health_paths` in Guild's config
   (rendered from `registry.health.probe.healthPaths` in the `guild` chart's
   values) is a map of kind → path, merged on top of the built-in defaults. Set
   only the kinds you're changing:

   ```yaml
   registry:
     health:
       probe:
         healthPaths:
           mimir: "/mimir/health"
   ```

2. **Per instance** — `config.health_path` on one registered instance wins over
   both the per-kind override and the built-in default. Use this when one
   instance's `base_url` doesn't follow its kind's usual convention — for example
   an instance registered with the API version prefix already baked into
   `base_url` (`https://host/api/v1`), where the kind's normal
   `/api/v1/<service>/health` default would double up the prefix. Set it in the
   instance's `config` when registering or editing it in Guild.

A path that isn't publicly reachable through the target's Envoy sidecar reports
`unreachable` even though the service is healthy — Envoy's `jwt_authn` filter
requires a token on every route except `envoy.jwt.bypassPrefixes`. Each service's
own health path is already listed there in its chart's `values.yaml`; adding a new
per-kind or per-instance override that points somewhere else needs the same
Envoy bypass, or the probe will see 401/403 instead of the service's own health
check.

## What crosses the wire to a remote instance

Every Guild aggregate call (Forge/Ravn REST, the session event stream, the
resident chat and Forge replay WebSockets, the health probe, and the
Observatory/agent-directory lookups) forwards only the caller's `Authorization`
bearer token to a remote instance. A client-supplied `x-auth-*` header or
`devUserId`-style query parameter never reaches a remote node — those assert
identity only within the process that resolved them, and the remote instance
re-verifies the bearer itself: in-process JWKS under `host_auth.mode: oidc`, or
allow-all under `host_auth.mode: none`. Under `none` on both sides no identity
crosses the wire at all — the remote simply admits every caller, so register
`none`-mode instances only on a network you already trust.

An instance that shares this process (`config.transport: "embedded"`) is not a
network hop at all, so this restriction does not apply to it: it keeps
receiving the caller's fully resolved identity, the same as any other in-process
call.

A registered instance that is not embedded and resolves to a hostname other
than `localhost`/`127.0.0.1`/`::1` (an in-cluster
`http://*.svc.cluster.local` address included — cluster-internal DNS is not a
loopback address and is not exempt) always goes through the checks below,
whether you reach it from a browser via `host_auth.mode: none` or `oidc`. A
caller on `localhost` against a non-embedded instance under `none` resolves as
an unauthenticated dev user, not as itself — do not rely on that identity for
anything that matters; register the instance as `embedded` or put real
authentication in front of it instead.

### Transport security

A remote instance's `base_url` — and its `config.ravn_base_url`, when a
split-service target configures one — must use `https://` unless the instance
explicitly opts out. This is enforced twice: once at registration/update time
(a 422 with the remedy in the message, so a mistake is caught immediately),
and again on every single outbound call this process makes to that instance
(REST, the health probe, both WebSocket proxy legs, and Ting's own Volundr
calls), which is the real boundary — a row that predates this validation, or
an update that never touched its transport fields, is still refused at call
time, never silently sent a bearer token over plaintext. Embedded instances
and `localhost`/`127.0.0.1`/`::1` targets are exempt — there is no network
path to harden.

**In-cluster Kubernetes service DNS is exempt the same way, by default.**
`guildTransport.trustedPlaintextHostSuffixes`
(`Settings.guild_transport_trusted_plaintext_host_suffixes`) defaults to
`[".svc.cluster.local", ".svc"]`: a host ending in one of those suffixes at a
full DNS-label boundary (`niuu-volundr.volundr.svc.cluster.local` matches;
`notreallyasvc.cluster.local` and `evil-svc.cluster.local.example.com` do
not) skips the https-unless-allow_plaintext requirement without
`config.allow_plaintext` set on every in-cluster seed — a live cluster's
Guild routinely seeds `http://niuu-volundr.volundr.svc.cluster.local` and a
`ravn_base_url` on the same in-cluster suffix, and neither needs an opt-in
for this. Set the list to `[]` to require the explicit `allow_plaintext`
opt-in everywhere, including in-cluster addresses — this is checked at the
same two enforcement points (registration/seed time and every outbound
call), and a suffix the operator adds is honoured identically at both. It
never exempts `config.tls_fingerprint` pinning, which always requires
`https://` regardless of hostname.

**If you already have a remote instance registered over plain `http://`**,
its outbound calls start failing (502, with the remedy in the message) as
soon as this hardening is deployed — set `config.allow_plaintext: true` on it
(or move it to `https://`) before or immediately after upgrading. No database
migration is needed for that change: `allow_plaintext` and `tls_fingerprint`
both live in the instance's existing `config` JSON column. An update that
doesn't touch `base_url`/`ravn_base_url`/`config` at all, or that disables the
instance, is never blocked by the write-time check either way, so you can
always disable a broken instance while you fix its transport settings.
Re-enabling a previously-disabled, insecure instance re-runs the same
write-time check even when that PATCH itself doesn't touch `base_url`/
`config` — a two-step "make it plaintext and disable it" followed later by a
bare "enable it" cannot slip back in unvalidated.

A **config-seeded instance** (`niuu.instances` in the Guild config file,
rendered from the Helm chart) is validated the same way, but the consequence
is more severe: seeding runs at process startup, before Guild serves any
traffic, so an `http://` seed entry that is neither loopback nor covered by
`guildTransport.trustedPlaintextHostSuffixes`, and has no
`config.allow_plaintext: true`, stops Guild from starting at all, not just
its outbound calls from failing once it's up. This is deliberate — a
misconfigured seed is a deploy-time error to fix in the chart values, not a
runtime condition to degrade past. An in-cluster seed like
`http://niuu-volundr.volundr.svc.cluster.local` starts fine under the
default suffix list without any `allow_plaintext`; it only needs the opt-in
(or a matching entry added to `trustedPlaintextHostSuffixes`) if that list
has been narrowed or emptied.

**If you pin `config.tls_fingerprint`**, the fingerprint is checked against
whatever certificate the live handshake presents, every time, with no grace
period: a certificate that expires (or is rotated to a new fingerprint) is
refused the moment it stops matching, even though it might otherwise still
chain to a trusted CA. Rotating a pinned instance's certificate means
updating `config.tls_fingerprint` to the new value as part of that rotation
— there is no separate warning window before the old pin starts failing.
A single `tls_fingerprint` also covers only the certificate presented at
`base_url`; a split-service instance whose `config.ravn_base_url` points at
a *different* host (and therefore a different certificate) is not covered by
that same fingerprint — the Ravn-side connection is checked against the
identical pin, so a split-service instance with two different leaf
certificates cannot be pinned with a single fingerprint today.

**Pinned instances bypass `HTTP_PROXY`/`HTTPS_PROXY` entirely**, including
the certificate pre-fetch used to check the pin: pinning requires seeing the
certificate the instance itself presents, which a raw socket connect that
ignores the proxy environment guarantees and a proxied connection cannot. The
practical effect is that an instance reachable only through a corporate or
cluster egress proxy — with no direct network route to it — cannot be
pinned, because neither the pre-fetch nor the pinned request itself can
traverse that proxy. Put such an instance on a route the pre-fetch can reach
directly (a tailnet, a direct route, a proxy exception) before pinning it, or
rely on ordinary CA-verified `https://` instead.

- **In-cluster Kubernetes service DNS needs nothing at all, by default.** A
  `base_url`/`ravn_base_url` ending in `.svc.cluster.local` or `.svc`
  already matches `guildTransport.trustedPlaintextHostSuffixes`'s default —
  no `config.allow_plaintext` and no chart change required. Only tighten
  this (narrow or empty the list) if the operator wants every in-cluster
  instance to opt in explicitly too.
- **Tailscale (or another already-encrypted overlay) first.** Put the instance
  on a tailnet and register its tailnet address; WireGuard encrypts the link
  end to end, so plain `http://` inside the tailnet is a reasonable, explicit
  choice — set `config.allow_plaintext: true` on that instance to say so.
- **A pinned, self-signed (or private-CA-issued) certificate second.** Set
  `config.tls_fingerprint` to the sha256 fingerprint (64 hex characters,
  colons optional) of the instance's leaf certificate. Every outbound call to
  that instance — REST, the health probe, and every WebSocket proxy leg —
  then trusts only a certificate matching that exact fingerprint and refuses
  to connect on any mismatch, including a certificate that would otherwise be
  perfectly valid. There is no fallback to the platform's normal CA trust once
  a fingerprint is configured for an instance.
- **Plaintext only as a last-resort, explicit per-instance opt-in.** Set
  `config.allow_plaintext: true` only when you have already established the
  network path is trusted by some other means. It is never the default and
  never inferred.

The Guild register/edit dialog exposes both `allow_plaintext` and
`tls_fingerprint` as instance fields.

### Knowledge deployments to a remote Mímir

Guild's knowledge-deployments router (`/knowledge/...`) forwards only the
caller's bearer token to the target Mímir instance, the same as every other
Guild aggregate call — the target re-verifies that bearer itself. A Mímir
reachable only over a Tailscale LAN address therefore needs to be able to
verify it: put it behind Envoy with the platform's IdP, or run it with
`host_auth.mode: oidc` directly. A bare `allow_anonymous_dev`/`none` Mímir on
a tailnet accepts the request but cannot attribute it to the calling user.

## Follow one session

Launch a session on a known target, then inspect **Observatory**. Follow the
session to its owning Forge instance and the relationships the deployment reports.
Compare those with the instance and target records. Missing relationships need
registration or telemetry investigation; they should not be inferred from names.

Cluster and namespace metadata should come from deployment configuration. The
umbrella chart exposes `global.niuu.cluster` for the cluster label. Use names that
reflect your deployment, not the infrastructure names from another installation.

## Diagnose the distinction

| Symptom | Likely layer to inspect |
| --- | --- |
| No instance is listed | Registration and service enablement |
| Instance exists but cannot be called | Endpoint, network, TLS, and authentication |
| Service works but target is unavailable | Advertised runtime profiles and target eligibility |
| Work succeeds but graph is incomplete | Relationship publication and observability ingestion |

[Observability](../operations/observability.md) continues from the graph to logs
and traces. [Architecture](../concepts/platform-model.md) distinguishes service
discovery from mesh membership and A2A discovery.
