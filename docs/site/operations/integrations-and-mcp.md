# Integration credentials and MCP sessions

Connect accounts in **Settings → Integrations**, then attach the connection to a
session. Authentication happens in the platform's integration flow, before the
remote runtime starts. A remote Claude session does not need access to `/mcp`, and
a headless Codex session does not need to run `codex mcp login`.

## What can renew

| Credential | Storage and renewal | When it stops working |
| --- | --- | --- |
| OAuth grant with a refresh token | OpenBao `oauthapp` owns rotation; the application stores the grant reference and auxiliary account fields | Reconnect when the provider revokes or expires the refresh grant |
| API key, PAT, or OAuth token without a refresh token | Existing user credential store; no renewal engine can manufacture a missing refresh grant | Replace the key or reconnect |
| Claude subscription setup token | Existing vault-backed integration; enrollment supplies a token, not a refresh grant | Repeat the Claude setup flow when required |
| Codex subscription | Dedicated import of nested `auth.json` into the same OpenBao engine | Reconnect the ChatGPT integration if the refresh grant is expired |

New renewable integrations enroll directly into OpenBao using the same OAuth
application that issued the grant. Provider-specific token formats and additional
refresh parameters must be supported by the configured engine provider; this is
not a promise that every OAuth provider accepts the same refresh request.

The production control plane does not scan users to refresh tokens. See
[security and permissions](security-and-permissions.md#mini-modes-legacy-refresh-scan)
for the separate mini-mode compatibility scanner and how to disable it.

## HTTP MCP

The integration catalog defines the server transport, URL, and credential field.
For an existing registered OAuth integration, configure its `mcp_server` with
`transport: http`, its actual HTTPS endpoint, `token_field`, `auth_header`, and
`auth_prefix`. The OAuth client and scopes must authorize that endpoint; a token
issued for a different resource is not interchangeable.

At session launch:

1. Verify the selected connection belongs to the session user and tenant.
2. Read the engine grant so an unavailable credential blocks launch.
3. Give the session's OpenBao identity read access to only its selected grant paths.
4. Project current access tokens into files through the existing OpenBao Agent.
5. Generate Claude's `headersHelper` or Codex's `http_headers_helper` configuration.
   These invoke the existing file reader, which rejects malformed or expired tokens.

The configuration contains a file path, not the provider refresh token. The engine
coordinates renewal across parallel sessions; the runtime helper reads updated
access tokens when the runtime connects or reconnects. Runtime retry timing still
matters: a file update does not itself reconnect an already-open MCP connection.
An expired/revoked grant requires reconnecting in Integrations and restarting the
session if its runtime cannot recover automatically.

OpenShell uses its existing workload-bound credential broker and endpoint-scoped
header injection instead of Agent files. The runtime sees an opaque credential;
the broker obtains current access tokens from OpenBao.

This follows the runtimes' native configuration mechanisms:
[Claude dynamic headers](https://code.claude.com/docs/en/mcp#dynamic-headers) and
[Codex MCP configuration](https://learn.chatgpt.com/docs/config-file/config-reference).
The runtime integration test uses the pinned CLI versions, an isolated MCP server,
and no real provider account or model call.

## Stdio and CLI-driven MCP

Static API-key/PAT servers can receive their configured environment variables at
startup. Codex explicitly allowlists these names with `env_vars`; credential
values are supplied by existing secret injection, not embedded into its config.

A running process cannot acquire a changed environment variable. Consequently,
Agent-backed renewable stdio servers that take tokens from environment variables
are rejected at launch. Use an HTTP endpoint, or configure a server that reads its
credential file on demand through the integration's existing file mounts. Restart
static stdio sessions after replacing credentials.

GitHub and GitLab source-control connections continue to supply `gh` and `glab`,
which are installed in the runtime image. Connect their MCP services using the
generic MCP form below. This gives the MCP server its own grant and audience;
GitLab's `mcp` scope is distinct from a CLI application's `api` scope. The runtime
no longer launches the deprecated `@modelcontextprotocol/server-github` or
`@modelcontextprotocol/server-gitlab` packages.

Linear uses its [official HTTP MCP endpoint](https://linear.app/docs/mcp),
`https://mcp.linear.app/mcp`, with the existing `api_key` field as a bearer header.
No separate MCP login is required. This replaces the nonexistent
`@modelcontextprotocol/server-linear` package.

## Adding another MCP integration

In **Settings → Integrations → MCP server**, enter the server's HTTPS URL and
choose **Sign in with OAuth** or **API token**. OAuth discovery shows the issuer
and requested permissions before sign-in. The platform supports protected-resource
metadata, OAuth/OIDC authorization-server metadata, PKCE S256, client metadata
documents, dynamic client registration, and pre-registered clients. Servers that
require a registered client expose the client ID and authentication fields.

The URL suggestions include GitHub (`https://api.githubcopilot.com/mcp/`), GitLab
(`https://gitlab.com/api/v4/mcp`), and Linear (`https://mcp.linear.app/mcp`). Other
standards-compliant MCP endpoints use the same flow. GitLab must have its MCP
server enabled by the group or instance administrator. Select **Reconnect** for
an existing connection when its grant expires or is revoked; changing the server
URL requires a new connection. Attach the completed connection to a session.

API tokens can use a configurable header and prefix. The **Test** action performs
MCP initialization without listing tools or reading user data. Operator-configured
stdio servers continue to use catalog environment/file mappings.

Discovered endpoints must resolve to public addresses. Discovery, token exchange,
and engine refresh pin validated DNS results and reject private/special-use
addresses and redirects. A publicly reachable self-hosted MCP endpoint works.

To run MCP servers on your own cluster, list their hostnames, and those of their
OAuth issuers, in `oauth.mcp_internal_hosts` (Helm: `oauth.mcpInternalHosts`), for
example `["*.asgard.niuu.world"]`. Entries are exact hostnames or `*.domain`
suffixes; a suffix matches any subdomain but not the domain itself, and must name
at least two labels. Listed hosts may resolve to private addresses (RFC 1918,
unique-local, CGNAT) but are still pinned, TLS-verified, and refused on loopback,
link-local, multicast, or reserved addresses. Every other host keeps the public
check. When an issuer's authorization or token endpoint is on the list, its OpenBao
server definition is registered without `public_endpoints_only`, so the engine
can refresh against it. Invalid or overly broad entries fail at startup. The
check that the install's own client metadata document is publicly reachable
ignores the list, even when the install's host matches it: an external
authorization server must be able to fetch that document itself.

Set `oauth.redirect_base_url` to the install's external HTTPS origin (Helm:
`oauth.redirectBaseUrl`). Allow unauthenticated GETs to the MCP callback and client
metadata document at `/api/v1/integrations/oauth/mcp/{callback,client-metadata}`;
discovery and connection creation still require the user's platform identity.

MCP renewal requires oauthapp with the RFC 8707 resource refresh extension and
public-endpoint restriction. The infrastructure image `openbao-oauthapp` carries
the reviewed patch over upstream 3.4.0. Set the credential adapter's
`mcp_resource_indicators: true` only with that plugin installed. An unpatched
engine is rejected before OAuth enrollment. Refresh remains inside OpenBao;
no application-wide token scanner is added.

## Migrating existing renewable grants

First deploy the OpenBao credential store everywhere and stop legacy production
refreshers. Verify the connection's owner, tenant, integration slug, OAuth
application, and access-token field against its records and catalog definition.
Run `scripts/openbao/migrate_integration.py` with the existing workload-auth
`--config` plus explicit `--owner`, `--tenant`, `--credential`, `--integration`,
`--oauth-app`, and `--token-field` arguments. The default is a dry run; `--apply`
imports one verified grant.

Run only one migration for a credential at a time. It checks for an existing engine
grant before exchanging the old refresh token, so a failed KV metadata write can
be resumed without replaying a rotated token. Current KV data retains auxiliary
fields, but removes access, refresh, and ID tokens. Historical versions remain
subject to the vault's retention policy. Missing/expired refresh grants require
reconnection; do not repeatedly retry them.

The adapter's `maximum_expiry_seconds` defaults to 3600 and bounds cached grants
when providers omit expiry. Configure it below the provider's actual lifetime and
above `minimum_seconds`. This bound schedules renewal; it does not extend upstream
validity. Validate real provider acceptance after import.
