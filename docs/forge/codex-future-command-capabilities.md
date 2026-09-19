# Future command capabilities — explicitly deferred

**Decision, September 13, 2026:** the user wants to rethink slash commands, their
utility and their interaction model. This document is a future-work inventory,
**not an implementation promise, runnable menu, or rollout requirement**. Do not
bulk expose the Codex TUI menu through Forge or treat command-looking user prose
as privileged API operations.

The dated inventory (`codex-command-inventory-20260913.json`, git history at
`99e41f83a`) accounts for 54 names
from the [official command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
and release notes. A TUI affordance does not necessarily correspond to an
app-server method. Existing bounded command mappings remain compatible; their
error and argument fidelity fixes are not an expansion of the menu.

## Design questions for the next review

| User intent | Candidate interaction | Required semantics before adding it |
| --- | --- | --- |
| Select model, effort, speed | Native-catalog settings selector; no slash required | Account/runtime availability; explicit cost/speed choice; requested next turn versus effective current turn; persistent acknowledgement. Server primitives are implemented separately. |
| Compact or review work | Typed action with progress/result | Native dispatch is not objective completion; retain action/turn IDs and cancellation state. |
| Edit title or objective | Field editor / named action | Keep `/rename` distinct from Codex TUI `/title`; pause/resume/clear goal are not replacement objective text. |
| Choose skills, MCP tools, connected apps | Context/resource picker | Workspace scope, typed invocation inputs, OAuth and per-action consent; do not pretend a listing invokes a skill. |
| Change permission or collaboration mode | Explicit settings/consent view | Show scope and requested authority, reject unsupported rules/edits, retain human authorship. No coordinator-specific workflow engine. |
| Fork, rollback, branch, worktree | Forge-managed session operation | Session ancestry, workspace ownership, durable history, auth and recovery must move together; a bare native thread ID is not a managed Forge session. |
| Stop a turn, terminal, or session | Distinct lifecycle controls | Never conflate live steer, queued next-turn message, Stop, terminal termination and session archive. |
| Clear display, copy, editor, theme, help | Client affordance | Do not invent a server command for UI-only behavior. |
| Logout, install plugins, share diagnostics, delete | Separately consented privileged action | Review scope, credentials/privacy, reversibility and confirmation; never arbitrary RPC passthrough. |

## Cross-harness contract to settle

1. Decide which intents deserve a command at all, then map each supported harness.
2. Return a typed operation result with a stable client request ID and native
   operation/turn identity. A timeout is uncertain, not safe-to-retry failure.
3. Persist accepted, running and terminal outcomes, and replay them without resends.
4. Discover only executable capabilities. Keep native-only, unsupported,
   experimental and client-only affordances distinct.
5. Preserve ordinary user text and existing integrations while introducing any
   replacement UI. Do not infer authorship or elevated privileges from text.

Native queue editing, side chats/worktrees, native realtime audio, structured
media/skill/mention composition and destructive session operations need their own
product contracts. They must not be smuggled into the maintenance rollout as new
slash aliases. Existing Lexi Voice/Live and ordinary session controls remain intact.
