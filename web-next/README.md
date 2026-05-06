# niuu-web

Composable plugin-based UI for the Niuu platform.

## Structure

```
packages/
  design-tokens/   @niuulabs/design-tokens   CSS tokens + ThemeProvider + fonts
  ui/              @niuulabs/ui              Shared primitives & composites
  plugin-sdk/      @niuulabs/plugin-sdk      PluginDescriptor, ServicesProvider, ConfigProvider
  shell/           @niuulabs/shell           Plugin shell (rail, topbar, subnav, content, footer)
  query/           @niuulabs/query           TanStack Query client factory
  plugin-hello/    @niuulabs/plugin-hello    Smoke-test plugin proving the composition loop
apps/
  niuu/            @niuulabs/niuu            Main dev app — mounts plugins, serves /config.json
```

## Commands

```bash
pnpm install            # Install workspace deps
pnpm dev                # Run the niuu app
pnpm storybook          # Run Storybook at :6006
pnpm test               # Run unit tests with coverage
pnpm test:e2e           # Run Playwright e2e
pnpm typecheck          # Type-check all packages
pnpm lint               # ESLint
pnpm format             # Prettier
```

## Runtime config

Feature flags and service URLs come from `apps/niuu/public/config.json`, fetched by
`ConfigProvider` on boot. Edit and refresh — no rebuild.

## Composability

Consumers install the packages they want and mount `<Shell plugins={[…]} />`. Each
plugin exports a `PluginDescriptor` and its port interfaces; consumers inject service
adapters via `<ServicesProvider>`.

See `apps/niuu/src/main.tsx` for the reference wiring.
