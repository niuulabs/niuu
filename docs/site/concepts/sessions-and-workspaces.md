# Sessions And Workspaces

Understand the unit of live AI work in Niuu.

A session is a running AI workspace with its own runtime state. It may include a repo checkout, chat, terminal, files, model configuration, credentials, logs, telemetry, and review history.

## Session lifecycle

Sessions usually move through these stages:

1. Created from a launch preset or custom launch.
2. Started in a local or remote runtime.
3. Used by an operator and one or more assistants.
4. Reviewed through diffs, logs, chronicles, and telemetry.
5. Stopped, archived, or reforged into follow-up work.

## Workspace boundary

Keep a session scoped to one reviewable stream of work. If two changes should be reviewed independently, use separate sessions or workflows.
