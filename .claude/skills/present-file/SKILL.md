---
name: present-file
description: Hand the user a file from a Forge session — it appears in their Lexi app as a tappable card that opens in the file preview. Use for finished deliverables (reports, images, PDFs, diagrams, screenshots, charts, build artifacts).
---

# present-file — deliver a file to the user

In a Forge coding session you can hand the user a file and it shows up in their app
as a tappable card that opens in the built-in file preview (QuickLook). This is the in-app,
durable analogue of the harness `SendUserFile` tool — it works over the Forge data plane, so it
reaches the Lexi app (not just claude.ai).

## Usage

```bash
present-file <path> [--caption "short note"] [--title "Display Name.ext"]
```

- `<path>` — absolute or relative, but it **must be under the session workspace or your home
  directory**. Anything else (including `/tmp`, on macOS and Linux) is refused with
  `path is outside the session roots`. Write scratch artifacts to `~/tmp/` instead
  (`mkdir -p ~/tmp`); the broker stages its own copy either way.
- `--caption` — a short line shown under the card (optional).
- `--title` — override the display name (defaults to the file's basename).

On success it prints `{"file_id": "...", "name": "...", "size": ..., "mime": "..."}`. On failure it
prints the error and exits non-zero: missing file → exit 4, outside the workspace and home → 400
(the message names both roots and the remedy), too large → 413, and the host's presented-file quota
full → 507 (`presented_files.max_total_bytes`).

## Examples

```bash
present-file ~/tmp/report.pdf --caption "The Q3 analysis you asked for."
present-file apps/chat/build-screenshots/build-42/dashboard.png --title "Build 42 — Dashboard"
mkdir -p ~/tmp && cp /tmp/diagram.svg ~/tmp/ && present-file ~/tmp/diagram.svg
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
