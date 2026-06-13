# Git And Review Flow

Keep AI work reviewable.

Niuu workspaces are designed to produce inspectable changes. Operators should review diffs, logs, tests, and session context before promoting work.

## Review surfaces

- Session diffs
- Terminal output
- Files
- Chronicles
- Telemetry and logs
- Pull request or branch state when a git provider is configured

## Recommended flow

Create one session for one shippable unit, keep the branch focused, run checks, review the diff, and archive or follow up when the work is complete.
