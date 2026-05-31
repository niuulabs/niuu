# Web UI

The Volundr web UI is a React single-page application built with Vite. It covers session management, settings, and admin functions.

## Session management

### Dashboard

The dashboard shows all your sessions with status, model, repo, and tokens used.

<div class="screenshot-full" markdown>

![Dashboard](../images/dashboard.png)

</div>

### Launch wizard

Sessions are created through a two-step wizard:

1. **Pick a template** — browse available workspace templates.
2. **Configure** — set the session name, model, repo, branch, credentials, integrations, and MCP servers.

<div class="screenshot-gallery" markdown>

<figure markdown>
![Template selection](../images/launch-wizard.png)
<figcaption>Step 1 — choose a template</figcaption>
</figure>

<figure markdown>
![Session configuration](../images/launch-wizard-config.png)
<figcaption>Step 2 — configure the session</figcaption>
</figure>

</div>

### Session workspace

Once a session is running, open it to access the workspace tabs:

- **Chat** — talk to the AI coding agent in real-time via WebSocket.
- **Terminal** — full shell access powered by ttyd.
- **Code** — VS Code running in the browser via code-server.
- **Diffs** — review file changes with an inline diff viewer.
- **Chronicles** — browse session history and timelines.

<div class="screenshot-gallery" markdown>

<figure markdown>
![Session workspace](../images/session-workspace.png)
<figcaption>Session workspace with tabs</figcaption>
</figure>

<figure markdown>
![Session diffs](../images/session-diffs.png)
<figcaption>Diffs — review code changes</figcaption>
</figure>

</div>

<div class="screenshot-full" markdown>

![Chronicle timeline](../images/chronicle-timeline.png)

</div>

## Settings

<div class="screenshot-full" markdown>

![Credentials management](../images/settings-credentials.png)

</div>

- **Credentials** — manage API keys, OAuth tokens, and SSH keys.
- **Integrations** — configure connections to external services like Linear and Jira.
- **Workspaces** — manage PVC storage for persistent session data.

<div class="screenshot-gallery" markdown>

<figure markdown>
![Workspace storage](../images/settings-workspaces.png)
<figcaption>Workspace PVC management</figcaption>
</figure>

<figure markdown>
![Integrations](../images/settings-integrations.png)
<figcaption>External service integrations</figcaption>
</figure>

</div>

## Admin

Admin pages are visible only to users with the admin role.

- **User management** — view and manage users across tenants.
- **Tenant management** — create and configure tenants.

<div class="screenshot-full" markdown>

![User management](../images/admin-users.png)

</div>

## Authentication

The web UI uses OIDC for authentication. Any compliant identity provider works — Keycloak, Entra ID, Okta, or anything else that speaks OIDC. Auth configuration is set in the Helm values.

<div class="screenshot-full" markdown>

![Login](../images/login.png)

</div>
