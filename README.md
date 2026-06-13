# niuu

[![CI](https://github.com/niuulabs/volundr/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/niuulabs/volundr/actions/workflows/ci.yaml)
[![Release](https://github.com/niuulabs/volundr/actions/workflows/release.yaml/badge.svg)](https://github.com/niuulabs/volundr/actions/workflows/release.yaml)
[![Secret Scan](https://github.com/niuulabs/volundr/actions/workflows/secrets.yaml/badge.svg?branch=main)](https://github.com/niuulabs/volundr/actions/workflows/secrets.yaml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/niuulabs/volundr/badge)](https://scorecard.dev/viewer/?uri=github.com/niuulabs/volundr)
[![Coverage](https://codecov.io/gh/niuulabs/volundr/branch/main/graph/badge.svg)](https://codecov.io/gh/niuulabs/volundr)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

The self-hosted platform for AI workspaces, custom AI teams, always-on assistants, shared knowledge, and local or cloud AI.

<p align="center">
  <img src="docs/site/images/ui-ting-workflows.png" alt="Ting workflow builder in Niuu" width="960">
</p>

<p align="center">
  <img src="docs/site/images/ui-guild-instances.png" alt="Guild instance registry" width="49%">
  <img src="docs/site/images/ui-niuu-home.png" alt="Volundr forge dashboard" width="49%">
</p>

## What Niuu Is

Niuu brings four operating modes into one platform you can run on your own machine, on Kubernetes, or inside your own infrastructure:

- **AI workspaces** for hands-on work with one assistant or several assistants working together in a live coding environment
- **Custom AI teams** you can design, launch, and steer for coding, research, operations, approvals, and your own multi-step flows
- **Always-on assistants** that monitor sources, revisit knowledge, refresh documents, and stay available for live operator guidance
- **Local and cloud AI** managed in one place, including what models are available, where they run, and when they are used

The result is a self-hosted system where you can move smoothly between direct operator work, autonomous execution, durable memory, and local or third-party models without handing control to an external vendor platform.

## Platform Map

```
                         Users
                    ┌───────────┐
                    │ Niuu Web  │  React/Vite, OIDC auth
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
    │ Volundr / Shared   │            │
    │ platform APIs      │            │
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
    │  Coordination & Runtime
    │  ┌──────┐  ┌──────┐  ┌──────┐
    │  │ Ting │  │ Ravn │  │Guild │
    │  │flows │  │agents│  │regis.│
    │  └──────┘  └──────┘  └──────┘
    │
    │  Supporting Services
    │  ┌──────────┐ ┌────────┐ ┌────────────┐ ┌─────────────┐
    │  │ Bifröst  │ │ Mimir  │ │ Observatory│ │  Sleipnir   │
    │  │(LLM gw)  │ │(memory)│ │ (ops view) │ │ (transport) │
    └──┴──────────┴─┴────────┴─┴────────────┴─┴─────────────┘
```

| Component | Role |
|-----------|------|
| **Niuu Web** | Operator interface for sessions, workflows, registered instances, memory, assistants, and platform settings |
| **Volundr** | Live AI workspaces, session lifecycle, workspace provisioning, chronicles, git workflows, and direct operator pairing |
| **Skuld** | Live broker inside session pods connecting the browser to coding agents, tools, terminals, and workflow runtime events |
| **Ting** | Workflow and coordination layer for teams, review loops, staged execution, and launchable flows |
| **Ravn** | Assistant runtime and persona harness for one assistant or a connected team, with tools, wakefulness, and long-lived behaviors |
| **Guild** | Shared registry for platform instances and targets, so Niuu can discover and use Volundr, Ting, Mimir, and other services across environments |
| **Mimir** | Shared knowledge and memory system for durable documentation, ingest, research artifacts, curation, and assistant recall |
| **Bifröst** | Local and cloud model gateway that decides what models are available, where they run, and how callers route between them |
| **Observatory** | Platform topology and operations view for health, discovery, and event visibility across the running system |
| **Sleipnir** | Transport abstraction for events and messaging across NATS, NNG, RabbitMQ, subprocesses, and other backbones |
| **niuu CLI** | Unified CLI and TUI for managing local and remote services |

Chat traffic flows directly from the browser to Skuld inside the session pod — Volundr is never in the chat data path.

## What Niuu Lets You Build

- **Live AI workspaces** with repos, terminals, diffs, and direct conversation with one assistant or several assistants working together
- **Custom AI teams** for coding, review, security, research, approvals, retries, and your own staged workflows
- **Shared knowledge systems** where research, chronicles, postmortems, curated memory, and Warden-maintained docs accumulate
- **Long-lived assistants** that continue working after the interactive session ends by watching sources, refreshing documents, and staying reachable for operator guidance
- **Local and cloud model operations** where you decide what models are available, whether work stays local or uses third-party providers, and how the rest of the platform can use them

## Features

### Sessions & Workspaces

- **Sessions** — create, start, stop, and archive AI coding sessions with model selection and preset configuration
- **Workspaces** — per-session PVC provisioning with user home volumes and storage quotas
- **Templates** — config-driven workspace blueprints (repos, setup scripts, runtime settings)
- **Presets** — portable runtime configs (model, MCP servers, resources, env vars) stored in the database
- **Profiles** — read-only workload configurations loaded from YAML or Kubernetes CRDs
- **Chronicles** — session history snapshots with timelines, file diffs, commit summaries, and reforge chains

### AI Agents

- **Ting saga dispatch** — decomposes issues from GitHub or Linear into typed tasks (feat, fix, refactor, test) and spawns coding agents for each
- **Run planning** — multi-agent coordination where sub-agents work on decomposed tasks in parallel
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

# Start the local platform stack
./start-dev

# Stop it again
./stop-dev
```

The local Niuu stack serves at `http://localhost:8080`. Interactive API docs are available under `/docs` for the relevant services.

If you want to run pieces manually instead of the dev stack, you can still start the individual services directly from their config files, but `./start-dev` and `./stop-dev` are the normal way to bring the platform up locally.

### Local and Deployment Modes

`./start-dev` runs the full local platform host in one process. In this local embedded Forge mode, Guild owns the public `/api/v1/forge` route and registers a system `Local Forge` instance backed by the in-process Volundr app. Guild dispatches to that local target through `httpx.ASGITransport`, so requests keep normal HTTP semantics but do not cross the network or call `localhost`.

Standalone Forge means the Volundr service is running without Guild as the front-door aggregator. In that shape, the Volundr app itself serves `/api/v1/forge`; this is what the standalone `charts/volundr` deployment does. In the umbrella `charts/niuu` deployment, the logical `forge-api` ingress backend resolves to Guild when `guild.enabled=true`, and to Volundr when `guild.enabled=false`.

The route ownership rule is: Guild owns `/api/v1/forge` in aggregate or local embedded mode; Volundr owns `/api/v1/forge` only when it is the standalone Forge service.

## Configuration

Each service loads config from YAML with environment variable overrides using `__` for nesting:

| Service | Config file |
|---------|------------|
| Volundr | `config.yaml` or `/etc/volundr/config.yaml` |
| Bifröst | `bifrost.yaml` |
| Ting | `ting.yaml` |
| Ravn | `ravn.yaml` |

```bash
# Volundr environment overrides
DATABASE__HOST=postgres.local
DATABASE__PASSWORD=secret
GIT__GITHUB__TOKEN=ghp_xxxx
EVENT_PIPELINE__OTEL__ENABLED=true
```

See the [configuration reference](https://niuulabs.github.io/volundr/reference/configuration/) for all options.

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

The main install surface is the `niuu` umbrella chart under `charts/`, which deploys the platform components together:

```bash
# Full Niuu platform
helm install niuu ./charts/niuu -n niuu \
  --set database.external.host=postgres.svc.cluster.local \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=niuu.example.com

# Or upgrade an existing release
helm upgrade niuu ./charts/niuu -n niuu
```

You can still deploy individual component charts when you need to, but the default platform deployment path should be the `niuu` chart.

See the [deployment guide](https://niuulabs.github.io/volundr/operations/kubernetes-deployment/) for Helm values, migrations, and production setup.

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
