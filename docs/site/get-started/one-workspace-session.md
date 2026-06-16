# One Workspace Session

A session is the basic unit of live AI work in Niuu.

Start one session before adding workflows or resident assistants. This teaches
the runtime boundary: what the assistant can see, where it can act, and how you
review the result.

![Launch wizard](../images/launch-wizard.png)

## What a session contains

A workspace session ties together:

- a repo or workspace target
- a model or model alias
- runtime settings and permissions
- chat
- terminal
- files
- logs and telemetry
- diffs and review state

The session is where concrete work happens. Workflows, resident assistants, and
memory systems may create or observe sessions, but the session remains the live
workspace.

## Launch a safe first session

1. Start the local platform.
2. Open the web UI.
3. Go to the workspace/session area.
4. Choose a quick launch preset if one exists.
5. Pick a safe repo.
6. Start the session.

For the first run, use a throwaway repo or a repo without secrets in the working
tree.

## Configure the launch

The launch wizard is where you choose the practical boundaries for the work.

![Launch wizard configuration](../images/launch-wizard-config.png)

Check:

- which repo is mounted
- which model or alias is selected
- what runtime target is used
- what credentials or integrations are available
- whether the session has write access

Do not mount your whole home directory just to make the first session work.
Give the assistant the smallest useful workspace.

## Inspect the workspace

After launch, open the session.

![Session workspace](../images/session-workspace.png)

Use the session tabs to understand what Niuu keeps together:

- Chat: conversation with the assistant.
- Terminal: shell access in the workspace.
- Files: inspect the workspace contents.
- Logs: runtime and tool output.
- Telemetry: timing, stages, and tool spans.
- Diffs: what changed.

## Review the diff

The diff is the review boundary. Even when the assistant does good work, treat
the result as a proposed change.

![Session diffs](../images/session-diffs.png)

For the first session, ask for a small change or a codebase explanation. Avoid
large refactors until you trust the launch preset and workspace boundaries.

## What good looks like

You should be able to answer:

- Which repo did the assistant work in?
- Which model did it use?
- What commands did it run?
- What files changed?
- Where can I stop or archive the session?

If you cannot answer those from the UI, pause and inspect the session before
launching more work.

## Next

Once a session works, configure model routing:

[Model routing](model-routing-step.md)
