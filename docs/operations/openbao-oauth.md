# OpenBao-managed integration renewal

Volundr can delegate provider OAuth renewal to the Apache-2.0
[OpenBao oauthapp secrets engine](https://github.com/openbao/openbao-plugin-secrets-oauthapp).
OpenBao KV alone does **not** refresh provider tokens. The external plugin must be
installed, registered and mounted by the OpenBao operator before using this adapter.
Use a pinned, reviewed plugin release compatible with your OpenBao deployment.

## Ownership and session flow

1. Existing integration enrollment obtains the grant in the authenticated user's
   context. The store imports its refresh token into `oauthapp/creds/<identity>`.
   Import immediately exchanges the refresh token using the configured OAuth client.
2. The identity hashes `(tenant_id, owner_id, credential_name)`. KV stores connection
   metadata and the engine reference, with no access or refresh token in the new
   document. Ordinary API keys and nonrenewable credentials still use KV.
3. Launch verifies the connection owner and credential tenant, and reads a valid
   access token from the engine before provisioning injection. Unavailable grants
   prevent launch. No access-token value is put in a pod spec or ConfigMap.
4. The existing session ServiceAccount/JWT role receives read access only to the
   selected engine credential paths. It cannot read server/client configuration,
   export refresh tokens, or create/update grants.
5. An init agent renders the initial credentials; a continuing OpenBao Agent sidecar
   authenticates with that session identity and updates files. The oauthapp plugin
   owns provider refresh. Agent auto-auth renews the separate OpenBao identity.
6. For HTTP MCP, Claude `headersHelper` and Codex `http_headers_helper` invoke
   `python3 -m skuld.mcp_credentials`. The helper reads an atomically rendered JSON
   document containing access token and expiry; it rejects expired/malformed tokens.
   Runtime connection/retry behavior determines when it rereads the file. It does
   not need an interactive `/mcp` command or a provider refresh token in the pod.

Parallel sessions read the same engine credential. The plugin serializes refresh
for that credential and forwards refresh work to the active backend. This replaces
application-level refresh races; it does not establish exactly-once semantics across
provider failures or storage outages. Import and the KV metadata write are separate
operations: if import succeeds but the metadata write fails, use the resumable per-connection
migration to finish the KV update; do not retry an old rotating refresh token blindly.

The control-plane adapter remains privileged to manage grants. Use workload auth,
restrict its OpenBao policy, audit access, and keep it separate from session roles.
Existing connection/KV metadata remains user-scoped; this change does not migrate
all platform data to tenant namespaces. Session launch enforces the stored OAuth
tenant and owner; engine paths are tenant/owner scoped.

## Configure the existing adapters

Keep existing URL, namespace, KV mount, workload-auth and JWT/injector settings.
Change/add these application configuration fields:

```yaml
credential_store:
  adapter: niuu.adapters.openbao_oauth_credential_store.OpenBaoOAuthCredentialStore
  kwargs:
    # Include existing OpenBao URL, KV mount and workload auth kwargs here.
    oauth_mount_path: oauthapp
    minimum_seconds: 120
    maximum_expiry_seconds: 3600
    manage_oauth_applications: true

secret_injection:
  adapter: volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter
  kwargs:
    # Include existing injector URL, namespace, auth path and workload auth kwargs here.
    oauth_mount_path: oauthapp
    refresh_interval_seconds: 30

oauth:
  mini_mode_refresh_enabled: false
```

For Helm, use `credentialStore`, `secretInjection`, and
`oauth.miniModeRefreshEnabled`; adapter kwargs remain snake_case. Both adapters
must use the same OpenBao namespace and `oauth_mount_path`. Existing dynamic adapter
loading handles these settings; no new service is deployed.

With `manage_oauth_applications: true`, the existing OAuth application registry
provisions a deterministic `oauthapp/servers/niuu-<hash>` entry when an application
is registered and when stored/configured clients are loaded. Enrollment and renewal
therefore use the same client and catalog endpoints, including self-hosted HTTPS
origins. This requires create/update permission on `oauthapp/servers/niuu-*` for
the control-plane identities; session roles must never receive that permission.
Application secrets remain in the existing credential-store registration flow.
Removing a registry entry does not delete the engine server or revoke active grants.

For operator-managed clients instead, leave `manage_oauth_applications: false` and
provide `oauth_servers`. Each `oauth_servers` key is `integration-slug/oauth-app` (`default` if no named app).
The value names an operator-provisioned `oauthapp/servers/<name>` entry. Its provider,
endpoints, OAuth client ID and secret must match the app that issued the grant.
Self-hosted endpoints and named OAuth applications need their own server entries.
In manual mode, registering a client in the integration UI does not provision a plugin server.
Missing mappings fail enrollment rather than falling back to the old refresher.
Do not put OAuth client secrets into Helm plaintext values or session config.

Grant the control-plane identity read/create/update/delete on the engine's `creds`
paths as required, alongside existing KV access. Provision `servers` and plugin
configuration with a separate operator identity. Session ACLs are generated as
literal per-credential read paths, without a tenant-wide wildcard.

Configure plugin background refresh with sufficient margin for provider latency
and the Agent polling interval. For example, a 60-second check interval with
`tune_refresh_expiry_delta_factor=3` gives a 180-second refresh window, ahead of
30-second Agent polling. Account for the provider's actual token lifetime; a token
lifetime shorter than `minimum_seconds` requires an appropriately smaller setting.
Disable the plugin reaper (`tune_reap_check_interval_seconds=0`) if revoked grants
should remain present for explicit reconnection/deletion. The plugin `config`
write **replaces** configuration, so include all settings you intend to retain.
See the [plugin configuration reference](https://github.com/openbao/openbao-plugin-secrets-oauthapp#config)
and [Agent template renewal behavior](https://openbao.org/docs/agent-and-proxy/agent/template/).
Monitor the agent sidecar and plugin refresh failures.

## Catalog HTTP MCP configuration

An integration definition can specify:

```yaml
mcp_server:
  name: gitlab
  transport: http
  url: https://gitlab.com/api/v4/mcp
  token_field: token
  auth_header: Authorization
  auth_prefix: "Bearer "
```

Use the actual endpoint and access-token field supported by that integration.
The named `token_field` is projected to a per-connection file; the runtime config
contains the path, not the token. HTTP static keys also use the file helper.
The deployed Claude/Codex versions must support their respective header helpers;
validate this on the pinned runtime images before rollout. Existing stdio API-key
MCP configurations continue unchanged.

Renewable stdio MCPs with credentials in their environment are rejected at launch:
a running process cannot pick up an updated environment. Use HTTP or a stdio server
that reads a configured credential file on demand. File-aware stdio and non-MCP
integrations remain responsible for reloading their own files. OpenShell sessions use their existing SPIFFE-bound dynamic provider broker instead
of Agent projection. The broker reads the current access token from the engine,
checks the tenant and permitted token field, and caps its cache lifetime at the
provider token expiry. HTTP MCP configs read the opaque provider environment
credential; OpenShell supplies the real header for the selected endpoint. Managed
static-file/materialized-environment projection remains unsupported on OpenShell.

## Rollout and recovery

The application-wide refresh task now starts **only** when both
`local_mounts.mini_mode` and `oauth.mini_mode_refresh_enabled` are true. The latter
keeps its default of true for local mini-mode compatibility. Even there, credentials
marked with a renewal owner are excluded from the old loop. Kubernetes deployments
cannot enable that loop just by setting the OAuth flag.

Before upgrading production, provision the plugin and client-provisioning policy (or manual server mappings), configure
both adapters, and reconnect renewable integrations. Existing KV grants are not
silently migrated. Reconnection imports a fresh grant; review and destroy historical
KV versions containing previous refresh tokens according to your retention policy.
If credentials were stored in another backend, reconnect there through the new
adapter rather than changing storage and assuming old values move automatically.

Listing integrations checks managed credential availability on demand. Expired or
missing grants report `auth_required`; engine authorization/configuration failures
report `unavailable`. Session launch propagates failure rather than injecting an
empty token. Provider errors are not reflected verbatim. A still-valid access token
can remain usable until expiry even after refresh-token revocation.

Disconnect deletes the engine credential and KV reference; deleting an engine
credential does not itself revoke tokens at the upstream provider. Complete
provider revocation through its supported revoke flow/account UI when required.
Rollback must not start the legacy refresher against engine references: either keep
the adapter or reconnect into the previous backend.

Before production rollout, exercise two concurrent sessions through a real token
rotation, provider revocation/reconnect, Agent restart, and OpenBao leader failover.
Verify no refresh/client secret reaches the pod and a session cannot read another
owner's or tenant's grant. Unit tests cover the API contracts, scoping, projection,
runtime helpers and mini-mode gating; they do not replace this deployment check.

## Codex subscription migration

Codex subscription refresh is delegated to the same engine. The token delivery
endpoint remains, but the production broker never exchanges the provider refresh token.
New device-login enrollment imports its nested `auth.json` grant and retains account
metadata and unrelated configuration in KV. Session responses contain no refresh or
ID token. Tenant membership and credential ownership are checked before grant reads.

1. Provision `oauthapp/servers/niuu-codex-subscription` as a `custom` provider with
   Codex's public client ID `app_EMoamEEZ73f0CkXaXp7hrann`, token URL
   `https://auth.openai.com/oauth/token`, and `auth_style: in_params`. No client
   secret is used. The infrastructure reconciler manages this definition.
2. Set `codex_oauth_server: niuu-codex-subscription` in every OAuth store's kwargs.
   `codex_maximum_expiry_seconds` defaults to 3600 and bounds the engine's cached
   lifetime even when the provider omits `expires_in`. Keep it below the provider's
   actual token lifetime and above `minimum_seconds`.
3. Roll out the new broker to **all clusters and replicas before importing grants**.
   Verify no legacy broker still refreshes nested KV credentials. Pause concurrent
   enrollment for the account during this one-time operation.
4. Verify the owner and tenant against the identity/connection records. Run
   `scripts/openbao/migrate_codex.py` with the existing application's workload-auth
   config and explicit `--owner`, `--tenant`, and `--credential`. The default checks
   eligibility only. Add `--apply` to import and rotate. Run one operator invocation
   per credential; do not run concurrent migrations in different clusters.
5. Verify token delivery through each cluster, account metadata, provider acceptance,
   and that the current KV version no longer contains `auth.json`. The plugin test
   at `tests/integration/openbao/test_codex_renewal.py` exercises actual engine
   rotation, concurrent readers, grant ACLs, and revocation using an isolated test
   OAuth provider. It does not prove acceptance by OpenAI; verify the live grant too.

The importer checks for an existing engine grant before exchanging a legacy refresh
token, so an interrupted KV metadata write can be resumed. It validates the engine
server and account before completing the KV update. An already-managed credential
is validated without reimport. Old rotating tokens must never be replayed blindly.
There is no atomic transaction between the upstream provider and OpenBao storage;
if a provider exchange succeeds but the plugin cannot persist it, reconnect.
Historical KV versions remain until the operator applies the retention policy.

Unmigrated grants fail with a reconnect/migration response. Missing or revoked grants
require reconnection; transient engine failures return HTTP 503. `oauthapp` v3.4.0
has no force-refresh API: a 401 retry receives a newly rotated token only if the
engine has rotated it already, otherwise reconnect is required. The legacy mini-mode
scanner cannot renew managed Codex grants and is not needed for this path.

Docker mini mode retains an explicitly selected `MiniModeCodexCredentialBroker`
for file-backed credentials. It is guarded by `local_mounts.mini_mode`, uses the
existing database advisory lock, and rejects engine-managed grants. This preserves
local compatibility without adding a production refresh fallback. Its on-demand
refresh is separate from the optional mini-mode integration scan; see the published
[security guide](https://docs.niuu.cloud/operations/security-and-permissions/).

For ordinary integration migration and the current MCP setup flow, see the
[published integration guide](https://docs.niuu.cloud/operations/integrations-and-mcp/).
