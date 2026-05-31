# Sessions

A session is a managed, isolated environment running in a Kubernetes pod (or a Docker container when running locally). Each session contains:

- An AI coding agent (Claude Code or Codex)
- A code editor (VS Code via code-server)
- A terminal (ttyd)
- A shared workspace volume

All containers in a session share the same workspace PVC, so the agent, editor, and terminal all see the same files.

## Session states

Sessions move through a defined lifecycle:

```
CREATED → STARTING → PROVISIONING → RUNNING → STOPPING → STOPPED → ARCHIVED
```

A session can also enter `FAILED` from either `PROVISIONING` or `RUNNING`.

| State | Meaning |
|-------|---------|
| **CREATED** | Session record exists, nothing deployed yet |
| **STARTING** | Session start requested, building pod spec |
| **PROVISIONING** | Pod submitted to Kubernetes, waiting for containers |
| **RUNNING** | All containers healthy, session is usable |
| **STOPPING** | Shutdown in progress, pods being terminated |
| **STOPPED** | Pods removed, chronicle auto-created |
| **ARCHIVED** | Moved to archive storage, can be restored later |
| **FAILED** | Something went wrong during provisioning or runtime |

## Creating a session

Two ways to create a session:

**Web UI** — Use the launch wizard. Pick a template or configure manually. Set the session name, model, repository, branch, credentials, and integrations.

**CLI** — Run `volundr sessions create`. Same options available as flags.

You can start from a template (pre-configured blueprint) or set everything yourself: model, resource limits, repos, MCP servers, environment variables.

## What happens during start

When you start a session, this is what runs behind the scenes:

1. The session contributor pipeline kicks in. Ten contributors build the pod spec — each one adds a piece (agent container, editor sidecar, volume mounts, credentials, etc.).
2. The pod manager deploys the assembled pod to Kubernetes.
3. Readiness polling begins. Volundr watches for all containers to pass their health checks.
4. Once everything is healthy, the session status flips to `RUNNING`.

If any container fails to start or crashes during provisioning, the session moves to `FAILED`.

## Working in a session

Once a session is running, you can:

- **Chat with the AI agent** — Give it tasks, ask questions, have it write or refactor code.
- **Use the terminal** — Run commands, install packages, run tests.
- **Edit code in VS Code** — Full editor experience via code-server.
- **Review diffs** — See what the agent changed before committing.

Everything happens in the same workspace. The agent edits files, you see the changes in the editor, and you can run the code in the terminal.

## Stopping a session

Stop a session with `volundr sessions stop <id>` or click Stop in the web UI.

When a session stops:

1. The pod is terminated.
2. A chronicle is automatically created, capturing the full history of the session.
3. The session moves to `STOPPED`.

## Archiving

Archiving moves a stopped session to archive storage. This cleans up resources while preserving the session record. Archived sessions can be restored later.

## Reforging

Reforging creates a new session from a chronicle. It picks up where the previous session left off — same repo state, same context.

The new session's chronicle links back to the original via `parent_chronicle_id`, creating a chain. You can trace work across multiple sessions this way.

This is useful when you need to continue a task across sessions, or when a session failed and you want to retry from the last known state.

## Session access

Sessions are scoped by tenant.

- Non-admin users see only their own sessions.
- Admins see all sessions within their tenant.

There is no cross-tenant session visibility.
