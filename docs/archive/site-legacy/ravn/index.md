# Ravn Documentation

Ravn is an autonomous AI agent framework built on Claude. It provides a rich CLI,
persistent memory, knowledge management, distributed coordination, and a deep
configuration surface — all designed for both interactive and unattended operation.

## Documentation Overview

### Phase 1 — Core

Everything a new user needs to get started and understand the basic surface area.

| Section | Description |
|---------|-------------|
| [Getting Started](getting-started/quick-start.md) | Installation, first run, config discovery |
| [Configuration Reference](configuration/reference.md) | Every config section with defaults, types, examples |
| [CLI Reference](cli/reference.md) | All commands, flags, and subcommands |
| [Tool Reference](tools/reference.md) | Built-in tools by group with permissions |
| [Personas](personas/reference.md) | Built-in and custom personas, RAVN.md overlay |

### Phase 2 — Platform Services

Services that extend Ravn beyond a single-session agent.

| Section | Description |
|---------|-------------|
| [Bifrost — LLM Proxy](platform/bifrost.md) | Model aliases, routing, cost tracking, budgets |
| [Mímir — Knowledge Base](platform/mimir.md) | Persistent wiki, ingestion, search, lint |
| [MCP Integration](platform/mcp.md) | Model Context Protocol servers, auth, transports |

### Phase 3 — Advanced Agent Features

Subsystems for distributed execution, memory, and self-improvement.

| Section | Description |
|---------|-------------|
| [Cascade & Parallel Execution](advanced/cascade.md) | Coordinator, sub-agents, task tools |
| [Flock / Mesh Networking](advanced/flock.md) | Mesh transports, peer discovery |
| [Drive Loop & Triggers](advanced/drive-loop.md) | Initiative engine, cron, events |
| [Memory & Knowledge Systems](advanced/memory.md) | Episodic memory, Búri fact graph, outcomes |
| [Skill System](advanced/skills.md) | Automatic skill extraction, storage, execution |
| [Context Management](advanced/context.md) | Compression, prompt builder, caching |
| [Extended Thinking](advanced/thinking.md) | Claude thinking mode, budget, auto-trigger |

### Phase 4 — Operations & Integration

Deployment, security, and integration with external systems.

| Section | Description |
|---------|-------------|
| [Sleipnir Event Backbone](operations/sleipnir.md) | RabbitMQ setup, exchange topology, events |
| [Gateway Channels](operations/gateway.md) | HTTP, Telegram, Discord, Slack, Matrix, WebSocket |
| [Security & Permissions](operations/security.md) | Permission modes, rules, approval memory, PATs |
| [Hooks](operations/hooks.md) | Pre/post-tool lifecycle, custom hooks |
| [Self-Improvement / Evolution](operations/evolution.md) | Pattern extraction, skill suggestion, strategy |
| [Deployment](operations/deployment.md) | Pi mode, Kubernetes, Docker, config overlays |
| [Checkpointing & Resume](operations/checkpointing.md) | Crash recovery, snapshots, `--resume` |

### Phase 5 — Developer Reference

For contributors building new tools, providers, or channels.

| Section | Description |
|---------|-------------|
| [Architecture](developer/architecture.md) | Hexagonal design, ports, adapters, modules |
| [Adding a New Tool](developer/new-tool.md) | Implement ToolPort, register, configure |
| [Adding a New LLM Provider](developer/new-provider.md) | Implement LLMPort, streaming, fallback |
| [Adding a New Channel](developer/new-channel.md) | Implement ChannelPort, event translation |
