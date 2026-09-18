# Slash-command coverage in Forge web

Reviewed 2026-09-17 against `forge/ux-improvement`, Lexi's ForgeKit and the running
session brokers. This is a coverage review and proposed sequence of work, not a
backend rollout. No commands were executed and no live runtimes were restarted.

## Why the menu is small

The React menu displays every advertised command; it does not truncate the
catalogue. Its viewport scrolls, which means a phone initially shows only a few
rows. Typing after `/` filters those rows by name.

There are three separate limits upstream of the menu:

| Layer                    | Evidence                                                                                                                                                                                                            | Consequence                                                                                                                                                                                                  |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Running Codex brokers    | A read-only sample on each of Thor, Build and Build Bro returned six entries: `/effort`, `/compact`, `/review`, `/goal`, `/title`, `/fork`.                                                                         | Refreshing the web application cannot manufacture the missing runtime capabilities.                                                                                                                          |
| Checked-in Codex adapter | Nine adapter entries plus the broker's `/effort`: the six above, plus `/rename`, `/status`, `/skills`, `/mcp`.                                                                                                      | There is a four-command gap between the sampled running catalogue and source. Compare the actual runtime release before a controlled upgrade; the endpoint response alone does not identify its exact build. |
| Full CLI experience      | The repository's **September 13 inventory** contains 54 names: eight implemented app-server actions, one compatibility conflict, 28 not implemented as Forge commands, and 17 client/platform-specific affordances. | This dated inventory is broader than the server adapter. It is not a claim that all 54 commands are available in the currently installed CLI or callable through the app-server API.                         |

The source catalogue is explicit in
[`codex_ws.py`](../../src/skuld/transports/codex_ws.py). Discovery returns that
catalogue once a thread exists; `refresh=true` does not enumerate the Codex TUI.
The generic broker adds `/effort`. Source and live counts include the legacy
`/title` alias, so ten entries do not mean ten distinct native workflows.

Some implemented mappings also have intentionally narrower behavior:

- `/skills` lists workspace skills; it does not implement a skill picker or typed
  invocation. Codex skill references use its own native input contract.
- `/mcp` lists server/tool status; it does not reproduce the interactive MCP
  configuration and authentication flows.
- `/fork` returns a native thread identifier; complete Forge session creation,
  ownership, ancestry and navigation need additional lifecycle integration.
- Forge's `/title` is a legacy alias for native thread renaming. The dated Codex
  TUI reference uses `/title` for terminal-title settings. The web pencil updates
  the Forge session name through a separate API; these names must not be conflated.

## Claude has different discovery limits

`tmux_interactive.py` seeds 21 built-in commands. The broker adds `/effort`, giving
22 entries before any live-menu discovery. A normal `refresh=false` call returns
that cache immediately. Workspace commands, plugins and skills missing from the
seed will therefore not appear until a fuller catalogue has been discovered.

The current forced refresh is a terminal probe: it sends Escape, Ctrl-U, `/` and
navigation keys, captures the menu, then clears the input again. The transport
avoids probing during an active turn, but Ctrl-U can still erase an idle draft.
It is not suitable for automatic refresh whenever a browser opens a session.

Headless Claude transports instead derive command existence from CLI `system/init`
names and skills. The filesystem only enriches descriptions and argument hints;
it does not add unadvertised commands. Refreshing that enrichment does not itself
make the CLI advertise newly installed commands. The sampled live sessions were
Codex; these Claude findings come from source review, not a live Claude probe.

Lexi's `FKSlashCommand` model and normalizer use the same rich catalogue fields.
Its older `slash-command.md` also describes the headless-Claude text-send path.
That guidance does not imply Codex app-server actions can run as ordinary model
messages. Harness-specific dispatch remains necessary.

## Suggested expansion sequence

1. **Close the runtime/source gap first.** Identify each host's deployed broker
   release; validate the existing ten-entry catalogue and its handlers in an owned
   test session; then use the established preservation/resume procedure for a
   controlled runtime upgrade. Web redeployment alone is insufficient. Existing
   rollout incident documentation makes session preservation a concrete concern.
2. **Make coverage visible.** Expose catalogue count, runtime/version, provenance
   (native discovery, cached seed or adapter mapping) and last refresh. Give a
   useful explanation when discovery fails or a command is unavailable. Avoid
   presenting a static list as complete native discovery.
3. **Prioritize useful workflows.** Model/effort selection can use existing native
   runtime-option APIs. Status/context information, skills and MCP deserve clear
   results and focused pickers. Reuse the existing session name and lifecycle
   controls where they already express the operation. Add slash aliases only
   after the underlying behavior is implemented and tested.
4. **Improve Claude discovery safely.** Prefer a noninteractive native catalogue.
   If a terminal probe is required, make it an explicit refresh with an idle-state
   check and a proven way to preserve the draft and restore the pane. Rebuild
   descriptions after workspace/skill changes while retaining the CLI as the
   authority on which commands exist.
5. **Add result and recovery semantics before broader mutations.** Distinguish
   accepted, running, completed and failed operations; correlate a durable request
   ID and native operation/turn ID; replay outcomes across reconnects without
   resending commands. `sent` currently means dispatch returned, not that a review
   or compaction finished. Fork, rollback, worktree and archive actions must also
   remain consistent with Forge's session/workspace state.

The concrete next increment is the existing four missing mappings plus clear
catalogue provenance. Full TUI-menu parity is a series of API/UI integrations,
not an expansion of a frontend name array.

## Source map

- [Dated command inventory](codex-command-inventory-20260913.json) and
  [integration review](codex-integration-review-20260913.md).
- [Earlier command design inventory](codex-future-command-capabilities.md), which
  records the previous decision to defer bulk exposure. The current request
  reopens the coverage review; it does not require that entire inventory shipped.
- [`broker.py`](../../src/skuld/broker.py): discovery normalization and `/effort`.
- [`tmux_interactive.py`](../../src/skuld/transports/tmux_interactive.py): seeded
  catalogue and terminal probing.
- [`slash_commands.py`](../../src/skuld/slash_commands.py): Claude catalogue
  enrichment using CLI-advertised names.
- [Current web command flow](web-session-commands-review.md): endpoints,
  autocomplete, argument handling and rejection feedback.
