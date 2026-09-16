# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Use GitHub's private vulnerability reporting flow for this repository:
<https://github.com/niuulabs/volundr/security/advisories/new>

Include:

- A clear description of the issue and the affected component.
- Steps to reproduce or a proof of concept.
- Any suggested mitigation or patch if you have one.

## What to expect

We will acknowledge valid reports as soon as practical, triage severity, and work toward a fix before public disclosure.

When coordination is needed, we will use the private advisory thread to share status updates and release guidance.

## Disclosure

Please give us reasonable time to investigate, prepare a fix, and publish a coordinated disclosure.

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

Existing KV credentials are not automatically migrated. Configure the matching
OAuth client and reconnect a renewable integration to enroll it in the engine.
Deleting a stored grant does not itself revoke an already-issued upstream token.
See [OpenBao renewal operations](docs/operations/openbao-oauth.md) for configuration,
recovery, historical KV-version retention, and live rotation/isolation checks.

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

### Codex subscription credentials: remaining application refresh

Codex subscription login is still a separate path. The authenticated
`/api/v1/internal/credentials/codex/tokens` endpoint derives the owner from the
caller identity. `OpenBaoCodexCredentialBroker` reads that user's nested `auth.json`
from the credential store and, when needed, refreshes it **inside the Volundr API
process**, then writes rotated tokens back. It returns access tokens and Codex
account metadata to the caller. The broker's name describes storage, not refresh
execution by OpenBao.

The mini-mode flag above does **not** disable this on-demand broker. Moving its
refresh work to `oauthapp` requires migrating the nested credentials, preserving
Codex account metadata, removing the competing application refresh path, and
validating actual provider rotation. Until that migration is implemented and
verified, do not assume OpenBao owns Codex subscription renewal.
