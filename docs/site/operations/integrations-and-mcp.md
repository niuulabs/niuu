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

The built-in GitHub and GitLab MCP definitions currently use stdio. Their legacy
`@modelcontextprotocol/server-*` packages are deprecated; configure a maintained
server appropriate to the Git host. The platform does not silently send a
self-hosted Git credential to a public MCP endpoint. Merely
connecting a renewable GitLab OAuth account does not convert its MCP server to
HTTP. An operator must configure a compatible endpoint/file-aware server before
that renewable MCP path can launch. Ordinary API keys remain supported.

Linear uses its [official HTTP MCP endpoint](https://linear.app/docs/mcp),
`https://mcp.linear.app/mcp`, with the existing `api_key` field as a bearer header.
No separate MCP login is required. This replaces the nonexistent
`@modelcontextprotocol/server-linear` package.

## Adding another MCP integration

Use the existing configurable integration catalog and OAuth application registry:
register the actual provider endpoints, required scopes and client, configure the
MCP transport and credential mapping, connect the account in Integrations, and
attach that connection to a session. No new global renewal service is required.

An arbitrary MCP URL is not currently sufficient to complete this flow. Automatic
MCP authorization-server discovery and dynamic client registration are not part of
the integration wizard. Configure the provider's OAuth application explicitly;
do not assume a runtime-local interactive login will work in remote sessions.

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
