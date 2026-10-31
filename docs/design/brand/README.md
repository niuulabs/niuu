# NIUU brand

The identity is **Continuity**, brand system v0.4: [BRAND-GUIDELINES.md](BRAND-GUIDELINES.md)
is the source document and `assets/` holds the masters. Change a master here, then
update the copies listed below.

## Product names

The interface and public docs use product names. Implementation names stay in
APIs, routes, config keys, packages, commands, and Helm charts. A rename here
never changes those.

| Product | Line | Implementation |
|---|---|---|
| Platform | Your environment | Guild (`guild`) |
| Workspaces | Where work happens | Realms (`realms`) |
| Agents | Who does the work | Ravn (`ravn`) |
| Forge | Build and execute | Völundr (`volundr`) |
| Memory | What is known | Mímir (`mimir`) |
| Observatory | What is happening | Observatory (`observatory`) |

Work (Ting), AI models (Bifröst) and Resident console (Valkyrie) keep their plain
names; they sit outside the six.

Two terms overlap, and both stay:

- **Session workspace** is a Forge session's filesystem. Use the full term, never
  bare "workspace", where the Workspaces product could be meant.
- A **realm** in Observatory and cluster views is a network zone, not a Workspace.

Prose says "Niuu". "NIUU" appears only as the logo and its accessible name.

## Where the brand lives

| Surface | File |
|---|---|
| Colours and themes | The `niuu` theme in `web-next/packages/design-tokens/src/tokens.css` |
| Mark and wordmark in the web UI | `web-next/packages/ui/src/primitives/NiuuMark/` |
| Web favicon | `web-next/apps/niuu/public/favicon.svg` (copy of `niuu-favicon.svg`) |
| Docs site | `docs/site/stylesheets/niuu.css`, `docs/site/images/niuu-mark.svg`, `docs/site/images/niuu-favicon.svg` |
| Docs landing page | Copy in `docs/site/index.md`, markup in `docs/overrides/home.html`, styles in `docs/site/stylesheets/home.css` |
| CLI TUI | `src/cli/tui/theme.py`, `src/cli/tui/theme.tcss` |

## Themes

`niuu` is the default. The other themes (`xteo`, `ice`, `amber`, `spring`, `light`) keep
their own colours, so a brand change belongs in the `niuu` block and never in `:root`.
Only the logo strokes are shared. Hard-coded colours outside the tokens are written as `var(--niuu-x, #original)`: only `niuu` defines those names, so every other
theme falls back to the original.

## Fonts

`Inter-Light.woff2` is `inter-latin-300-normal.woff2` from `@fontsource/inter` 5.3.0
(SIL Open Font License 1.1). It holds Latin only, and the package's `Inter Display`
is not shipped, so headlines use Inter Light.

## Additions to the package

The v0.4 package did not ship these; they are built from its masters.

- `niuu-lockup-dark.svg`: the lockup for light backgrounds, using the
  `niuu-orbit-dark` and `niuu-wordmark-dark` colours.
- `niuu-favicon.svg`: the orbit with heavier strokes (13/17), enlarged 1.2x, on no
  background. The masters blur at 16px and vanish on a light tab bar, so a
  `prefers-color-scheme` rule picks deep blue strokes on light tabs and light blue
  on dark ones. Browsers that ignore that rule get the deep blue strokes.
- `--color-text-faint` (`#648090`), `--brand-100` to `--brand-900`: the package
  names no fourth text level and no ramp, so these are derived around Signal.
- `--color-text-on-critical` is dark: white on Danger is only 2.43:1.

## Status

The package describes itself as a concept. Trademark screening and rights
clearance are open and should finish before a public launch.
