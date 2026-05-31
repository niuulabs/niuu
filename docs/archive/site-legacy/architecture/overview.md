# Architecture Overview

Volundr is a self-hosted remote development platform that provisions ephemeral coding sessions on Kubernetes. Each session runs AI-assisted development tools (Claude Code, Codex) inside isolated pods with their own workspace, terminal, and IDE.

## System Diagram

```
                         Users
                    +-----------+
                    | Web UI    |  React/Vite, OIDC auth
                    | (browser) |
                    +-----+-----+
                          |
              +-----------+-----------+
              |                       |
         REST/SSE                 WebSocket
         (sessions,               (chat)
          chronicles,                |
          git, etc.)                 |
              |                      |
    +---------v----------+           |
    |   Volundr API      |           |
    |   (FastAPI)        |           |
    |                    |           |
    |  +-- Ports ---+    |           |
    |  | Session    |    |           |
    |  | Chronicle  |    |           |
    |  | PodManager |    |           |
    |  | Storage    |    |           |
    |  | Gateway    |    |           |
    |  | Identity   |    |           |
    |  | AuthZ      |    |           |
    |  | Git        |    |           |
    |  | Events     |    |           |
    |  | Secrets    |    |           |
    |  | Resources  |    |           |
    |  +------------+    |           |
    +---+---+---+---+----+           |
        |   |   |   |               |
        |   |   |   +--- Event Pipeline ---> PostgreSQL (always)
        |   |   |                    |       RabbitMQ  (optional)
        |   |   |                    |       OTel      (optional)
        |   |   |                    |
        |   |   +--- Git APIs -----> GitHub / GitLab
        |   |                        |
        |   +--- IDP --------------> Keycloak / Entra ID / Okta
        |                            |
        +--- Kubernetes API          |
             |                       |
    +--------v-----------------------v--------+
    |          Session Pod                     |
    |  +-------------+ +----------+ +-------+ |
    |  | Skuld broker | | code-    | | ttyd  | |
    |  | (WebSocket   | | server   | | term  | |
    |  |  <-> CLI)    | | (VS Code)| |       | |
    |  +------+-------+ +----------+ +-------+ |
    |         |                                 |
    |    Claude Code CLI / Codex CLI            |
    |         |                                 |
    |  +------v------+     +-----------+        |
    |  | Workspace   |     | Home PVC  |        |
    |  | PVC         |     | (per-user)|        |
    |  | (per-session)|     +-----------+        |
    +-----------+-------------------------------+
                |
       +--------v--------+
       | Vault / Infisical|  (CSI secret injection)
       +------------------+

    +-------------+
    | CLI (Go)    |  Local mode: embedded stack
    | local/remote|  Remote mode: REST + WebSocket to API
    +-------------+
```

### Key data path: Chat bypasses the API

Chat messages flow directly from the browser to the Skuld broker inside the session pod via WebSocket. The Volundr API is not in this path. This keeps latency low and avoids the API becoming a bottleneck during interactive coding.

```
Browser  --WebSocket-->  Skuld broker  --SDK WS-->  Claude Code CLI
                              |
                    (reports usage/chronicle
                     back to API on shutdown)
```

## Data Flows

### 1. Session Creation (Contributor Pipeline)

Session creation is a two-phase process: record creation followed by pod provisioning.

1. API receives `POST /sessions` with name, model, source config, optional template/preset.
2. Template and profile defaults are resolved (if specified).
3. Repository is validated against the git registry (if git source with validation enabled).
4. Session record is persisted with status `CREATED`.
5. `start_session()` transitions to `STARTING` and runs the **contributor pipeline**:
   - Build a `SessionContext` from the request metadata (principal, template name, credentials, integrations).
   - Run 10+ contributors sequentially. Each returns a `SessionContribution` (Helm values + pod spec additions).
   - Deep-merge all contributions into a single `SessionSpec`.
6. `PodManager.start()` submits the merged spec to the backend (Flux or direct K8s).
7. Session transitions to `PROVISIONING`. A background task polls readiness.
8. When all containers report ready, session transitions to `RUNNING`.

The contributors, in order:

| # | Contributor | Responsibility |
|---|-------------|---------------|
| 1 | Core | Session identity, ingress host, terminal restriction |
| 2 | Template | Workspace template defaults (repos, setup scripts) |
| 3 | Git | Clone URL, branch, git credentials |
| 4 | Integration | MCP servers, env vars from enabled integrations |
| 5 | Storage | Workspace PVC + home PVC provisioning |
| 6 | Gateway | Gateway API HTTPRoute config for session routing |
| 7 | Resource | CPU/memory/GPU requests, node selectors, tolerations |
| 8 | Isolation | Namespace, security context, network policy |
| 9 | SecretInjection | CSI driver volumes/mounts for secret injection |
| 10 | Secrets | K8s secret env refs |

### 2. Chat (WebSocket through Skuld)

1. Browser opens WebSocket to Skuld broker (`wss://<session>.volundr.example.com/ws/chat`).
2. User message arrives as JSON over WebSocket.
3. Skuld forwards the message to the CLI transport:
   - **SDK transport** (Claude Code): sends NDJSON message over a second WebSocket to the CLI process, which connected back via `--sdk-url`.
   - **Subprocess transport** (legacy): spawns `claude -p <message>` as a subprocess.
   - **Codex transport**: spawns `codex --full-auto <message>`, normalizes output to the common event format.
4. CLI streams response events (content deltas, tool use, result) back through the transport.
5. Skuld forwards events to the browser over the original WebSocket.
6. On `result` event, Skuld extracts token usage and reports it back to the Volundr API via HTTP.

The API is never in the chat hot path.

### 3. Chronicle Creation

Chronicles capture what happened during a session for continuity across reforges.

1. Session stops (user-initiated or timeout).
2. Skuld broker sends a final report to the Volundr API: summary, key changes, unfinished work, duration.
3. Volundr creates (or enriches an existing draft) chronicle with session metadata plus broker data.
4. Timeline events collected throughout the session lifetime are aggregated into file summaries, commit summaries, and token burn charts.
5. Chronicle status moves from `DRAFT` to `COMPLETE`.

### 4. Authentication Flow

Volundr is IDP-agnostic. Authentication is delegated to a standard OIDC provider.

1. User authenticates with the IDP (Keycloak, Entra ID, Okta, etc.) via the web UI's OIDC flow or the CLI's device/authorization code flow.
2. JWT access token is sent with every API request in the `Authorization` header.
3. The `IdentityPort` adapter validates the JWT (signature, expiry, issuer, audience).
4. On first login, JIT provisioning creates the user record, home PVC, and secret backend resources.
5. A `Principal` (user_id, email, tenant_id, roles) is extracted from the validated token.
6. The `AuthorizationPort` adapter (Cerbos, OPA, or allow-all for dev) checks whether the principal can perform the requested action on the target resource.

In production, Envoy sits in front of session pods and validates JWTs before traffic reaches Skuld.

## Key Design Decisions

**Raw SQL via asyncpg.** No ORM. Queries are parameterized, migrations are idempotent SQL files run by `migrate` (Kubernetes-native). This keeps the data layer explicit and avoids the abstraction overhead of an ORM.

**Dynamic adapter loading.** Infrastructure adapters are specified as fully-qualified Python class paths in YAML config. The composition root (`main.py`) imports each class dynamically and passes remaining config keys as kwargs. Adding a new adapter means writing the class and updating YAML -- zero code changes elsewhere.

**Ports over mocks.** Business logic depends only on abstract port interfaces (ABCs). Tests can use stub implementations without mocking framework magic. The domain layer has zero imports from the adapters package.

**CSI-based secrets.** Volundr never reads or stores secret values. It generates pod spec additions (volumes, mounts, annotations) that tell the CSI driver (Vault Agent, Infisical) how to inject secrets at pod startup. The `SecretInjectionPort` returns `PodSpecAdditions`, not secret data.

**Config-driven profiles/templates vs. user-owned presets.** Profiles and workspace templates are read-only, loaded from YAML config or Kubernetes CRDs -- managed by platform operators. Presets are DB-stored, user-created runtime configurations (model, MCP servers, resources). This separation keeps operator guardrails distinct from user preferences.

**Direct WebSocket for chat.** Chat traffic goes straight from the browser to the Skuld broker inside the session pod. The Volundr API is not in this path. This eliminates a proxy hop, reduces latency, and means the API can restart without interrupting active chat sessions.

**Session contributor pipeline.** Pod spec assembly is decomposed into independent contributors that run sequentially. Each contributor wraps a single port and produces values and pod spec additions. The contributions are deep-merged into a single `SessionSpec`. This makes pod composition extensible without touching the session service.
