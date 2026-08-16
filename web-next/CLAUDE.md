# Niuu Web (`web-next/`)

This is the new, composable, plugin-based UI for the Niuu platform. It replaces the
monolithic `web/` app and supersedes the earlier prototype demos.

**Read this first in any new session.** It captures the non-negotiable architecture
and the rationale — not a log of past work.

---

## Non-negotiable architecture

### 1. Every plugin is its own publishable package

A consumer must be able to `npm install @niuulabs/plugin-ting` and embed Ting in their
own page without pulling in the rest of Niuu. This is the primary design constraint.
Everything else flows from it.

```tsx
// A third-party app using only Ting:
import { Shell } from '@niuulabs/shell';
import { ConfigProvider, ServicesProvider, FeatureCatalogProvider } from '@niuulabs/plugin-sdk';
import { ThemeProvider } from '@niuulabs/design-tokens';
import { createQueryClient } from '@niuulabs/query';
import { QueryClientProvider } from '@tanstack/react-query';
import { tingPlugin, type ITingService } from '@niuulabs/plugin-ting';
import '@niuulabs/design-tokens/tokens.css';
import '@niuulabs/ui/styles.css';
import '@niuulabs/shell/styles.css';

<ConfigProvider endpoint="/config.json" fallback={<Loading />}>
  <ThemeProvider theme="ice">
    <QueryClientProvider client={createQueryClient()}>
      <ServicesProvider services={{ ting: myTingAdapter }}>
        <FeatureCatalogProvider>
          <Shell plugins={[tingPlugin]} />
        </FeatureCatalogProvider>
      </ServicesProvider>
    </QueryClientProvider>
  </ThemeProvider>
</ConfigProvider>;
```

### 2. Hexagonal architecture per plugin

Every plugin package follows the same internal shape:

```
plugin-<name>/
├── src/
│   ├── domain/        pure value objects, no framework imports
│   ├── application/   use cases (orchestrate domain + ports)
│   ├── ports/         interfaces (I<Name>Service, I<Name>Stream)
│   ├── adapters/      implementations (http / ws / mock) — optional per plugin
│   ├── ui/            React components, pages, hooks
│   └── index.ts       exports: <name>Plugin (PluginDescriptor), ports, domain types
```

Rules:

- `ui/` may import from `application/`, `domain/`, `ports/`. Never from `adapters/`.
- `adapters/` implement `ports/`. Consumers can ignore the built-in adapters and
  inject their own.
- Business logic lives in `application/` and `domain/`, not in components.

### 3. Services via dependency injection, never imported directly

Components get services from `useService<T>(key)`. The consumer wires adapters in
`<ServicesProvider>`. **Plugins never import concrete service implementations.**

```ts
// inside plugin-ting:
const ting = useService<ITingService>('ting'); // contract only
```

```ts
// inside apps/niuu/src/services.ts:
import { buildTingApiAdapter } from '@niuulabs/plugin-ting/adapters/http';
const services = { ting: buildTingApiAdapter(config.services.ting) };
```

Tests supply mock adapters. Adapter swap = zero component changes.

### 4. TanStack Query wraps services — it does not replace them

The services abstraction (ports + adapters + DI) is the stable plugin boundary. TanStack
Query is the client cache on top. Every API call looks like:

```ts
export function useSagas() {
  const ting = useService<ITingService>('ting');
  return useQuery({ queryKey: ['ting', 'sagas'], queryFn: () => ting.getSagas() });
}
```

Server state → Query. Client-only UI state → Zustand (when needed).

### 5. Runtime config, not build-time

`apps/niuu/public/config.json` is fetched on boot by `<ConfigProvider>` and validated
with Zod. Operators edit the file and refresh the browser — no rebuild. The config
declares which plugins are enabled, service URLs, theme, auth config.

Three feature-flag tiers, any of which can hide a plugin:

1. **Install-time** — `apps/niuu/src/plugins.ts` imports; not imported = not bundled
2. **Runtime operator flags** — `public/config.json` `{ plugins.<id>.enabled }`
3. **Runtime per-user flags** — backend `FeatureCatalog` (future; same port pattern)

### 6. Tailwind is the default styling layer, driven by design tokens

Styling in `web-next/` uses **Tailwind CSS** mapped to our design tokens.

- **`@niuulabs/design-tokens` owns `tokens.css`** — ported from the earlier
  prototype system and now treated as the single source of truth for color,
  spacing, typography, motion, and theme (ice / amber / spring).
- **Tailwind config reads from tokens** — `tailwind.config.ts` in each package
  pulls from `tokens.css` via `theme.extend`. Never hard-code hex or px in a class
  (`bg-[#09090b]` is a bug — use `bg-bg-primary` mapped to `var(--color-bg-primary)`).
- **Single brand theme policy** — default is `ice`. `[data-theme]` on `<html>` swaps
  token values; Tailwind classes don't change.
- **Each plugin package publishes its own pre-compiled CSS.** Consumers install the
  plugin and import its `styles.css`. They do **not** need Tailwind in their build.
  Each package runs Tailwind at build time (tsup + postcss) and ships only the
  classes it actually uses.
- **Shared preset** — `@niuulabs/design-tokens/tailwind.preset.ts` centralizes the
  token mapping so every package's `tailwind.config.ts` inherits it.
- **Class prefix per package** — use Tailwind's `prefix: 'niuu-'` (or package-scoped
  prefix) so two packages can't collide when their CSS is concatenated on a host page.
- **No inline styles, no CSS-in-JS.** Tailwind + tokens covers the surface.
- **One-off component CSS is still fine** when a utility soup gets unwieldy — co-locate
  a `.css` file next to the component, use `@apply` against token-backed utilities.

### 7. Routing is code-based, not file-based

TanStack Router routes are constructed in code and composed by the Shell from
`PluginDescriptor.routes`. File-based routing cannot cross package boundaries, so it
is incompatible with composability. This is a deliberate trade.

### 8. Module boundaries — what goes where

| Live in `@niuulabs/ui`                             | Live in a specific plugin              |
| -------------------------------------------------- | -------------------------------------- |
| Used by 2+ plugins                                 | Used by only one plugin                |
| Design-system primitives (Chip, StateDot)          | WorkflowBuilder (ting), RunMesh (ting) |
| Cross-plugin composites (PersonaAvatar, MountChip) | TopologyCanvas (observatory)           |
| Layout/overlay/form/data primitives                | TemplateEditor (volundr)               |

**Promotion rule:** start plugin-local, promote to `@niuulabs/ui` as soon as a second
plugin needs it. Cheap to move.

Cross-plugin **domain** types (Persona, Mount, ToolRegistry, EventCatalog, Budget)
live in `@niuulabs/domain` (to be added when first needed) — not in a plugin.

---

## Layout

```
web-next/
├── pnpm-workspace.yaml
├── package.json                  workspace root (dev deps only)
├── tsconfig.base.json            shared TS config
├── tsconfig.json                 project references
├── vitest.config.ts              unit test config (root)
├── playwright.config.ts          e2e config (root)
├── eslint.config.js              flat config
├── packages/
│   ├── design-tokens/            @niuulabs/design-tokens
│   ├── plugin-sdk/               @niuulabs/plugin-sdk
│   ├── query/                    @niuulabs/query
│   ├── ui/                       @niuulabs/ui
│   ├── shell/                    @niuulabs/shell
│   └── plugin-hello/             @niuulabs/plugin-hello (smoke test)
├── apps/
│   └── niuu/                     @niuulabs/niuu — dev app
└── e2e/                          Playwright specs
```

### What each package owns

| Package                   | Role                                                                                                                |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `@niuulabs/design-tokens` | `tokens.css`, fonts (Inter + JetBrainsMono NF), `ThemeProvider`                                                     |
| `@niuulabs/plugin-sdk`    | `PluginDescriptor`, `ServicesProvider`, `ConfigProvider`, `FeatureCatalogProvider`, Zod config schema               |
| `@niuulabs/query`         | `createQueryClient()` with Niuu defaults                                                                            |
| `@niuulabs/ui`            | Shared primitives (Chip, StateDot, Rune, Kbd, LiveBadge today — grows)                                              |
| `@niuulabs/shell`         | `Shell` — rail/topbar/subnav/content/footer. Host-agnostic, reads config + feature catalog, renders enabled plugins |
| `@niuulabs/plugin-<name>` | One plugin = one package. Exports `<name>Plugin` + ports + domain types                                             |
| `@niuulabs/niuu` (app)    | Reference composition. Imports plugins, wires services, serves `/config.json`                                       |

---

## Stack

- **React 19** (StrictMode in dev)
- **TypeScript 5.7** — strict, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`
- **Vite 7** — app builder
- **pnpm 9** workspaces
- **TanStack Router** — code-based routes
- **TanStack Query** — server state caching
- **Zod** — runtime config validation
- **Vitest + React Testing Library** — unit tests, 85% coverage minimum
- **Playwright** — e2e
- **tsup** — library builds for each package (ESM only)
- **ESLint flat config + Prettier** — code style

## Explicitly not used

- **Monaco / `@codingame/monaco-vscode-*`** — dropped. File manager covers file ops.
  If read-only syntax-highlighted viewing is needed later, reach for `shiki` or
  `prism-react-renderer` (~50KB).
- **Vercel AI SDK UI** — we use the platform-owned `SessionChat/` implementation.
- **File-based routing** — see rule 7.
- **CSS-in-JS / styled-components / emotion** — runtime cost, not needed.
- **Tailwind as a _consumer_ dependency** — we use Tailwind _inside_ our packages at
  build time, but consumers just import the pre-compiled `styles.css`. No Tailwind
  install required on the host side.
- **ORM** — doesn't apply here (backend concern), but noted for consistency.

---

## Commands

```bash
pnpm install            # install workspace deps (first thing in a fresh clone)
pnpm dev                # run @niuulabs/niuu at :5173
pnpm test               # vitest run with coverage
pnpm test:watch         # vitest watch
pnpm test:e2e           # playwright test
pnpm typecheck          # project-reference-aware tsc
pnpm lint               # eslint
pnpm format             # prettier
pnpm build              # build all packages, then the app
```

## Authenticated services — tokens are already in the environment

This session is pre-authenticated for GitHub and Linear. If you hit an auth
failure calling `gh`, the GitHub API, or the Linear MCP tools, **the token is
already available — look before you ask**:

- **GitHub CLI** — `gh auth status` should show a logged-in account. `gh` reads
  from `~/.config/gh/hosts.yml` automatically. Git HTTPS operations use the same
  credentials.
- **GitHub API directly** — if you need to call the raw API with curl or from a
  script, the `gh` CLI is the simplest path (`gh api repos/...`) because it
  injects the token. Do not fabricate URLs or invent tokens.
- **Linear** — the MCP tools under `mcp__claude_ai_Linear__*` are already wired
  and use the session's Linear API key. No further setup needed. If a tool call
  fails, it's not an auth issue — re-read the error.

Never add a token to code, commit messages, `.env` files, or logs. If a token
ever shows up in a diff, the commit is wrong — the TruffleHog pre-commit hook
will block the push.

## Git hooks — install once per clone

The workspace ships a `.pre-commit-config.yaml` at the repo root that catches the
same errors CI would catch, before you round-trip to GitHub:

- **pre-commit** (runs on every `git commit`, ~2s): prettier + eslint auto-fix on
  staged files under `web-next/`
- **pre-push** (runs on every `git push`, ~30s): builds all packages, runs
  `pnpm typecheck`, `pnpm test` (coverage gate), and `pnpm format:check`

Install both hook types once:

```bash
# from the workspace root (not web-next/)
pre-commit install --hook-type pre-commit --hook-type pre-push
```

If pnpm isn't on your `PATH`, either `corepack enable` (recommended — ships with
Node 16.10+) or install pnpm globally. The hooks extend `PATH` to cover the
common install locations (`~/.npm-global/bin`, `~/.local/share/pnpm`,
`/opt/homebrew/bin`, `/usr/local/bin`).

**Do not skip `pre-push` with `--no-verify`.** If a hook fails, fix the issue.
Every failure a dev sees locally is a failure that would otherwise waste a CI
run and a round-trip.

## Coverage thresholds — non-negotiable

Configured in `vitest.config.ts`: **85% statements / branches / functions / lines**.

This is **a hard CI gate, not a suggestion.** `pnpm test` runs with coverage and
fails the run if any threshold falls below 85%. Do not lower the thresholds to get
a PR through. If coverage drops, write the tests.

- Every new component ships with at least one test that exercises rendering and
  any state or variant logic.
- Every new hook ships with tests that cover happy path, loading, and error states.
- Every new port/adapter ships with tests for the full contract surface.
- Bug fixes ship with a regression test.

CI runs `pnpm test` (unit, with coverage) AND `pnpm test:e2e` (Playwright) on every
push. Both gates must pass. Playwright is part of CI from the start — do not defer it.

Every new plugin page, feature flow, and shell interaction ships with a Playwright
spec in `e2e/`. Specs cover at least:

1. The happy path (user can reach the feature and see its core content)
2. Loading state (before data resolves)
3. One error state (service fails, empty state, or permission denied)
4. Keyboard accessibility where the feature has interactive controls (tab order,
   ⌘K opens, Escape closes, etc.)

---

## Runtime config shape

See `packages/plugin-sdk/src/config.ts` for the Zod schema. Example:

```json
{
  "theme": "ice",
  "plugins": {
    "observatory": { "enabled": true, "order": 1 },
    "ting": { "enabled": true, "order": 4 },
    "volundr": { "enabled": false, "order": 5, "reason": "k8s not provisioned" }
  },
  "services": {
    "ting": { "baseUrl": "https://api.niuu.world/ting", "mode": "http" },
    "volundr": { "baseUrl": "https://api.niuu.world/volundr", "mode": "http" }
  },
  "auth": {
    "issuer": "https://auth.niuu.world",
    "clientId": "niuu-web"
  }
}
```

Edit `apps/niuu/public/config.json` and refresh the browser. No rebuild.

---

## How to add a new plugin

1. `pnpm -F @niuulabs/plugin-sdk build` (or whatever you're branching off).
2. Create `packages/plugin-<name>/` mirroring `plugin-hello/` — `package.json`,
   `tsconfig.json`, `tsup.config.ts`, `src/{ports,adapters,domain,application,ui}`,
   `src/index.ts` exporting a `definePlugin({...})`.
3. Add the package name to `apps/niuu/package.json` deps and reference in
   `apps/niuu/tsconfig.json`.
4. Import and list it in `apps/niuu/src/plugins.ts`.
5. Wire its mock service in `apps/niuu/src/services.ts` (real adapter when ready).
6. Add to `apps/niuu/public/config.json` under `plugins` + `services`.
7. Write stories for every shared component you add.

---

## References

- Current plugin examples: `packages/plugin-hello/`, `packages/plugin-volundr/`, and `apps/niuu/`
- Project rules: `../.claude/rules/*.md`

---

## Common pitfalls

- **Don't import from `adapters/` in components.** Components depend on `ports/`
  through DI. If you see `import { buildTingAdapter } from '../adapters/...'` inside
  `ui/`, that's a bug.
- **Don't hard-code plugin IDs in the shell.** Shell reads `plugins` prop + config.
- **Don't hard-code colors.** Use Tailwind classes backed by tokens
  (`bg-bg-primary`, `text-text-secondary`, `border-border`) — never raw hex or
  arbitrary values like `bg-[#09090b]`. Red is reserved for failures — use the
  `critical` token (`bg-critical`, `text-critical`), never a brand color.
- **Don't add a component to a plugin if a sibling plugin already needs it.** Promote
  to `@niuulabs/ui` on the spot.
- **Don't bypass `ConfigProvider`.** All env-dependent values (URLs, flags, theme)
  come from config, not from `import.meta.env`.
- **Don't publish with amend.** Commits matter; each package has its own version.
