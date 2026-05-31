# Components

## Volundr API

FastAPI application serving the REST API for session management, chronicles, git workflows, and multi-tenant access control.

### Stack

- **Framework:** FastAPI with Uvicorn (4 workers default)
- **Database:** PostgreSQL via asyncpg (raw SQL, no ORM)
- **Real-time:** Server-Sent Events (SSE) for live session state and stats updates
- **Auth:** OIDC JWT validation via pluggable `IdentityPort`, authorization via pluggable `AuthorizationPort`

### Port Interfaces

The API depends exclusively on abstract port interfaces. Every infrastructure concern is behind an ABC:

| Port | Purpose |
|------|---------|
| `SessionRepository` | Session CRUD (PostgreSQL) |
| `ChronicleRepository` | Chronicle persistence, reforge chain walking |
| `TimelineRepository` | Timeline event storage for chronicles |
| `PodManager` | Start/stop/status of session pods (Flux, direct K8s) |
| `StoragePort` | PVC provisioning -- workspace (per-session) and home (per-user) |
| `GatewayPort` | Gateway API config for session HTTPRoute creation |
| `CredentialStorePort` | Pluggable credential storage (Vault, Infisical, memory, file) |
| `SecretInjectionPort` | CSI driver pod spec additions -- Volundr never sees secret values |
| `IdentityPort` | JWT validation and JIT user provisioning |
| `AuthorizationPort` | Action-level authorization decisions (Cerbos, OPA, allow-all) |
| `GitProvider` | Repository validation, listing, branch discovery per git host |
| `GitWorkflowProvider` | PR creation/merge, CI status checks |
| `EventSink` | Event pipeline sink (PostgreSQL always, RabbitMQ and OTel optional) |
| `EventBroadcaster` | SSE event fan-out to connected browser clients |
| `ProfileProvider` | Read-only forge profiles from YAML/CRD config |
| `TemplateProvider` | Read-only workspace templates from YAML/CRD config |
| `TokenTracker` | Token usage recording per session |
| `PricingProvider` | Model pricing metadata |
| `ResourceProvider` | Cluster resource discovery and translation (CPU/GPU/memory) |
| `IssueTrackerProvider` | External issue tracker integration (Linear, Jira) |
| `SecretManager` | K8s secret listing and creation |
| `MCPServerProvider` | Available MCP server configurations |
| `PresetRepository` | User-created runtime config presets (DB-stored) |
| `SavedPromptRepository` | Reusable saved prompts |
| `SessionContributor` | Contributor pipeline -- each wraps a port and produces pod spec additions |
| `StatsRepository` | Aggregate dashboard statistics |
| `UserRepository` | User persistence and tenant membership |
| `TenantRepository` | Tenant hierarchy persistence |
| `IntegrationRepository` | Integration connection persistence |
| `ProjectMappingRepository` | Repo-to-tracker-project mappings |
| `SessionEventRepository` | Read-side query port for persisted session events |
| `SecretRepository` | Vault/OpenBao credential storage and session secret lifecycle |

### Domain Services

| Service | Responsibility |
|---------|---------------|
| `SessionService` | Session CRUD, start/stop orchestration, contributor pipeline execution, readiness polling, archive/restore |
| `ChronicleService` | Chronicle CRUD, broker report ingestion, reforge, timeline aggregation |
| `GitWorkflowService` | PR creation from sessions, merge, CI status, merge confidence calculation |
| `TenantService` | Tenant hierarchy, default tenant provisioning |
| `TokenService` | Token usage recording, cost calculation via pricing provider |
| `StatsService` | Aggregate statistics for the dashboard |
| `RepoService` | Repository listing across all registered git providers |
| `CredentialService` | Credential store operations with mount strategy validation |
| `PresetService` | Preset CRUD with default-flag management |
| `PromptService` | Saved prompt CRUD and search |
| `TrackerService` | Issue tracker operations, project mapping management |
| `EventIngestionService` | Multi-sink event dispatch (fire-and-forget per sink) |
| `IntegrationRegistry` | Catalog of known integration types and their adapter class paths |
| `UserIntegrationService` | Per-user ephemeral provider factory (git + issue tracker) |
| `ForgeProfileService` | Profile listing with active session counts |
| `WorkspaceTemplateService` | Template listing and lookup |
| `WorkspaceService` | Workspace PVC listing and management |

### REST Endpoints

Routes are organized into separate router modules under `adapters/inbound/`:

| Module | Prefix | Tags | Key Operations |
|--------|--------|------|---------------|
| `rest.py` | `/` | Sessions, Chronicles, Timeline, Models & Stats | Session CRUD, start/stop, chronicle CRUD, reforge, timeline, SSE stream, models, stats |
| `rest_git.py` | `/git` | Git Workflow | Create PR, merge PR, CI status, list PRs |
| `rest_profiles.py` | `/profiles`, `/templates` | Profiles, Templates | List/get profiles and workspace templates |
| `rest_presets.py` | `/presets` | Presets | Preset CRUD, set default |
| `rest_prompts.py` | `/prompts` | Prompts | Prompt CRUD, search |
| `rest_tenants.py` | `/tenants`, `/users` | Tenants | Tenant/user CRUD, membership management |
| `rest_credentials.py` | `/credentials` | Credentials | Credential store/list/delete, mount preview |
| `rest_secrets.py` | `/mcp-servers`, `/secrets` | MCP Servers, Secrets | MCP server listing, K8s secret management |
| `rest_events.py` | `/sessions/{id}/events` | Events | Event ingestion, query, token timeline |
| `rest_integrations.py` | `/integrations` | Integrations | Integration catalog, connection management |
| `rest_tracker.py` | `/tracker` | Issue Tracker | Issue search, status updates, project mappings |
| `rest_resources.py` | `/resources` | Resources | Cluster resource discovery |
| `rest_admin_settings.py` | `/admin/settings` | Admin | Runtime admin settings |

### Event Pipeline

Session events flow through a multi-sink pipeline:

1. Skuld broker (or API routes) emits `SessionEvent` objects.
2. `EventIngestionService` dispatches to all registered sinks concurrently.
3. Sink failures are logged but do not block other sinks.

Sinks:

- **PostgreSQL** (always enabled) -- buffered writes via `PostgresEventSink`
- **RabbitMQ** (optional) -- AMQP publish to a configurable exchange
- **OpenTelemetry** (optional) -- GenAI semantic conventions via OTLP gRPC

---

## Skuld Broker

WebSocket bridge that sits inside each session pod and connects the browser to the AI agent CLI. One Skuld instance per session.

### Architecture

```
Browser                    Skuld Broker                AI CLI
  |                            |                         |
  |---WebSocket /ws/chat------>|                         |
  |                            |---SDK WebSocket-------->|
  |                            |   (or subprocess)       |
  |<---streaming events--------|<---NDJSON events--------|
  |                            |                         |
```

### Transport Modes

Skuld supports multiple CLI backends through the `CLITransport` abstraction:

**SDK WebSocket Transport** (default for Claude Code):

- Skuld spawns the Claude Code CLI with `--sdk-url ws://localhost:{port}/ws/cli/{session_id}`.
- The CLI connects back to Skuld over a second WebSocket.
- Messages flow as NDJSON over this persistent connection.
- Supports session resume, control messages (interrupt, model switch), and tool permission responses.
- Keep-alive ping every 10 seconds.

**Subprocess Transport** (legacy fallback for Claude Code):

- Spawns `claude -p <message>` per user message.
- Reads stream-json output from stdout.
- No persistent connection between messages.

**Codex Subprocess Transport** (OpenAI Codex CLI):

- Spawns `codex --model <model> --full-auto <message>` per message.
- Normalizes Codex events to the common broker format (tool names mapped, synthetic result events generated).
- Does not use the SDK WebSocket protocol.

### Session Management

Skuld manages multiple concurrent sessions via `ServiceManager`:

- Each session has its own transport instance, channel registry, and workspace directory.
- Sessions can be created, stopped, and listed via Skuld's REST API.
- Session definitions (from Kubernetes CRDs) configure CLI type, model, permissions, and environment.

### Channels

Skuld supports multiple output channels per session:

- **WebSocket channel** -- primary, streams to connected browsers
- **Telegram channel** -- optional, forwards events to a Telegram bot

### Reporting

On session shutdown, Skuld reports back to the Volundr API:

- Token usage (input/output tokens per model)
- Chronicle data (summary, key changes, unfinished work)
- Activity metrics (message count, duration)

This is the only point where Skuld talks to the Volundr API. During active chat, the API is not involved.

---

## Web UI

React single-page application for session management, chat, and administration.

### Stack

- **Framework:** React 18+, TypeScript, Vite
- **State:** Zustand stores
- **Styling:** CSS Modules with design tokens (no Tailwind, no inline styles, no CSS-in-JS)
- **Auth:** OIDC via the configured identity provider
- **Real-time:** WebSocket for chat, SSE for session state updates

### Architecture

The web UI follows the same hexagonal pattern as the backend:

```
UI Components
     |
     v
  Zustand Stores (state management)
     |
     v
  Service Ports (abstract interfaces)
     |
     v
  HTTP/WS Adapters (fetch, WebSocket)
```

Port interfaces define the contract between the UI and the backend. Adapters implement these ports using `fetch` for REST calls and native `WebSocket` for chat. This makes it possible to swap backends or run the UI against mock data for testing.

### Pages

| Page | Function |
|------|----------|
| Sessions | List, create, start, stop, archive sessions |
| Chat | Interactive AI chat within a running session |
| Terminal | Terminal access to the session pod (ttyd) |
| Code | VS Code in the browser (code-server) |
| Diffs | File change viewer for session workspaces |
| Chronicles | Session history, reforge chains, timeline visualization |
| Settings | User preferences, credentials, integrations |
| Admin | Tenant management, user management, system settings |

---

## CLI

Python package (`src/cli/`) built with Textual for the TUI and Click for command parsing. Compiled to a single native binary via Nuitka.

### Features

- Plugin-based service registry for managing local Volundr services
- Textual TUI for interactive session management
- Connects to a deployed Volundr instance or runs services locally
- OIDC authentication (device flow and authorization code flow)

### Stack

- **Framework:** Click (commands) + Textual (TUI)
- **Packaging:** Nuitka single-binary compilation
- **Language:** Python 3.12+
