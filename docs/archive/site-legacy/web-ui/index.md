# Web UI

The web UI now lives entirely in the `web-next/` workspace. It is a composable React/Vite application with the main shell in `web-next/apps/niuu` and shared capabilities split across `web-next/packages/*`.

## Session views

The main workspace provides tabs for chat, terminal, code, diffs, chronicles, and logs.

<div class="screenshot-gallery" markdown>

<figure markdown>
![Session chat](../images/dashboard.png)
<figcaption>Chat — talk to the AI coding agent</figcaption>
</figure>

<figure markdown>
![Session diffs](../images/session-diffs.png)
<figcaption>Diffs — review code changes</figcaption>
</figure>

</div>

<div class="screenshot-full" markdown>

![Chronicle timeline](../images/chronicle-timeline.png)

</div>

<div class="screenshot-full" markdown>

![Session workspace](../images/session-workspace.png)

</div>

## Launch wizard

Sessions are created through a guided wizard: pick a template, configure resources and credentials, then launch.

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

## Settings

<div class="screenshot-full" markdown>

![Credentials management](../images/settings-credentials.png)

</div>

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

<div class="screenshot-full" markdown>

![User management](../images/admin-users.png)

</div>

## Development

```bash
cd web-next
pnpm install --frozen-lockfile
pnpm dev              # Dev server at http://localhost:5173
pnpm build            # Production build
pnpm lint             # ESLint
pnpm format:check     # Prettier check
pnpm typecheck        # TypeScript check
pnpm test             # Unit tests with coverage
```

## Architecture

The current frontend is organised as a workspace:

```
web-next/
├── apps/
│   └── niuu/        # Primary browser app and route shell
├── packages/
│   ├── shell/       # Shell layout and chrome
│   ├── ui/          # Shared UI primitives
│   ├── auth/        # Authentication/runtime config
│   └── plugin-*/    # Domain plugins (Volundr, Ting, Ravn, Mimir, ...)
└── e2e/             # Playwright coverage for the integrated app
```

## Styling

Design tokens live in the shared workspace packages and are consumed across the `niuu` app and domain plugins. Styling is no longer tied to the old `web/src` layout.

## Key components

| Component | Description |
|-----------|-------------|
| `LaunchWizard` | Guided session creation flow |
| `SessionChat` | WebSocket chat connected to Skuld |
| `SessionChronicles` | Chronicle timeline and history |
| `SessionDiffs` | Git diff viewer |
| `SessionTerminal` | ttyd terminal integration |
| `TemplateBrowser` | Workspace template selection |
| `CredentialForm` | Credential management |
| `IntegrationCard` | Integration setup |
| `AdminGuard` | Admin-only route protection |

## Testing

Tests use vitest with @testing-library/react. Test files are co-located with source:

```
components/
  StatusBadge/
    StatusBadge.tsx
    StatusBadge.module.css
    StatusBadge.test.tsx
    index.ts
```

Coverage thresholds: 85% on statements, branches, functions, and lines.
