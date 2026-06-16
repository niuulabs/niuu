# Introduction

Start small, then add the parts of Niuu when you need them.

Niuu is a self-hosted platform for AI workspaces, assistant teams, durable
memory, workflows, and long-running assistants. You do not need to understand
the whole platform before you can use it.

The first mental model is simple:

| Tool | Use it for |
| --- | --- |
| `niuu` | Run and operate the local platform, web UI, sessions, and service stack. |
| `ravn` | Run an assistant directly, or run a resident assistant/daemon outside the UI. |

The rest of the names in the platform are capabilities that the stack provides.
You will meet them when they become useful.

## Recommended path

1. [Install the tools](install.md).
2. [Run the first local stack](first-local-stack.md).
3. [Launch your first AI workspace](first-ai-workspace.md).
4. [Configure your project](configure-project.md).
5. [Grow from small to autonomous](path-from-small-to-autonomous.md).

## What you will build up to

- A local platform you can open in the browser.
- One workspace session with chat, terminal, files, diffs, logs, and telemetry.
- Model routing when you want consistent access to local or cloud models.
- Shared memory when the work should survive one session.
- Workflows when work needs stages, gates, or several roles.
- Resident assistants when something should keep watching, curating, or improving.

Start with the first workspace. The rest can wait.
