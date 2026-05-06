# Milestone: Visual Parity — web-next matches web2 prototypes

**Project:** Niuu Web — Composable Plugin UI
**Tracking:** 33 tickets across 6 plugins
**Validation:** `pnpm test:visual` — currently **15/33 pass** (≤5% pixel diff)
**Target:** all 33 pass
**Last updated:** 2026-04-21

---

## How to run

```bash
cd web-next

# 1. Capture web2 baselines (only needed after web2 prototype changes)
pnpm capture-baselines

# 2. Copy baselines into Playwright snapshot dirs
bash scripts/setup-visual-baselines.sh

# 3. Run visual comparison
pnpm test:visual

# 4. View diff report
pnpm exec playwright show-report
```

---

## Progress log

| Date           | Pass/Total | Key changes                                                                                                         |
| -------------- | ---------- | ------------------------------------------------------------------------------------------------------------------- |
| Apr 20 (start) | 0/35       | App was blank (broken plugin-tyr export). Tests had wrong selectors.                                                |
| Apr 20 (mid)   | 33/33      | Fixed app render, test selectors, but baselines were self-referential (not web2)                                    |
| Apr 20 (late)  | 10/33      | Switched baselines to web2 screenshots. Real comparison.                                                            |
| Apr 21 (early) | 16/33      | Tyr subnav route-awareness, Mimir /pages + /sources routes added                                                    |
| Apr 21 (mid)   | 15/33      | Fixed 27 missing CSS imports in @niuulabs/ui (KpiCard, Table, etc.). Ravn overview regressed slightly from new CSS. |

## Root causes found and fixed

1. **App wouldn't render at all** — `plugin-tyr` dist was stale, missing `buildTyrAuditLogHttpAdapter` export. Fixed by rebuilding packages.
2. **Web2 baselines were unstyled** — Tyr prototype's `styles.css?v=4` query string caused 404. Fixed with URL query-string stripping in HTTP server.
3. **Web2 Mimir baselines showed Observatory** — Flokk shell defaulted to Observatory plugin. Fixed with explicit rail click to activate Mimir.
4. **CORS blocking web2 prototype rendering** — Babel's XHR-based JSX loading blocked on `file://`. Fixed by serving via HTTP.
5. **Tyr Settings subnav on all pages** — `subnav: () => SettingsRail()` was unconditional. Fixed with route-aware `TyrSubnav` wrapper.
6. **Mimir /pages and /sources routes missing** — PagesView and SourcesView were internal tabs without dedicated routes. Visual tests 404'd. Fixed by adding routes.
7. **27 CSS files missing from @niuulabs/ui build** — KpiStrip, Table, LifecycleBadge, PersonaAvatar, MountChip, all chat components etc. existed in source but weren't imported in `styles.css` entrypoint. Pages rendered without card/table/badge styling. Fixed.
8. **Live clock in web2 baselines** — UTC clock changed every capture. Fixed by hiding clock elements before screenshots.

---

## Ticket inventory

### Login (1 ticket) — NIU-698

| Page       | Status   | Gaps remaining                                          |
| ---------- | -------- | ------------------------------------------------------- |
| Login page | **PASS** | OAuth buttons added, footer added, build metadata added |

### Observatory (4 tickets) — NIU-699 to NIU-702

| Page                 | Status      | Gaps remaining                                       |
| -------------------- | ----------- | ---------------------------------------------------- |
| Canvas               | **PASS**    | —                                                    |
| Registry Types       | **FAIL 6%** | Type display layout (rows vs cards), inspector width |
| Registry Containment | **FAIL 6%** | Hint box styling, tree indentation                   |
| Registry JSON        | **PASS**    | —                                                    |

### Ravn (5 tickets) — NIU-703 to NIU-707

| Page     | Status      | Gaps remaining                                   |
| -------- | ----------- | ------------------------------------------------ |
| Overview | **FAIL 6%** | Slight regression from CSS fix — content density |
| Ravens   | **PASS**    | —                                                |
| Sessions | **PASS**    | —                                                |
| Budget   | **PASS**    | —                                                |
| Personas | **PASS**    | —                                                |

### Tyr (7 tickets) — NIU-708 to NIU-714

| Page        | Status      | Gaps remaining                                    |
| ----------- | ----------- | ------------------------------------------------- |
| Dashboard   | **FAIL 7%** | Content density (1 saga vs 4), event feed section |
| Sagas list  | **FAIL 6%** | Workflow card, stage rail, confidence drift chart |
| Saga detail | **FAIL 6%** | Same as sagas list detail                         |
| Dispatch    | **FAIL 6%** | Content density, recent dispatches data           |
| Workflows   | **PASS**    | —                                                 |
| Plan        | **PASS**    | —                                                 |
| Settings    | **FAIL 6%** | Content density in section cards                  |

### Mimir (11 tickets) — NIU-715 to NIU-726

| Page         | Status      | Gaps remaining                                    |
| ------------ | ----------- | ------------------------------------------------- |
| Overview     | **FAIL 7%** | Content density (fewer mounts/ravns in mock data) |
| Pages tree   | **FAIL 7%** | Sidebar width (220 vs 260px), panel proportions   |
| Pages reader | **PASS**    | —                                                 |
| Sources      | **PASS**    | —                                                 |
| Search       | **FAIL 6%** | Empty results (no pre-populated search in mock)   |
| Graph        | **PASS**    | —                                                 |
| Entities     | **PASS**    | —                                                 |
| Ravns        | **FAIL 6%** | Content density in warden cards                   |
| Lint         | **FAIL 7%** | KPI accent colors, rule description styling       |
| Routing      | **FAIL 6%** | Table row density, minor spacing                  |
| Dreams       | **FAIL 6%** | Activity log layout vs dream cycles               |

### Volundr (5 tickets) — NIU-725 to NIU-730

| Page           | Status       | Gaps remaining                                                       |
| -------------- | ------------ | -------------------------------------------------------------------- |
| Forge overview | **FAIL 10%** | KPI cards now styled (CSS fix), but fewer pods/clusters in mock data |
| Templates      | **PASS**     | —                                                                    |
| Clusters       | **FAIL 12%** | Fewer clusters in mock data, resource panel layout                   |
| Sessions       | **FAIL 6%**  | Left subnav tree vs top tabs, search filter                          |
| Session chat   | **PASS**     | —                                                                    |

---

## Completed infrastructure tickets

| Ticket  | Title                                            | Status   |
| ------- | ------------------------------------------------ | -------- |
| NIU-695 | Shared UI components — promote and create        | **Done** |
| NIU-696 | Shell layout gaps — topbar, tabs, footer, subnav | **Done** |
| NIU-697 | Design token gaps                                | **Done** |

---

## What's driving the remaining 18 failures

The 18 failing pages are ALL at 6-12% diff (threshold 5%). The gaps fall into two categories:

### 1. Mock data density (affects ~12 pages)

Web2 prototypes bake in rich seed data (8 pods, 6 clusters, 4 sagas, 7 search results).
Web-next mock adapters return minimal data (2 pods, 2 clusters, 1 saga, 0 search results).
This makes pages look emptier even though the layout and styling are correct.

**Fix:** Enrich mock adapters to return web2-equivalent data density.

### 2. Layout proportions (affects ~6 pages)

Minor grid/panel width differences:

- Mimir pages sidebar: 220px vs 260px
- Registry types: row list vs card grid
- Observatory containment: hint box styling
- Volundr sessions: top tabs vs left sidebar

**Fix:** Per-page CSS tweaks matching web2 proportions.

---

## Non-negotiable rules (from CLAUDE.md)

Every implementation MUST follow:

- **Tailwind with `niuu-` prefix** for all styling (rule 6)
- **Design tokens** for all colors/spacing/typography
- **Hexagonal architecture** — UI imports from ports only (rule 2)
- **DI via `useService<T>(key)`** — never import concrete services (rule 3)
- **85% test coverage** — unit tests for every new component
- **Playwright e2e** — visual test must pass with ≤5% pixel diff
