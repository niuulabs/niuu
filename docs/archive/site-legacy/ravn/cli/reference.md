# CLI Reference

Ravn provides seven top-level commands and two subcommand groups.

## `ravn run`

Interactive agent session or single-prompt execution.

```
ravn run [PROMPT]
```

| Flag | Short | Description |
|------|-------|-------------|
| `--no-tools` | | Disable all tool execution |
| `--show-usage` | | Print token usage after each turn |
| `--config` | `-c` | Path to ravn config YAML |
| `--persona` | `-p` | Persona name (built-in or `~/.ravn/personas/`) |
| `--profile` | | Profile name (built-in or `~/.ravn/profiles/`) |
| `--resume` | `-r` | Resume interrupted task by `task_id` |

When `PROMPT` is omitted, Ravn enters an interactive REPL. When provided,
Ravn executes the prompt and exits.

**Examples:**

```bash
# Interactive REPL
ravn run

# Single prompt
ravn run "explain the auth middleware in this repo"

# With persona and thinking
ravn run -p autonomous-agent "refactor the payment module"

# Resume an interrupted task
ravn run -r task_abc123
```

---

## `ravn resume`

Resume from a crash-recovery checkpoint or named snapshot.

```
ravn resume TASK_ID
```

| Flag | Short | Description |
|------|-------|-------------|
| `--checkpoint` | `-c` | Specific `checkpoint_id` to restore |
| `--config` | | Path to ravn config YAML |
| `--show-usage` | | Print token usage after each turn |

**Examples:**

```bash
# Resume latest checkpoint for a task
ravn resume task_abc123

# Resume from a specific snapshot
ravn resume task_abc123 -c ckpt_task_abc123_3
```

---

## `ravn daemon`

Long-lived service combining gateway channels with the initiative (drive loop)
engine. Runs until `SIGINT` or `SIGTERM`.

```
ravn daemon
```

| Flag | Short | Description |
|------|-------|-------------|
| `--config` | `-c` | Path to ravn config YAML |
| `--persona` | `-p` | Default persona for dispatched tasks |
| `--profile` | | Profile name |
| `--resume` | | Resume unfinished tasks from journal |

The daemon:

1. Starts all configured gateway channels (HTTP, Telegram, etc.)
2. Runs the initiative engine (cron triggers, event triggers)
3. Processes task queue with configurable concurrency
4. Publishes events to Sleipnir (if configured)

**Examples:**

```bash
# Start daemon with Telegram + drive loop
ravn daemon -c ravn.yaml

# Resume interrupted tasks from journal
ravn daemon --resume
```

---

## `ravn gateway`

Pi-mode gateway — HTTP + messaging channels without the drive loop.

```
ravn gateway
```

| Flag | Short | Description |
|------|-------|-------------|
| `--telegram` | | Enable Telegram polling channel |
| `--http` | | Enable local HTTP channel |
| `--config` | `-c` | Path to ravn config YAML |
| `--persona` | `-p` | Persona for all gateway sessions |
| `--profile` | | Profile name |

Each incoming message creates a per-session agent instance. The gateway
manages session lifecycles and routes responses back to the originating channel.

**Examples:**

```bash
# Pi-mode: Telegram + local HTTP
ravn gateway --telegram --http

# HTTP-only gateway
ravn gateway --http -p coding-agent
```

---

## `ravn listen`

Subscribe to Sleipnir (RabbitMQ) for remote task dispatch. Executes
tasks autonomously as they arrive.

```
ravn listen
```

| Flag | Short | Description |
|------|-------|-------------|
| `--config` | `-c` | Path to ravn config YAML |
| `--persona` | `-p` | Default persona for dispatched tasks |
| `--profile` | | Profile name |

Listens on `ravn.task.dispatch` routing key. Tasks with unknown personas
are rejected with `ravn.task.rejected`.

**Examples:**

```bash
ravn listen -c ravn.yaml -p autonomous-agent
```

---

## `ravn evolve`

Run the self-improvement pattern extraction pass. Analyzes accumulated
task outcomes and episodic memory, then prints suggested improvements.

```
ravn evolve
```

| Flag | Short | Description |
|------|-------|-------------|
| `--config` | | Path to ravn config YAML |

Output includes:

- **Skill suggestions** — recurring tool sequences across successful episodes
- **System warnings** — systematic error patterns
- **Strategy injections** — domain-specific success patterns

No automatic modifications are made. Review the output and apply manually.

---

## `ravn peers`

List verified flock members (peer Ravn instances).

```
ravn peers
```

| Flag | Short | Description |
|------|-------|-------------|
| `--config` | `-c` | Path to ravn config YAML |
| `--verbose` | `-v` | Show address, latency, task count, last seen |
| `--scan` | | Force fresh mDNS/K8s scan |

**Examples:**

```bash
# Quick peer list
ravn peers

# Detailed peer info with fresh scan
ravn peers -v --scan
```

---

## `ravn tui`

Terminal user interface for managing distributed Ravn clusters. Requires
the `tui` extra: `pip install ravn[tui]`.

```
ravn tui
```

| Flag | Short | Description |
|------|-------|-------------|
| `--connect` | `-C` | Connect to Ravn daemon at `HOST:PORT` (repeatable) |
| `--discover` | | Auto-discover peers via mDNS |
| `--layout` | `-l` | Layout preset: `flokk`, `cascade`, `mimir`, `compare`, `broadcast` |
| `--config` | `-c` | Path to ravn config YAML |

**Layout presets:**

| Layout | Purpose |
|--------|---------|
| `flokk` | Overview of all peers in the cluster |
| `cascade` | Coordinator + sub-agent task tree |
| `mimir` | Knowledge base browser |
| `compare` | Side-by-side agent comparison |
| `broadcast` | Send prompt to all connected agents |

---

## Subcommands

### `ravn approvals`

Manage per-project bash command approval patterns. Only relevant when
`permission.mode` is `prompt`.

#### `ravn approvals list`

List all stored approval patterns for the current project.

```bash
ravn approvals list
```

#### `ravn approvals revoke`

Revoke an approval pattern so the command is prompted again.

```bash
ravn approvals revoke "npm test"
```

---

### `ravn mimir`

Knowledge base CLI utilities.

#### `ravn mimir ingest`

Ingest a file or stdin into Mímir.

```
ravn mimir ingest PATH
```

| Flag | Short | Description |
|------|-------|-------------|
| `--title` | `-t` | Title override (defaults to filename) |
| `--type` | | Source type: `document`, `web`, `research`, `conversation`, `tool_output` |
| `--url` | `-u` | Original URL (metadata) |
| `--mimir` | `-m` | Named instance (e.g., `local`, `shared`) |
| `--config` | `-c` | Path to ravn config YAML |

**Examples:**

```bash
# Ingest a document
ravn mimir ingest ./architecture.md -t "System Architecture"

# Ingest from URL metadata
ravn mimir ingest ./page.html --type web -u "https://example.com/page"

# Ingest into shared instance
ravn mimir ingest ./runbook.md -m shared
```
