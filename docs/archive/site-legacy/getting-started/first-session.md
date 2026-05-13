# First Session

A session is a containerized AI coding environment: an LLM agent, a terminal, a code editor, and your repo -- all wired together. This guide walks through creating one.

---

## Web UI

### 1. Open Volundr

Navigate to your Volundr instance in a browser (default: [http://localhost:8080](http://localhost:8080)).

### 2. Launch a new session

Click **New Session**. The launch wizard opens with two steps.

**Step 1: Template**

Pick a session template or use the default. Templates pre-configure the model, resource limits, and integrations for common workflows.

**Step 2: Configure**

Fill in the details:

| Field | Description |
|-------|-------------|
| **Name** | A short name for this session |
| **Model** | Which AI model to use (e.g. `claude-sonnet-4`) |
| **Repository URL** | The repo to clone into the workspace |
| **Branch** | Branch to check out (defaults to the repo's default branch) |
| **Credentials** | Git credentials for private repos |
| **Integrations** | Optional tools: Linear, Slack, etc. |

Click **Launch**.

### 3. Watch it start

The session transitions through states:

```
CREATED -> STARTING -> PROVISIONING -> RUNNING
```

This takes 10-30 seconds depending on repo size and image pull time.

### 4. Use the session

Once running, the **Chat** tab opens. Type a message to start working with the AI agent.

The session has five tabs:

| Tab | What it does |
|-----|-------------|
| **Chat** | Talk to the AI coding agent |
| **Terminal** | Full shell access to the workspace |
| **Code** | VS Code editor (Code Server) |
| **Diffs** | See what the agent changed |
| **Chronicles** | Session history and summaries |

### 5. Stop the session

When you're done, click **Stop**. A chronicle is automatically created -- a summary of what happened during the session, the changes made, and the conversation history.

---

## CLI

### 1. Set up your context

If you're connecting to a remote Volundr instance, add it as a context:

```bash
niuu context add local --server http://localhost:8080
```

Skip this if you ran `niuu volundr init` and `niuu volundr up` locally -- the context is already configured.

### 2. Create a session

```bash
niuu sessions create \
  --name my-project \
  --repo org/repo \
  --model claude-sonnet-4
```

This returns a session ID.

### 3. Start the session

```bash
niuu sessions start <session-id>
```

### 4. Open the TUI

```bash
niuu
```

The terminal UI gives you the same capabilities as the web UI: chat, terminal, diffs, and chronicles. Navigate between views with keyboard shortcuts.

### 5. Stop the session

```bash
niuu sessions stop <session-id>
```

A chronicle is created automatically, same as the web UI.

---

## What happens under the hood

The details depend on your deployment mode.

### Local mode (mini)

In local mode, Volundr runs all services as processes on your machine:

| Process | Role |
|---------|------|
| **PostgreSQL** | Stores session state, chronicles, and configuration |
| **API server** | Handles lifecycle operations (create, start, stop, delete) |
| **Reverse proxy** | Routes requests to the correct service |

Sessions run as local processes with your repo cloned into a workspace directory.

### Kubernetes mode (k3s or production)

In Kubernetes mode, Volundr creates a pod for each session with dedicated containers:

| Container | Role |
|-----------|------|
| **Skuld broker** | Manages the LLM conversation and tool execution |
| **Code Server** | VS Code in the browser |
| **Terminal** | Shell access to the workspace |

All containers share a workspace volume (PVC) where your repo is cloned.

Chat messages go directly from your browser to the Skuld broker inside the pod -- they don't route through the Volundr API server. This keeps latency low and means the API server doesn't need to handle streaming LLM responses.

The Volundr API handles lifecycle operations only: creating, starting, stopping, and deleting sessions.

---

## Next steps

- [Local Quickstart](local-quickstart.md) -- full end-to-end walkthrough for local mode
- [Configuration](configuration.md) -- customize models, resource limits, and integrations
- [Troubleshooting](troubleshooting.md) -- common issues and solutions
- [Helm Deployment](../deployment/helm.md) -- run Volundr on a real cluster
