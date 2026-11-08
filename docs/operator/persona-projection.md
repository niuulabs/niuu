# Personas on harness sessions

A persona says what a role is. A session plays it on whichever harness the node runs (Claude
Code, Codex, OpenCode, Grok, Pi, dsh, Muse). Forge pins the persona at launch and Skuld
projects it onto the harness **on every start, resume included**. What the harness cannot
enforce, the session refuses to start with; nothing is dropped silently.

## What a persona names

`allowed_tools`, `forbidden_tools` and `permission_mode`. Tools come from one closed
vocabulary (`personas.domain.tool_vocabulary`). The persona loader, the persona API and the
portable document all refuse a name outside it and list the valid set.

| Tool | Meaning |
|---|---|
| `file_read` | read, list and search files |
| `file_write` | create and edit files |
| `shell` | run any command (includes `git`, `git_read` and every command below) |
| `git_read` | status, diff, log, show |
| `git` | git including commands that change the repository |
| `web_fetch`, `web_search` | fetch a URL, search the web |
| `plan` | keep a task list (never a restriction: every harness has one) |
| `present_file` | the `present-file` command |
| `tracker` | the `tracker_issue` command, or an MCP server named `tracker` |
| `mimir`, `mimir:<tool>` | Mímir, whole or one tool (`search`, `read`, `write`, `ingest`, ...) |
| `mcp:<server>`, `mcp:<server>:<tool>` | an MCP server of the session |

A non-empty `allowed_tools` is a closed world: only those tools. `forbidden_tools` wins over
`allowed_tools`. `permission_mode` is `read_only`, `workspace_write`, `full_access`, `prompt`
or `deny_all` (hyphens are accepted too; digests follow the spelling a persona was authored
with, so pins of saved personas stay valid).

Forbidding something a granted `shell` already contains (`git` while `shell` is granted) is a
restriction no harness can enforce, so it is refused.

## How a persona and a request prompt combine

The persona is the base. A request's ad-hoc `system_prompt` is appended after the persona's
instructions and never replaces them. Forge sends `session.persona` (the pinned document) and
`session.systemPrompt` (the request) separately; Skuld composes them.

## What each harness enforces

| Harness | Instructions | Tools | Permission mode |
|---|---|---|---|
| Claude (Agent SDK, CLI, persistent, sdk-websocket, tmux) | appended to Claude's own prompt, resent on resume | built-in tools offered (`--tools`), exact rules pre-approved, everything else denied (`dontAsk`), only the granted MCP servers; a persona with no built-in tools uses the MCP-only boundary and waits for its servers | `read_only` removes write tools and `git`; `workspace_write` is stated, not enforced (the session runtime is the boundary); `prompt` refused |
| Codex app-server | `developerInstructions`, on start and resume | shell off unless granted, web search off unless granted, MCP servers isolated to the granted ones | OS sandbox `read-only` / `workspace-write` with approvals `never`; a persona without `file_write` runs `read-only`; the node's own sandbox is only ever narrowed; `prompt`, `deny_all` refused |
| Codex exec | `developer_instructions` on every invocation | shell and web as above; cannot isolate MCP servers, so a closed persona is refused | as above |
| OpenCode, Pi, dsh | appended | none: any tool list refused | `read_only`, `prompt`, `deny_all` refused |
| Grok | sent as ACP `systemPrompt` (unverified, stated) | none | as above |
| Muse, Claude/Codex Remote Control | none: a persona with instructions is refused | none | as above |

Codex reads files and runs git only through its shell tool, so a persona that grants
`file_read` or `git` and forbids `shell` is refused on Codex rather than widened.

## Where to look

`persona.json` in the session's per-start runtime dir records the persona and what was made
of it (the boundary handed to the transport, and the gaps). `/ready` carries the same summary
under `persona`, or the refusal under `error`.

## After editing a built-in persona

Bundled workflows pin their personas by digest. Run
`uv run python scripts/repin_bundled_workflows.py` to re-pin them (and bump each changed
workflow's `version` and its entry in `tests/test_ting/data/bundled_workflow_versions.lock.json`).
