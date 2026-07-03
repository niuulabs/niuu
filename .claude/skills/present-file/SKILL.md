---
name: present-file
description: Hand the user a file from a Forge session — it appears in their Lexi app as a tappable card that opens in the file preview. Use for finished deliverables (reports, images, PDFs, diagrams, screenshots, charts, build artifacts).
---

# present-file — deliver a file to the user

In a Forge coding session you can hand the user any file on the host and it shows up in their app
as a tappable card that opens in the built-in file preview (QuickLook). This is the in-app,
durable analogue of the harness `SendUserFile` tool — it works over the Forge data plane, so it
reaches the Lexi app (not just claude.ai).

## Usage

```bash
present-file <path> [--caption "short note"] [--title "Display Name.ext"]
```

- `<path>` — any file on the host. **Absolute or relative; inside OR outside the workspace** — a
  `/tmp` scratch artifact works exactly like a repo file (the broker stages a copy).
- `--caption` — a short line shown under the card (optional).
- `--title` — override the display name (defaults to the file's basename).

On success it prints `{"file_id": "...", "name": "...", "size": ..., "mime": "..."}`. On failure it
prints the error and exits non-zero (missing file → exit 4, too large → the broker returns 413).

## Examples

```bash
present-file /tmp/report.pdf --caption "The Q3 analysis you asked for."
present-file apps/chat/build-screenshots/build-42/dashboard.png --title "Build 42 — Dashboard"
present-file /tmp/diagram.svg
```

## When to use it

- **Do** use it for finished deliverables the user would want to open or keep: a generated report,
  a rendered chart/diagram, a screenshot, a produced PDF, an exported artifact.
- **Don't** use it for routine tool output, logs, or intermediate scratch files — those belong in
  the normal transcript, not as a delivered file.

## How it works (for the curious)

A per-session broker PATH shim POSTs the resolved absolute path to a loopback endpoint. The broker
stages a copy under the session home (outside the workspace, survives restart), mints an opaque
`file_id`, and emits the file as a self-contained assistant turn carrying a `present_file` tool_use
block. The app renders that as the card and fetches the bytes by opaque id (never by path). Engine-
agnostic: it works the same in Claude Code and Codex sessions.
