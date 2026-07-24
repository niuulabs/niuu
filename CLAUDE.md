# Volundr / Niuu platform

Monorepo for the Niuu agent platform. The detailed, binding conventions live in
`.claude/rules/*.md` — read those before changing code. This file is the map.

## Packages (`src/`)

| Package | Role |
|---|---|
| `volundr` | Forge backend — session lifecycle, workspaces, chronicles, REST API (`/api/v1/forge`) |
| `skuld` | Runtime session gateway — runs Codex/Claude/Ravn sessions and adapts shared collaboration to channels/WebSockets |
| `niuu` | Shared platform libraries and host/gateway — collaboration/mesh mechanics, plugin APIs, registries, aggregate routing |
| `ting` | Autonomous dispatcher — sagas, runs, tracker integration (must never import `volundr`) |
| `ravn` | Agent runtime — wraps models/runtimes and owns judgment, learning, tools, A2A, and resident autonomy when enabled |
| `bifrost` | Model gateway and catalog |
| `sleipnir` | Event bus / event-type registry |
| `cli` | `niuu` CLI — `niuu platform up` runs the whole stack in mini mode |

Everything follows hexagonal architecture: `domain/` (models + services) →
`ports/` (interfaces) → `adapters/` (implementations); composition happens in
each package's `main.py`. See `.claude/rules/architecture.md` and
`.claude/rules/module-boundaries.md`.

## Dev commands

```bash
./start-dev                       # full local stack on :8080 (mini mode, embedded postgres)
./stop-dev
make verify                       # ruff lint + full backend test suite
uv run --extra dev pytest -q      # backend tests directly
cd web-next && pnpm test          # web tests (coverage-gated)
```

## Key rules (the short version)

- Migrations go in BOTH `migrations/` and the Helm configmap — `.claude/rules/migrations.md`
- Raw SQL with asyncpg, no ORM — `.claude/rules/database.md`
- 85% coverage gates on backend and web; never lower them — `.claude/rules/testing.md`
- Conventional commits — `.claude/rules/commits.md`
- New adapters use dynamic `adapter:` + kwargs config — `.claude/rules/dynamic-adapters.md`
- Preserve the Ravn/Niuu ownership and communication boundaries — `.claude/rules/ravn-niuu-boundary.md`
- No placeholders or incomplete implementations outside tests — `.claude/rules/implementation-completeness.md`
- `web-next/` is Tailwind + tokens and has its own `web-next/CLAUDE.md`; legacy `web/` rules differ

## Docs

- `docs/openclaw-session-orchestrator-guide.md` — how an AI controller drives
  Forge sessions end to end (API contracts, SSE, WebSocket, event-log replay)
- `docs/operator/` — operator-facing feature guides
