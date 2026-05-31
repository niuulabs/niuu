---
name: new-module
description: Scaffold and implement a new UI module for the Odin platform. Use when the user wants to add a new product module (like Volundr or Ting) to the web UI.
argument-hint: <module-name> [description of what the module does]
---

# New Module: $0

You are scaffolding and implementing a new UI module for the Odin web platform.

## Step 1: Run the scaffold script

```bash
./scripts/scaffold-module.sh $0
```

This creates the full module skeleton under `web/src/modules/$0/` with:
- `register.ts` — module definition (routes, sections, proxies)
- `ports/` — service interface
- `adapters/api/` + `adapters/mock/` — real and mock service implementations
- `models/` — TypeScript domain models
- `pages/` — page components with CSS modules
- `store/` — Zustand store

It also registers the module import in `web/src/modules/index.ts`.

## Step 2: Read the architecture

Before writing any code, read these files to understand the patterns:

1. **Module contract**: `web/src/modules/shared/registry/types.ts` — the `ModuleDefinition` interface
2. **Example (simple module)**: `web/src/modules/volundr/register.ts` — single-page module with many settings sections
3. **Example (complex module)**: `web/src/modules/ting/register.ts` — multi-page module with layout, nested routes, sections, and proxy config
4. **Shared API client**: `web/src/modules/shared/api/client.ts` — how to create API clients
5. **Shared identity**: `web/src/contexts/useAppIdentity.ts` — how to access current user identity

## Step 3: Design the module

Based on the user's description (`$ARGUMENTS`), plan:

1. **What pages does this module need?** — Single page or multi-page with layout?
2. **What domain models does it have?** — Define TypeScript interfaces in `models/`
3. **What API endpoints does it call?** — Define the service port interface
4. **Does it need its own backend proxy?** — If the backend runs on a different port, add a proxy entry to `web/src/modules/proxy-manifest.ts`
5. **Does it contribute settings or admin sections?** — Add them to the `sections` array in `register.ts`

## Step 4: Implement

Work through the module in this order:

### 4a. Domain models (`models/<name>.model.ts`)
Define all TypeScript interfaces for the module's domain objects.

### 4b. Service port (`ports/<name>.port.ts`)
Define the service interface with all methods the UI will call. Follow the pattern from `web/src/modules/volundr/ports/volundr.port.ts` or `web/src/modules/ting/ports/ting.port.ts`.

### 4c. Mock adapter (`adapters/mock/<name>.adapter.ts`)
Implement the mock service with realistic fake data. This enables offline development and is required before the real API exists.

### 4d. API adapter (`adapters/api/<name>.adapter.ts`)
Implement the real API adapter using `createApiClient('/api/v1/<name>')`. Follow the snake_case → camelCase mapping pattern from existing adapters.

### 4e. Pages and components
Build the UI. Follow these rules strictly:
- **CSS Modules only** — no inline styles, no Tailwind classes in JSX, no CSS-in-JS
- **Design tokens** — use `var(--color-*)`, `var(--space-*)`, `var(--text-*)` from `web/src/styles/tokens.css`
- **Co-located styles** — every component gets a `.module.css` file next to it
- **Early returns** — no nested conditionals
- **Lazy loading** — all page components are loaded via the route definitions in `register.ts`

### 4f. Update `register.ts`
Configure the full module definition:
- Choose an appropriate icon from `lucide-react`
- Define all routes (with layout if multi-page)
- Add settings/admin sections if needed
- If this module has its own backend, add a proxy entry to `web/src/modules/proxy-manifest.ts`

### 4g. Store (if needed)
Set up Zustand store for module-specific UI state. Keep it module-scoped — do not create global state.

## Step 5: Verify

After implementation:

1. Run `cd web && npx tsc --noEmit --project tsconfig.app.json` — must have zero errors
2. Confirm the module appears in the sidebar
3. Confirm all routes resolve correctly
4. Confirm the module follows the project rules:
   - No imports from other product modules (Volundr, Ting) — only from `shared/`
   - CSS modules with design tokens (no inline styles or Tailwind)
   - Hexagonal architecture: pages import from ports, never from adapters directly

## Rules

- **Module boundaries**: This module MUST NOT import from `volundr/` or `ting/`. Shared code lives in `modules/shared/`. See `.claude/rules/module-boundaries.md`.
- **No edits to App.tsx**: Routes are auto-generated from `register.ts`. If you're editing App.tsx, you're doing it wrong.
- **No edits to vite.config.ts**: Proxy config comes from `web/src/modules/proxy-manifest.ts`.
- **Identity**: Use `useAppIdentity()` from `@/contexts/useAppIdentity` — never import `volundrService` for identity checks.
