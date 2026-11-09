# Personas on harness sessions

A persona says what a role is. A session plays it on whichever harness the node runs (Claude
Code, Codex, OpenCode, Grok, Pi, dsh, Muse). Forge pins the persona at launch and Skuld
projects it onto the harness **on every start, resume included**. What the harness cannot
enforce, the session refuses to start with; nothing is dropped silently.

## What a persona names

Its instructions and a `permission_mode`. `permission_mode` is `read_only`,
`workspace_write`, `full_access`, `prompt` or `deny_all` (hyphens are accepted too; digests
follow the spelling a persona was authored with, so pins of saved personas stay valid).

**Tool restrictions are not supported yet.** A persona cannot list the tools it may or may not
use: `allowed_tools` and `forbidden_tools` were removed because per-harness tool restrictions
are not available yet (restricting tools per harness is future work). A persona, a persona API
request, a portable document or a `persona_overrides` block that still carries either key is
refused with an error naming it, never read with the key dropped. Saved personas had both keys
stripped by migration `000117_strip_persona_tool_restrictions`.

## How a persona and a request prompt combine

The persona is the base. A request's ad-hoc `system_prompt` is appended after the persona's
instructions and never replaces them. Forge sends `session.persona` (the pinned document) and
`session.systemPrompt` (the request) separately; Skuld composes them.

## What each harness enforces

| Harness | Instructions | Permission mode |
|---|---|---|
| Claude (Agent SDK, CLI, persistent, sdk-websocket, tmux) | appended to Claude's own prompt, resent on resume | `read_only` offers only tools that cannot change the workspace (`--tools`: read, search, web, task list, and a shell limited by rules to read-only repository commands and the session's own commands), pre-approves exactly those and denies everything else (`dontAsk`); `deny_all` offers no tool; `workspace_write` is stated, not enforced (the session runtime is the boundary); `prompt` refused |
| Codex app-server and exec | `developerInstructions` / `developer_instructions`, on start and resume | OS sandbox `read-only` / `workspace-write` with approvals `never`; the node's own sandbox is only ever narrowed; `prompt`, `deny_all` refused |
| OpenCode, Pi, dsh | appended | `read_only`, `prompt`, `deny_all` refused |
| Grok | sent as ACP `systemPrompt` (unverified, stated) | as above |
| Muse, Claude/Codex Remote Control | none: a persona with instructions is refused | as above |

Without a restricting `permission_mode`, a harness runs with the tools the node gives it.

## Where to look

`persona.json` in the session's per-start runtime dir records the persona and what was made
of it (the boundary handed to the transport, and the gaps). `/ready` carries the same summary
under `persona`, or the refusal under `error`.

## After editing a built-in persona

Bundled workflows pin their personas by digest. Run
`uv run python scripts/repin_bundled_workflows.py` to re-pin them (and bump each changed
workflow's `version` and its entry in `tests/test_ting/data/bundled_workflow_versions.lock.json`).
