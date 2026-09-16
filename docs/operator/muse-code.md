# Meta Muse Code sessions (`skuldMuse`)

Forge can run coding sessions on **Meta Muse Code**, Meta's terminal coding agent, through
Skuld's `MuseMSPTransport`. The broker spawns one long-lived `muse serve` host per session and
drives it over the **Muse Session Protocol (MSP)** — the open, JSON-RPC-2.0-over-stdio protocol
Meta publishes for programmatic control of Muse Code. Nothing in this path is scraped from a
TUI: sessions, turns, streaming items, approvals, questions and token usage are all typed wire
messages, and sessions are durable and resumable on the host.

| | |
|---|---|
| Session definition | `skuldMuse` (display name "Meta Muse Code") |
| Transport adapter | `skuld.transports.muse.MuseMSPTransport` (`cliType: muse`) |
| Default model | `muse-spark-1.3` (catalog also lists `muse-spark-1.2`, `muse-spark-1.3-contributor`) |
| Provider vendor | `meta` |
| Steering | native — a mid-turn message joins the running turn (`turn/start ifBusy: steer`) |
| Interrupt | `turn/interrupt`; the turn ends with terminal `cancelled` |
| Resume | `session/resume` by the host's session id (durable log under `~/.local/share/muse`) |
| Questions | `userInput/request` surfaces as the same `ask_user_question` card as Claude |
| Permissions | auto-approved when `skipPermissions` is on; otherwise a `control_request` gate |

## Install and authenticate the CLI on the Skuld host

```bash
curl -fsSL https://dev.meta.ai/install.sh | bash     # installs ~/.local/bin/muse (launcher + binary)
muse --version                                         # Muse Code 1.0.2 or newer
```

Skuld and OpenShell images include checksum-pinned Muse Code `1.3.0-R3233.1` for
Linux amd64 and arm64. Docker, Kubernetes and SSH VM sessions use the Skuld image;
OpenShell-backed sessions use the OpenShell image. Rebuild those images for this release.
Mini-mode hosts still need the installation above.

The transport uses `skuld.muse_bin` (`SKULD__MUSE_BIN`), then `PATH`, then `muse`.
Session definitions and Helm use `broker.museBin` for an explicit executable path.

MSP currently has no system-prompt field. Muse rejects configured system prompts,
including generated Project briefings, instead of silently dropping them. Select another
runtime for those sessions, or configure Muse instructions in workspace `AGENTS.md`.
A failed native resume is an error; it never replaces the session with a fresh one.

Credentials resolve in the host's own order: `META_API_KEY` in the broker's environment wins,
then a key stored with `printf '%s' "$KEY" | muse auth set --api-key-stdin`, then a browser
login (`muse login`, device-code flow — works over SSH). Keys come from the Meta Model API
dashboard at <https://dev.meta.ai/> (pay-as-you-go, same rates as the API). **A host with no
credential still serves**: every turn ends `failed` with `modelError: not logged in`, which the
transport surfaces as an error result in the transcript instead of wedging the session — so a
missing key is visible, not silent.

For the local dev stack, export `META_API_KEY` in the shell that runs `./start-dev` (the broker
inherits it). It is not read from `config.yaml`.

## Enabling it

`skuldMuse` is a built-in session definition, so it is available as soon as the code is deployed.
Create a session with it like any other engine:

```json
{ "name": "muse-try", "model": "muse-spark-1.3", "definition": "skuldMuse",
  "source_path": "/home/thor/repos/niuu", "prompt": "Run the test suite and fix what fails." }
```

The Bifrost catalog carries the Muse Spark rows with `session_definition: skuldMuse`, so the
model pickers (web, iOS) list them under the Muse engine and a model-only launch resolves to the
right runtime.

## Knobs (transport kwargs, set through the session definition's `broker` defaults)

| Kwarg | Default | Meaning |
|---|---|---|
| `skip_permissions` | broker `skipPermissions` | `true` → session approval mode `allowAll`; `false` → `onRequest` and every approval the host escalates is shown as a permission gate |
| `approval_mode` | derived | Explicit MSP mode: `allowAll`, `onRequest`, `promptUnmatched`, `denyUnmatched` (aliases `yolo`, `default`, `untrusted`, `plan` accepted) |
| `reasoning_effort` | host default (`high`) | `minimal` … `ultra` (`max` maps to `ultra`); sent on every `turn/start` |
| `disable_sandbox` | `true` | Muse's OS sandbox (bubblewrap on Linux) fails closed in containers without the capabilities to build it; Skuld already isolates the workspace pod |
| `sandbox_network` | unset | Only when the sandbox is on: `proxy-only`, `restricted`, `enabled` |
| `trust_workspace` | `true` | Load the repo's `AGENTS.md`/`CLAUDE.md` rules and skills |
| `acp_prompt_timeout_s` | `300` | Turn watchdog: past it the turn is interrupted and, if the host stays silent, closed with a `timeout` result |
| `resume_session_id` | Volundr `resume_session_id` | Resume this Muse session id instead of starting a new one |

## What the transcript shows

Muse view-stream items are folded into the shared Claude-style frames, so every UI surface works
unchanged: `agentMessage` deltas stream as text, `reasoning` as thinking, `toolCall` items become
paired `tool_use` / `tool_result` blocks (with per-tool timing) under the cross-engine names
(`shell` → Bash, `write_file` → Write, `edit_file` → Edit, `read_file` → Read, …), subagents
become `Task` calls, the todo list drives the plan dock, and `turn/completed` becomes the
`result` frame carrying the host's real token usage (input, cached, output, reasoning) priced at
the Muse Spark list rates.

## Troubleshooting

- **`not logged in: run /login to add an API key`** in a turn's result — no credential reached
  the host. Export `META_API_KEY` for the broker or store a key with `muse auth set`.
- **Nothing happens for a long time, then a `timeout` result** — the host stopped answering;
  the watchdog interrupted the turn. Check the broker log for `muse-stderr` lines.
- **`serve` argument errors at spawn** — host flags must follow the verb (`muse serve
  --disable-sandbox`); the transport always orders them that way, so this indicates an
  unexpected `MUSE_BIN` wrapper.
- **The host warns that the model is not in its catalog** — Muse Code 1.0.x accepts any id and
  lets the provider decide at the first model call; a wrong id fails the turn with a typed
  `modelError`, visible in the transcript.
- **Protocol reference** — `muse schema generate-json-schema --out DIR` exports the exact wire
  schema of the installed binary; the SDK, docs and conformance transcripts live at
  <https://github.com/meta-models/muse-code-sdk> (MIT).
