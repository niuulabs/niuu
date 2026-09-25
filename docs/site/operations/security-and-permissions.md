# Execution boundaries and permissions

A Niuu session can run code, read files, and call services using the authority
available to its runtime. The execution backend determines the isolation boundary.

## Local processes

Mini mode starts processes as the host OS user. A workspace directory is not an
access-control boundary around that account. Local mounts can expose an existing
checkout, and the runtime may inherit host credentials or environment settings.
Use the local path for work whose code and tools you trust with that account.

## Remote runtimes

A Kubernetes pod or OpenShell sandbox has its own configured network, filesystem,
identity, and credential paths. Verify the actual mounts and runtime policy.
Do not assume that moving a process to a cluster automatically limits the
credentials attached to it.

OpenShell's provider-grant path is distinct from mounting a home directory with
agent login files. Use the supported [OpenShell credential flow](openshell-runtime.md)
for that backend.

## People, workloads, and providers

Operator login controls access to Niuu. Workload identity identifies a running
session or sandbox. Provider authentication allows inference or another external
operation. Test each independently and scope grants to the intended caller and
operation. See [identity](../reference/identity.md) and
[credentials](../reference/credentials-and-secrets.md).

## Authentication mode on hosts without Envoy

Kubernetes deployments sit behind an Envoy sidecar that verifies each request's
JWT and forwards trusted `x-auth-*` headers; the identity and authorization
adapters trust those headers unconditionally because Envoy already checked the
signature. Mini mode and single-host `niuu up` (docker mode) have no Envoy in
front of them. `host_auth.mode` in `~/.niuu/config.yaml` is an explicit,
config-first choice between two modes — there is no third, implicit option:

```yaml
host_auth:
  mode: none # or: oidc
```

It is named `host_auth`, not `auth`: Ting's own `Settings` reads a top-level
`auth:` key from this same file for its own, differently-shaped config
(`auth.adapter` / `auth.kwargs` / `auth.allow_anonymous_dev`) — a shared
`auth:` key would collide between the two.

### `none` — explicit no-auth (the default)

Every caller is treated as the local admin, exactly as mini and docker mode
have always behaved. This keeps existing installs working unchanged. It is an
explicit operator decision, never a silent fallback: the platform logs

```
authentication disabled (auth.mode: none): every caller is treated as admin
```

once at startup for every co-hosted service, and `GET /api/v1/identity/auth/config`
reports `"mode": "none"`. Use `none` only for local development or a network
you already trust — anyone who can reach the host's port can act as admin.

### `oidc` — in-process JWT verification, for the paths that check it

Configure at least one trusted issuer and the covered paths (see the table
below) verify bearer JWTs in-process against the issuer's published JWKS —
the same signature check Envoy's `jwt_authn` filter performs in Kubernetes —
instead of trusting `x-auth-*` headers from the caller:

```yaml
host_auth:
  mode: oidc
  oidc:
    issuers:
      - issuer: "https://keycloak.example.com/realms/volundr"
        audiences: ["volundr-api"]
        # jwks_uri is optional; omitted, it is resolved once via OIDC
        # discovery (<issuer>/.well-known/openid-configuration). Both issuer
        # and jwks_uri must be HTTPS, except localhost for local dev.
    clock_leeway_seconds: 60 # allowed skew for exp/nbf
    jwks_cache_ttl_seconds: 300 # routine JWKS re-fetch interval
    min_refresh_interval_seconds: 5 # throttle for forced refreshes (unknown kid)
```

Any OIDC-compliant provider works (Keycloak, Entra ID, Okta, ...) — the
adapter is IDP-agnostic, matching the platform's [architecture
rule](https://github.com/niuulabs/niuu/blob/dev/.claude/rules/architecture.md)
that authentication never couples to a specific vendor. Claim names default to
the same claims Envoy's `jwt_authn` filter maps in Kubernetes (`sub`, `email`,
`tenant_id`, `resource_access.volundr.roles`), so a token a Kubernetes
deployment would accept carries the same identity here. Only asymmetric keys
(RS256/ES256/...) are ever trusted; a symmetric (`oct`) or encryption-only
(`use: enc`) key published in a JWKS response is never used to verify a
signature.

An unrecognised `kid` forces at most one JWKS refresh per
`min_refresh_interval_seconds` window (not one per request — otherwise many
tokens with distinct or missing kids could force a fetch each). An invalid,
expired, or unverifiable token is always rejected with 401 — there is no
fallback to anonymous access.

Personal access tokens issued by an IDP-backed `TokenIssuer` (e.g.
`KeycloakTokenIssuer`, via OAuth token exchange) validate through the same
path automatically, since they share the configured issuer. PATs issued by
the local development `MemoryTokenIssuer` (HS256, signed with a
process-local secret) cannot be verified against any JWKS endpoint; the
platform refuses to start with `host_auth.mode: oidc` combined with that
issuer, with a remedy in the error.

`host_auth.mode: oidc` also refuses to start while the Guild or Bifröst
plugins are enabled (`plugins.enabled.guild` / `.bifrost`, both on by
default) — see the coverage table below for why (Guild forwards raw
headers; Bifröst's own inbound auth is verified now, but nothing yet
supplies sessions or residents with a credential to send it). Disable
either to run `oidc` today. Mímir no longer needs to be disabled: its own
auth checks now go through the same `auth_mode` contract as every other
co-hosted service, covering both the REST router and the MCP server — see
their rows in the table below.

### What `oidc` actually covers

`oidc` is a claim that a given inbound path verified the caller's bearer JWT
signature. This table is the actual, current answer for which paths that
claim is true for — `none` always means the historical "every caller is
admin," so only the `oidc` column varies:

| Inbound identity path | `oidc` behaviour |
|---|---|
| Völundr / Identity REST API (`extract_principal`, `IDENTITY__ADAPTER`) | Verified — `JwksIdentityAdapter` |
| Cedar authorization (`AUTHORIZATION__ADAPTER`) | Enforced — `CedarAuthorizationAdapter` (bundled policies) |
| Ravn's own inbound API (`RAVN_API_AUTH__ADAPTER`) | Verified — `JwksBearerAuthenticationAdapter` |
| Ting's own inbound API (`AUTH__ADAPTER`) | Verified — `JwksBearerAuthenticationAdapter` |
| Niuu root app / session-proxy attach (`HOST_IDENTITY__ADAPTER`) | Verified — `JwksBearerAuthenticationAdapter`; the proxy strips client `x-auth-*` and forwards only the identity it resolved |
| Session-proxy WS/HTTP attach ownership (`/s/{id}/session`, `/s/{id}/api/{path}`) | Re-checked against the already-verified principal (`JwksIdentityAdapter.revalidate_verified_principal`) — no second bearer token is available at that point, so this re-derives current role-mapping/membership rather than re-verifying a signature |
| PATs from an IDP-backed `TokenIssuer` (Keycloak token exchange) | Verified — same issuer/JWKS as regular tokens |
| PATs from the local `MemoryTokenIssuer` (HS256) | **Refused at startup** — cannot be verified without a shared secret |
| Guild's knowledge-deployments admin check (`_require_admin`) | Verified — goes through `extract_principal` |
| Mímir's own auth (`_require_deploy_auth`, per-request tenant scoping) | Verified — `JwksBearerAuthenticationAdapter` via `MIMIR_AUTH__ADAPTER`/`MIMIR_AUTH__KWARGS` (`mimir.config.MimirServiceConfig.identity_adapter`), guarded at startup by the same `niuu.service_runtime._validate_identity_adapter_class` check every other co-hosted service gets. Caller-supplied `x-auth-*` headers are never read directly any more; only the verified bearer's claims decide identity. Under `auth_mode: none`, tenant scoping is explicitly skipped (tenant `""`, the unrestricted host operator) rather than the allow-all adapter's own fixed tenant — see `mimir.router.MimirRouter._request_scope`. Refuses to start if `auth_mode: none` is combined with a configured `tenant_id` (the allow-all adapter can never assert it, so every request would permanently 403) |
| Mímir writes — REST `_require_write_auth` (page PUT/DELETE, ingest, registry mounts, `/doctor/fix`, `/page/revise`) and the MCP `mimir_write`/`mimir_ingest` tools | Verified **under `auth_mode: oidc` only**: requires an authenticated tenant *and* one of `admin`/`volundr:admin`/`volundr:developer` (`mimir.router.WRITE_ROLES`). Under `envoy` and `none` this stays the previous behaviour exactly — no role gate, only the pre-existing tenant-scope check — because ymir's knowledge warden and valhalla's resident Muninn write to their Mímir instances directly today, bypassing Envoy with no credential at all, and Keycloak-authenticated web-UI users reach `envoy`-mode Mímir with raw `resource_access` roles (`developer`, not `volundr:developer` — Mímir's `EnvoyHeaderAuthenticationAdapter` is never given Völundr's `identity.roleMapping`, unlike Völundr's own identity adapter); gating those under `envoy` today would 403 all of them on every write. Under `oidc`, `MIMIR_AUTH__KWARGS` already carries the same `role_mapping` as Völundr's chart default (`charts/volundr/values.yaml` `identity.roleMapping`: `admin`/`developer`/`viewer` → `volundr:*`), so a raw Keycloak `developer` role already arrives mapped and satisfies `WRITE_ROLES` — no separate raw-role allowance was needed. The MCP gate is `MimirMcpServer._require_write_auth`, same scoping, mirroring the REST check (same `WRITE_ROLES`, same identity adapter). **Follow-up, not done here:** extending real write-role enforcement to `envoy` needs ymir's warden and valhalla's Muninn to go through Envoy with a workload credential (`auth: {type: workload, audiences: [mimir]}`) instead of dialling Mímir directly, and Mímir's own Envoy identity adapter needs the same `role_mapping` wiring Völundr's already has |
| Every non-health Mímir path — REST *and* MCP (`/mcp`, mounted under `/api/v1/mimir/mcp`) | Verified app-wide by `mimir.app.create_app`'s `enforce_identity` middleware: under `auth_mode: oidc`, a missing or invalid bearer is always 401, never treated as an anonymous-but-allowed caller — this closed a real gap where an anonymous caller could read `/mimir/stats`, register a local registry mount naming an arbitrary host path, or call MCP write tools with zero credential. The same middleware also denies (403) a scoped workload token or scoped PAT under `oidc` (`niuu.domain.services.token_scope.credential_allows_route`) — Mímir is not one of those credentials' named entry points. A configured `tenant_id` is still separately enforced (403 on mismatch) on top of this. Mímir does **not** apply a PAT revocation check under `oidc` (unlike Bifröst's `OidcAuthAdapter`) — a revoked PAT that shares the platform's OIDC issuer would still verify by signature alone; wiring `bifrost.config.PATRevocationConfig`'s equivalent into the shared `identity.adapters.jwks` adapters (used platform-wide, not just here) is tracked as follow-up work, not done in this round |
| Guild's forwarding of caller identity to a remote Mímir deployment (`forward_identity_headers`) | **Not covered** — forwards the caller's raw headers; `oidc` refuses to start while the `guild` plugin is enabled |
| Bifröst gateway (`bifrost.auth_mode`) — `/v1/messages`, `/v1/chat/completions`, `/v1/responses`, `/v1/cache/stats`, `/admin/reload-keys`, `/settings`, `/models`, `/models/{id}`, `/aliases`, `/providers`, `/providers/health`, `/v1/models`, `/api/tags`, `/v1/usage` | Verified when the CLI host sets `bifrost.auth_mode: oidc` — it always does this automatically under `host_auth.mode: oidc` (`cli.commands.platform._effective_bifrost_config`), overriding whatever `bifrost.auth_mode` says in config.yaml (and refusing to start if the operator explicitly configured `bifrost.auth_mode` themselves, rather than silently discarding it — `bifrost.pat_revocation` is not discarded, it survives and still applies). Every one of the listed routes now requires the configured credential — `/settings`, `/models`, `/models/{id}`, `/aliases`, `/providers`, `/providers/health`, `/v1/models`, and `/api/tags` had no auth check in any mode before this round; `/health`, `/healthz`, `/readyz`, and `/metrics` remain deliberately open (liveness/readiness probes, and metrics — gating `/metrics` would silently stop Prometheus scraping the moment any cluster leaves `open` mode, since it is scraped on the app port with no credential today; see `bifrost.inbound.observability`'s module docstring). `/admin/reload-keys` additionally requires an admin role (`admin`/`volundr:admin`) in `pat`/`oidc` mode specifically — those are the modes with a per-caller role concept; a verified `mesh` credential (XFCC/SPIFFE service identity, no role concept) remains sufficient there, as it always has been. Both `OidcAuthAdapter` and `PATAuthAdapter` deny (403) a scoped workload token or scoped PAT (`niuu.domain.services.token_scope.token_requires_scope_check`) — Bifröst model access is not a named entry point for either. A tenant-less verified `oidc` token is refused (403) rather than pooled into a shared `default` quota tenant; `pat` mode does *not* reject a tenant-less PAT the same way — `niuu.adapters.memory_token_issuer.MemoryTokenIssuer` never sets a `tenant_id` claim at all today, so rejecting would break ordinary PAT auth, not harden it; those PATs keep pooling into `default`. `mesh` mode no longer falls back to the caller-supplied `X-Agent-Id` header when `X-Forwarded-Client-Cert` is absent — the request is rejected instead. `pat` mode *requires* a revocation decision (`BifrostConfig._pat_mode_requires_revocation_decision`): configure `bifrost.pat_revocation.adapter`, or set `pat_revocation.enabled: false` explicitly — there is no silent default either way; `oidc` applies the same revocation check, optionally, when the verified bearer happens to be a PAT. `GET /v1/usage`'s `tenant_id` query parameter is always ignored outside `open` mode in favour of `identity.tenant_id` — but how trustworthy that tenant is still depends on the mode, per the paragraph above (verified for oidc, a plain unsigned claim or the shared `default` bucket for pat, the caller-supplied `X-Tenant-Id` header for mesh). `host_auth.mode: oidc` currently refuses to start at all while the `bifrost` plugin is enabled (see the next row and `cli.config.CLISettings._OIDC_UNCOVERED_PLUGINS['bifrost']`) |
| Model calls from Claude Code / Codex sessions, and local residents, routed through Bifröst (`skuld.config.ModelGatewayConfig.token` sent as `ANTHROPIC_AUTH_TOKEN`/`NIUU_MODEL_GATEWAY_TOKEN`; `ravn.adapters.llm.bifrost.BifrostAdapter`) | **Not covered — `host_auth.mode: oidc` refuses to start while the `bifrost` plugin is enabled specifically because of this.** `volundr.adapters.outbound.contributors.model_gateway.ModelGatewayContributor` sends a named, documented sentinel (`OPEN_GATEWAY_TOKEN`, not a credential — valid only because `open`/`none` ignores it) for every `auth_mode` except `oidc`, where it sends no token at all rather than one that would be meaningless; `claude_env.py`/`codex_ws.py` now raise instead of silently sending a blank `ANTHROPIC_AUTH_TOKEN`/`NIUU_MODEL_GATEWAY_TOKEN` (an empty override used to read as "not logged in" in a container, or fall back to sending the host's real subscription OAuth token to the gateway instead). `BifrostAdapter` (used by local residents) still sends no `Authorization` header at all. Until a real per-session/per-resident PAT or workload token is minted and threaded through both paths, running oidc with Bifröst enabled would 401 every model call — set `plugins.enabled.bifrost: false` to run oidc today (cloud-model sessions call their vendor's API directly, not through Bifröst, and are unaffected), or `auth.mode: none`. Minting and threading a real credential through is tracked as follow-up work. A second, separate path reaches the same sentinel: a session on a seeded **Model server** connection (`cli.commands.platform.model_server_seed_connections`, docker/mini mode) gets `SKULD__MODEL_GATEWAY__URL` *and* `SKULD__MODEL_GATEWAY__TOKEN` from `IntegrationContributor`'s `env_from_config` on the connection's own `config.token`, not from `ModelGatewayContributor` (which isn't wired in docker/mini mode) — this path only ever runs where the bifrost plugin is enabled, so it too never fires under `oidc` |
| Observatory / Ravn residents forwarding caller headers upstream | **Not yet audited** — treat as unverified until confirmed otherwise |
| Skuld's own broker HTTP API (`skuld.service_manager`), reached directly on the loopback broker port rather than through the niuu proxy | Not authenticated by this scheme at all — the broker binds loopback-only and relies on the host's own process/network boundary (nothing else can reach `127.0.0.1:<broker port>` without already being on the host); the session-proxy's ownership guard is what protects the browser-facing path (`/s/{id}/...`), not the broker port itself |

## OAuth credentials and renewal

### OpenBao-managed integrations

Production integration OAuth renewal uses OpenBao's `oauthapp` secrets engine;
KV storage alone does not refresh tokens. Enrollment imports the provider refresh
token into the engine. Grant identities include tenant, user, and credential/account.
The application keeps connection metadata in the existing user-scoped store; this
is not a migration of all platform data into separate tenant vaults.

Session launch checks credential ownership and tenant membership. Kubernetes
sessions receive read-only access to their selected grant paths and current access
tokens through continuous Agent file projection. OpenShell uses its existing
workload-bound broker to obtain current access tokens. Neither delivery path needs
the provider refresh token or OAuth client secret in the session. OpenBao owns
refresh-token rotation and coordinates concurrent readers of a grant. Renewing a
session's OpenBao authentication lease is separate from refreshing a provider token.

The application control plane still has broader credential access: enrollment
services manage grants and configured OAuth clients; Ting reads current grant
tokens. These service identities are trusted across the users they serve. Never
attach their broad policies to session identities. A hashed grant name is not an
access-control boundary; OpenBao policies and application ownership checks are.

Existing KV credentials are not automatically migrated. Use the explicit
[per-connection migration procedure](integrations-and-mcp.md#migrating-existing-renewable-grants)
or reconnect a renewable integration with the matching OAuth client. Credentials
without a refresh token remain in the existing store and cannot be auto-renewed.
Deleting a stored grant does not itself revoke an already-issued upstream token.
See [OpenBao renewal operations](https://github.com/niuulabs/niuu/blob/dev/docs/operations/openbao-oauth.md) for configuration,
recovery, historical KV-version retention, and live rotation/isolation checks.

### MCP discovery and browser authorization

MCP connections use the same credential store and selected-connection workload
policies as other integrations. Each grant is bound to its user, tenant, and MCP
URL. Changing that URL requires a new connection; a different user or tenant
cannot reconnect an existing one. The runtime gets access tokens through the
existing Agent projection or OpenShell broker, never the refresh token or OAuth
client secret.

Discovery validates protected-resource and issuer metadata and requires PKCE
S256. Registration identities are cached separately per tenant, user, issuer,
and callback URL. Pending authorization state and PKCE verifiers are stored in
the vault so callbacks can reach another replica; callbacks reject expired or
consumed state. Callback code/state query parameters are removed from application
access logs. Configure upstream access logs to omit OAuth callback query strings
as well. Client metadata and callback GETs are public; starting discovery or a
connection requires an authenticated platform user.

Discovered endpoints are restricted to public HTTPS addresses. Both the platform
and patched OpenBao plugin validate DNS at connection time, reject private and
special-use addresses, and do not follow redirects. The plugin also retains the
grant's RFC 8707 `resource` parameter during refresh. Set
`mcp_resource_indicators: true` only when this plugin revision is deployed; the
application refuses MCP OAuth enrollment with an unpatched engine.

These MCP changes do not enable the legacy global refresh scanner.

### Mini-mode's legacy refresh scan

The shared host retains an optional application-level refresh loop for local
mini-mode compatibility. It runs only when **both** `local_mounts.mini_mode` and
`oauth.mini_mode_refresh_enabled` are true. The application setting defaults to
true; the shared-host Helm chart defaults it to false. Production cluster values
explicitly disable it, and the OAuth flag alone cannot enable it outside mini-mode.

The loop scans immediately at startup and every five minutes. It enumerates enabled
integration connections **across all users in the configured database**, reads
eligible OAuth credentials, and refreshes tokens expiring within ten minutes.
It runs independently of active sessions and skips credentials with a declared
`renewal_owner`, including OpenBao-managed grants. It uses the host's credential-store
permissions, not a session's limited permissions. Treat that host as privileged;
this compatibility scan is not a per-session or per-tenant isolation mechanism.

To disable the scan, set this in the shared host's application YAML and restart the
shared host (or restart the local mini-mode stack):

```yaml
oauth:
  mini_mode_refresh_enabled: false
```

The equivalent process-environment override is
`OAUTH__MINI_MODE_REFRESH_ENABLED=false`. It must be present when the shared host
starts. For the umbrella Helm chart, configure:

```yaml
niuu-shared:
  oauth:
    miniModeRefreshEnabled: false
volundr:
  oauth:
    miniModeRefreshEnabled: false
```

For either standalone chart, use `oauth.miniModeRefreshEnabled: false` at its root.
Disabling this loop does not revoke credentials, stop OpenBao's own renewal, or
migrate unmanaged grants. Unmanaged tokens may expire and require reconnection.

### Codex subscription credentials

Codex subscription grants also use OpenBao's `oauthapp` engine. Enrollment imports
the nested refresh token from `auth.json`; the current KV version retains account
metadata and unrelated configuration, but no login document. Historical KV versions
remain subject to the vault's retention policy and must be handled separately.

The authenticated `/api/v1/internal/credentials/codex/tokens` endpoint checks the
caller's user and tenant, then reads the current engine token. It delivers only an
access token, expiry, and Codex account metadata. It contains no provider-refresh
code. Parallel sessions read the same grant; the engine coordinates rotation.
The mini-mode scanning flag does not control this engine-managed path.

Existing nested credentials require an explicit operator migration after every
legacy broker has been replaced. Unmigrated credentials fail closed with a reconnect
request. Configure `codex_oauth_server` in the OAuth credential-store adapter and
provision that server with the public OAuth client used by Codex enrollment.
Run the [migration procedure](https://github.com/niuulabs/niuu/blob/dev/docs/operations/openbao-oauth.md#codex-subscription-migration)
once per credential. It resumes an already-imported grant without reusing the old
refresh token if the KV metadata write was interrupted.

The deployed `oauthapp` API refreshes by expiry and has no forced invalidation API.
If Codex rejects a token that the engine still considers current, the broker can
return a token the engine has already rotated; otherwise it requests reconnection.
It never reports the unchanged rejected token as successfully refreshed. Revoked
provider grants require reconnecting; vault outages return a service-unavailable
response instead of asking the user to replace valid credentials.

### Docker mini-mode Codex compatibility

Docker mini mode explicitly selects `MiniModeCodexCredentialBroker` for its
file-backed credentials. It refreshes the authenticated user's grant on request,
under a database advisory lock; it does not scan users. It refuses to start outside
mini mode and refuses grants managed by OpenBao. Host-native mini mode continues
to use the runtime's local login.

The scanning flag above does not disable this on-demand path. To disable it, set:

```yaml
codex_credential_broker:
  adapter: volundr.adapters.outbound.codex_credential_broker.DisabledCodexCredentialBroker
  kwargs: {}
```

For the generated Docker bundle, override `CODEX_CREDENTIAL_BROKER` in its Compose
environment with the equivalent JSON object; the generated environment setting
takes precedence over application YAML. Restart the mini-mode host after changing
the adapter. Brokered Codex sessions
then require configuring the OpenBao path; disabling a broker does not revoke the
provider grant or disable a runtime's separate host login. Production deployments
select `OpenBaoCodexCredentialBroker` and do not construct the mini-mode lock.

## Before promoting output

Inspect the diff and run the project's checks. A generated instruction or tool
result can be untrusted input, including content retrieved from a repository or
knowledge source. Preserve human or agent approval policies at the point where
work is published, deployed, or sent to another system.
