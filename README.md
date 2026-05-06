# niuu

[![CI](https://github.com/niuulabs/volundr/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/niuulabs/volundr/actions/workflows/ci.yaml)
[![Release](https://github.com/niuulabs/volundr/actions/workflows/release.yaml/badge.svg)](https://github.com/niuulabs/volundr/actions/workflows/release.yaml)
[![Secret Scan](https://github.com/niuulabs/volundr/actions/workflows/secrets.yaml/badge.svg?branch=main)](https://github.com/niuulabs/volundr/actions/workflows/secrets.yaml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/niuulabs/volundr/badge)](https://scorecard.dev/viewer/?uri=github.com/niuulabs/volundr)
[![Coverage](https://codecov.io/gh/niuulabs/volundr/branch/main/graph/badge.svg)](https://codecov.io/gh/niuulabs/volundr)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

Self-hosted AI-native development platform. Provisions ephemeral coding sessions on Kubernetes where AI agents assist with real-time development, runs autonomous task agents, and routes across multiple LLM providers — all under your own infrastructure.

<p align="center">
  <img src="docs/site/images/dashboard.png" alt="Session dashboard" width="720">
</p>

## Platform Components

```
                         Users
                    ┌───────────┐
                    │  Web UI   │  React/Vite, OIDC auth
                    │ (browser) │
                    └─────┬─────┘
                          │
              ┌───────────┴───────────┐
              │                       │
         REST/SSE                 WebSocket
         (sessions,               (chat)
          chronicles,
          git, etc.)
              │                       │
    ┌─────────▼──────────┐            │
    │   Volundr API      │            │
    │   (FastAPI)        │            │
    └────────┬───────────┘            │
             │                        │
    ┌────────┴──────────────────────┐ │
    │ Kubernetes                    │ │
    │  ┌──────────────────────────┐ │ │
    │  │      Session Pod         │ │ │
    │  │  ┌────────┐ ┌─────────┐  │◄┘ │
    │  │  │ Skuld  │ │VS Code  │  │   │
    │  │  │(broker)│ │ Server  │  │   │
    │  │  └───┬────┘ └─────────┘  │   │
    │  │      │  Claude Code CLI  │   │
    │  │      │  / Codex CLI      │   │
    │  │  ┌───▼──────────────┐    │   │
    │  │  │  Workspace PVC   │    │   │
    │  └──┴──────────────────┴────┘   │
    └───────────────────────────────┘ │
                                       │
    ┌──────────────────────────────────┘
    │  Autonomous Agents
    │  ┌──────┐   ┌──────┐
    │  │ Tyr  │   │ Ravn │
    │  │(saga)│   │(agent│
    │  └──────┘   │frame)│
    │             └──────┘
    │
    │  Supporting Services
    │  ┌──────────┐ ┌────────┐ ┌───────────┐
    │  │ Bifröst  │ │  Mimir │ │ Sleipnir  │
    │  │(LLM gw)  │ │(memory)│ │(transport)│
    └──┴──────────┴─┴────────┴─┴───────────┘
```

| Component | Role |
|-----------|------|
| **Volundr** | Core API — session lifecycle, workspace provisioning, git workflows, multi-tenancy, event pipeline |
| **Skuld** | WebSocket broker connecting the browser to AI coding agents inside session pods |
| **Web UI** | React frontend — session management, chronicles, diffs, terminal access, admin |
| **Tyr** | Saga coordinator — decomposes GitHub/Linear issues into typed tasks and dispatches autonomous coding agents |
| **Ravn** | Agent framework — long-running AI agents with personas, memory, wakefulness triggers, and dream cycles |
| **Bifröst** | Multi-provider LLM gateway — OpenAI-compatible API with failover, cost-optimised and latency-optimised routing |
| **Mimir** | Knowledge and context system — structured memory for agents, thread enrichment, and RAG over project history |
| **Sleipnir** | Transport abstraction — NATS, NNG, RabbitMQ, and subprocess adapters behind a single event backbone |
| **niuu CLI** | Unified CLI and TUI for managing local and remote services |

Chat traffic flows directly from the browser to Skuld inside the session pod — Volundr is never in the chat data path.

## Features

### Sessions & Workspaces

- **Sessions** — create, start, stop, and archive AI coding sessions with model selection and preset configuration
- **Workspaces** — per-session PVC provisioning with user home volumes and storage quotas
- **Templates** — config-driven workspace blueprints (repos, setup scripts, runtime settings)
- **Presets** — portable runtime configs (model, MCP servers, resources, env vars) stored in the database
- **Profiles** — read-only workload configurations loaded from YAML or Kubernetes CRDs
- **Chronicles** — session history snapshots with timelines, file diffs, commit summaries, and reforge chains

### AI Agents

- **Tyr saga dispatch** — decomposes issues from GitHub or Linear into typed tasks (feat, fix, refactor, test) and spawns coding agents for each
- **Raid planning** — multi-agent coordination where sub-agents work on decomposed tasks in parallel
- **Ravn personas** — configurable agent identities with tone, expertise, and behaviour profiles
- **Dream cycles** — background reflection and knowledge consolidation for long-running Ravn agents
- **Wakefulness triggers** — schedule or event-driven agent activation

### LLM Routing (Bifröst)

- OpenAI-compatible API (`POST /v1/chat/completions`, `GET /v1/models`) across Anthropic, OpenAI, and Ollama
- Routing strategies: failover, cost-optimised, round-robin, latency-optimised
- Model aliases (`fast`, `balanced`, `best`) resolved from config
- Usage logging to SQLite; optional authentication via PAT or open mode
- Pi mode: run fully offline via Ollama on a Raspberry Pi or any low-power device

### Git & Issue Tracking

- **Git workflows** — branch creation, PR management, CI status checks, merge confidence scoring
- **GitHub and GitLab** — pluggable provider adapters via dynamic loading
- **Issue tracking** — Jira and Linear integration with repo-to-project mappings

### Platform

- **Multi-tenancy** — hierarchical tenant tree with roles (admin, developer, viewer) and quota enforcement
- **Identity** — IDP-agnostic OIDC authentication (Keycloak, Entra ID, Okta) with JIT user provisioning via Envoy
- **Authorization** — pluggable policy engine (Cerbos, simple role-based, or allow-all for dev)
- **Secret injection** — CSI-based mounting via Infisical, OpenBao/Vault; Volundr never reads secret values
- **Credential management** — pluggable credential stores (Vault, Infisical, memory) for API keys, OAuth tokens, SSH keys
- **Event pipeline** — session events dispatched to PostgreSQL, RabbitMQ, and/or OpenTelemetry sinks
- **MCP servers** — configurable Model Context Protocol servers injected into sessions
- **Saved prompts** — reusable prompts scoped globally or per-project
- **SSE streaming** — real-time session state and stats updates

## Quick Start

```bash
# Install dependencies
uv sync --all-extras --dev

# Copy and edit configs
cp config.yaml.example config.yaml
cp bifrost.yaml.example bifrost.yaml    # optional: LLM gateway
cp tyr.yaml.example tyr.yaml            # optional: saga coordinator

# Start the Volundr API
uv run volundr

# Or with auto-reload
uv run uvicorn volundr.main:app --reload --port 8080

# Start other services (each in its own terminal)
uv run bifrost --config bifrost.yaml
uv run tyr --config tyr.yaml
```

The Volundr API serves at `http://localhost:8080`. Interactive docs at `/docs`.

### Bifröst — Pi mode (offline)

Run the LLM gateway entirely without cloud APIs using [Ollama](https://ollama.com):

```bash
# Pull models
ollama pull llama3.2:1b      # fast   — 1.3 GB
ollama pull llama3.2:3b      # balanced — 2.0 GB
ollama pull llama3.1:8b      # best   — 4.7 GB (Pi 5 + 8 GB RAM recommended)

# Start gateway with Pi-mode config
bifrost --config bifrost.pi.example.yaml
```

The gateway starts at `http://localhost:8088`. See `bifrost.pi.example.yaml` for the full annotated config.

## Configuration

Each service loads config from YAML with environment variable overrides using `__` for nesting:

| Service | Config file |
|---------|------------|
| Volundr | `config.yaml` or `/etc/volundr/config.yaml` |
| Bifröst | `bifrost.yaml` |
| Tyr | `tyr.yaml` |
| Ravn | `ravn.yaml` |

```bash
# Volundr environment overrides
DATABASE__HOST=postgres.local
DATABASE__PASSWORD=secret
GIT__GITHUB__TOKEN=ghp_xxxx
EVENT_PIPELINE__OTEL__ENABLED=true
```

See the [configuration reference](https://niuulabs.github.io/volundr/configuration/) for all options.

## Testing

```bash
# Backend (85% coverage enforced)
uv run pytest tests/ -v

# Web UI (85% coverage enforced)
cd web && npm run test:coverage

# Lint
uv run ruff check src/ tests/
```

## Deployment

Each component has its own Helm chart under `charts/`:

```bash
# Core platform
helm install volundr ./charts/volundr -n niuu \
  --set database.external.host=postgres.svc.cluster.local \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=niuu.example.com

# LLM gateway
helm install bifrost ./charts/bifrost -n niuu

# Saga coordinator
helm install tyr ./charts/tyr -n niuu
```

See the [deployment guide](https://niuulabs.github.io/volundr/deployment/) for Helm values, migrations, and production setup.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI, Uvicorn, Pydantic |
| Database | PostgreSQL via asyncpg (raw SQL, no ORM) |
| Web UI | React 18, Vite, CSS Modules, Zustand |
| Broker | FastAPI WebSockets |
| Transport | NNG (pynng), NATS, RabbitMQ via Sleipnir |
| Orchestration | Kubernetes, Helm |
| Auth | OIDC/OAuth2, Envoy, Cerbos |
| Secrets | OpenBao/Vault, Infisical, CSI driver |
| Observability | OpenTelemetry (traces + metrics) |
| Events | RabbitMQ (optional), SSE |
| Git | GitHub API, GitLab API |
| LLM | Anthropic, OpenAI, Ollama (via Bifröst) |

## Optional Dependencies

```bash
uv sync --extra rabbitmq   # RabbitMQ event sink
uv sync --extra k8s        # Kubernetes client
uv sync --extra otel       # OpenTelemetry export
uv sync --extra nats       # NATS transport
uv sync --extra tui        # Terminal UI (niuu CLI)
```

## Documentation

Full documentation at [niuulabs.github.io/volundr](https://niuulabs.github.io/volundr/).

## License

Apache 2.0 — see [LICENSE](LICENSE).
