# Model calls with a session's own credential

A session's broker, or the memory gateway answering a question for a session,
can call Bifröst's model routes with the session's Forge credential. The call is
charged to the **session's owner**, in the owner's tenant, attributed to that
session. Nothing the caller sends names who pays.

## What is accepted, and where

| | |
|---|---|
| Credential | A `forge_session` credential (`token_use: forge_session`) carrying the scope `forge:model`. Every session gets `forge:model` by default. |
| Routes | `POST /v1/messages`, `POST /v1/chat/completions`, `POST /v1/responses` (bare, and under `/api/v1/bifrost`). |
| Auth modes | `open`, `pat`, `mesh` and `oidc`. The credential is accepted in each, in front of the configured adapter. |
| Nowhere else | `/v1/models`, `/v1/usage`, `/models`, `/aliases`, `/providers`, `/admin/*`, the Ollama-compatible routes and every other route behave as before: the credential is never confirmed with Forge there, so in `pat` and `oidc` mode a scoped credential is refused 403 and in `open` and `mesh` the route's own rules apply unchanged. |

A credential without `forge:model` is refused **403** on the model routes.

## How it is confirmed

Bifröst reads the session id off the credential (unverified, only to address
Forge) and presents the whole credential to Forge:
`GET {forge_session.base_url}/api/v1/forge/sessions/{id}`. Forge verifies the
signature, the scope, that the credential is bound to that session and on the
current launch, and that the session is live. **Forge's 200 is the proof.**
Bifröst also checks that Forge answered for the same session and named an owner
and a tenant.

The identity usage is recorded under is Forge's answer:

| Usage field | Value |
|---|---|
| `agent_id` | the session's `owner_id` |
| `tenant_id` | the session's `tenant_id` |
| `session_id` | the session's id |
| `saga_id` | empty |

`X-Agent-Id`, `X-Tenant-Id`, `X-Session-Id`, `X-Saga-Id` and their `X-Ravn-*`
spellings are never read for a session credential, and quotas and agent
permissions apply to the owner exactly as for the owner's own calls.

Nothing is cached: every call asks Forge, so a stopped session, or one restarted
on a new launch, loses model access with its next request.

## When it fails

| Answer | Cause |
|---|---|
| 401, naming why | Forge refused the credential (stopped or archived session, stale launch, bad signature), answered for another session, or did not name an owner and tenant; the credential names no valid session; or `forge_session.base_url` is not set. |
| 403 | The session credential does not carry `forge:model`. |
| 503, naming Forge | Forge could not be asked: unreachable, timed out, or answered 5xx. The credential is neither confirmed nor refused and the call is not admitted. |

## Configuration

```yaml
forge_session:
  base_url: http://volundr:8080   # empty = session credentials are refused 401
  timeout_seconds: 10             # per-request; slower than this answers 503
```

In the Helm chart this is `config.forge_session` of `charts/bifrost`. Behind the
Envoy sidecar (`envoy.jwt`), the issuer that signs session credentials (the
workload-identity issuer) must be accepted by `envoy.jwt.workload`, or Envoy
refuses the token before Bifröst sees it. Where `PATRevocationMiddleware` fronts
Bifröst (the shared host), it admits a session credential on these three routes
only, and only with `forge:model`.

The timeout is listed in [limits.md](limits.md).
