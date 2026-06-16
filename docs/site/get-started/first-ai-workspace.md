# First AI Workspace

Create a session and inspect the operator surfaces around it.

In Niuu, a workspace session is the unit of live AI work. It connects a repo, preset, model, runtime, chat, terminal, files, logs, telemetry, and review state.

## Create a session

1. Start the local stack.
2. Open the web UI.
3. Go to **Völundr**.
4. Choose a quick launch preset or create a custom launch.
5. Select a safe repo and provider configuration.

For a first pass, use a throwaway repo or a repo with no secrets in the working tree.

## Inspect the session

Use the session tabs to understand what Niuu keeps together:

- **Chat** for operator and assistant conversation
- **Terminal** for the live workspace shell
- **Diffs** for review
- **Files** for workspace inspection
- **Chronicle** for persisted history
- **Telemetry** and **Logs** for runtime signals

## Review before merging

Treat the session branch and diff as the review boundary. Keep work small enough that a human can inspect what changed before promoting it.
