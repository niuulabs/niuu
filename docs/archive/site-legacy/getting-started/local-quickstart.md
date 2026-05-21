# Local Quickstart

A single end-to-end walkthrough from zero to a working AI coding session on your local machine. No Kubernetes required.

---

## Prerequisites

Before you start, make sure you have:

- **An Anthropic API key** — get one at [console.anthropic.com](https://console.anthropic.com)
- **A GitHub personal access token** — create one at [github.com/settings/tokens](https://github.com/settings/tokens) with `repo` scope
- **macOS or Linux** — the CLI binary is available for both platforms

---

## Step 1: Download the `niuu` CLI

Pick the binary for your platform:

```bash
# macOS (Apple Silicon — M1/M2/M3/M4)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-darwin-arm64 -o niuu

# macOS (Intel)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-darwin-amd64 -o niuu

# Linux (x86_64)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-linux-amd64 -o niuu

# Linux (ARM64)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-linux-arm64 -o niuu
```

Make it executable and move it to your PATH:

```bash
chmod +x niuu
sudo mv niuu /usr/local/bin/
```

Verify it works:

```bash
niuu version
```

Expected output:

```
niuu version v0.x.x
```

---

## Step 2: Initialize Volundr

```bash
niuu volundr init
```

The wizard will prompt you for configuration. Here's an example flow:

```
? Select runtime: local
? Anthropic API key: sk-ant-api03-xxxx...
? Database mode: embedded
? GitHub personal access token: ghp_xxxx...
? GitHub organizations (comma-separated): my-org
? GitHub API URL: https://api.github.com

✓ Configuration saved to ~/.volundr/config.yaml
```

**What each option means:**

| Option | Recommended value | Why |
|--------|-------------------|-----|
| Runtime | `local` | Runs everything as local processes — no Docker or K8s needed |
| Anthropic API key | Your key | Powers the AI coding agent |
| Database mode | `embedded` | Bundles PostgreSQL — no separate database install required |
| GitHub token | Your PAT | Lets Volundr clone repos and create branches |
| GitHub orgs | Your org(s) | Filters which repositories appear in the UI |
| GitHub API URL | `https://api.github.com` | Change only for GitHub Enterprise Server |

---

## Step 3: Start Volundr

```bash
niuu volundr up
```

Expected output:

```
Starting embedded PostgreSQL... ✓ (port 5432)
Running database migrations... ✓
Starting API server... ✓ (port 8080)
Starting reverse proxy... ✓

Volundr is ready at http://localhost:8080
Press Ctrl+C to stop all services.
```

This starts three services:

1. **PostgreSQL** — embedded database for session state and chronicles
2. **API server** — handles session lifecycle (create, start, stop, delete)
3. **Reverse proxy** — routes web UI and API traffic

Leave this terminal running.

---

## Step 4: Open the Web UI

Open [http://localhost:8080](http://localhost:8080) in your browser.

You should see the Volundr dashboard. If you see a connection error, check [Troubleshooting](troubleshooting.md).

---

## Step 5: Create your first session

### Using the Web UI

1. Click **New Session**
2. **Template**: choose the default or pick one that matches your workflow
3. **Configure**:
    - **Name**: `my-first-session`
    - **Model**: `claude-sonnet-4`
    - **Repository URL**: a repo you want to work on (e.g. `github.com/my-org/my-repo`)
    - **Branch**: leave blank for the default branch
4. Click **Launch**

### Using the CLI (alternative)

```bash
niuu sessions create \
  --name my-first-session \
  --repo my-org/my-repo \
  --model claude-sonnet-4
```

Then start it:

```bash
niuu sessions start <session-id>
```

---

## Step 6: Start coding with the AI agent

Once the session reaches **RUNNING** state (10-30 seconds), the **Chat** tab opens.

Type a message to start working:

```
Add input validation to the user registration endpoint
```

The AI agent will:

- Read the relevant code in your repo
- Propose and implement changes
- Show diffs for review
- Create commits and branches

Use the tabs to switch between:

| Tab | Purpose |
|-----|---------|
| **Chat** | Conversation with the AI agent |
| **Terminal** | Shell access to the workspace |
| **Code** | VS Code editor |
| **Diffs** | Review changes the agent made |
| **Chronicles** | Session history and summaries |

---

## Step 7: Stop and review

When you're done:

1. Click **Stop** (or run `niuu sessions stop <session-id>`)
2. A **chronicle** is automatically created — a summary of the session, changes, and conversation
3. Review the chronicle in the **Chronicles** tab

---

## Using the TUI (optional)

Instead of the web UI, you can use the terminal UI:

```bash
niuu
```

This launches a full-screen TUI with the same capabilities. Navigate with:

| Shortcut | Page |
|----------|------|
| Alt+1 | Sessions |
| Alt+2 | Chat |
| Alt+3 | Terminal |
| Alt+4 | Diffs |
| Alt+5 | Chronicles |

---

## Checking service status

In a separate terminal:

```bash
niuu volundr status
```

Expected output:

```
SERVICE      STATE    PID    PORT   ERROR
postgresql   running  1234   5432
api-server   running  1235   8080
proxy        running  1236   8443
```

---

## Stopping Volundr

Press **Ctrl+C** in the terminal running `niuu volundr up`, or run:

```bash
niuu volundr down
```

---

## Local mode limitations

Local mode (mini) is designed for single-user development. Here's what's different compared to a full Kubernetes deployment:

| Feature | Local (mini) | k3s / Production |
|---------|:---:|:---:|
| Number of users | 1 | Many |
| OIDC authentication | No | Yes |
| Resource limits per session | No | Yes |
| Persistent volumes (PVC) | Local disk | Kubernetes PVC |
| Secret management (Vault, Infisical) | No | Yes |
| Gateway routing | No | Yes |
| Horizontal scaling | No | Yes |

For multi-user or production deployments, see the [Installation Guide](../installation/overview.md).

---

## Next steps

- [CLI Reference](../user-guide/cli.md) -- full list of `niuu` commands
- [Configuration](configuration.md) -- customize models, integrations, and more
- [Troubleshooting](troubleshooting.md) -- common issues and solutions
- [Git Workflows](../user-guide/git-workflows.md) -- how Volundr manages branches and PRs
