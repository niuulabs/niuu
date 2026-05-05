# Ravns / Wardens (/mimir/ravns) — Visual Parity with web2

**Visual test:** `e2e/visual/mimir.visual.spec.ts` → `mimir ravns matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/mimir/ravns.png`
**Web2 source:** `web2/niuu_handoff/mimir/design/views.jsx` (RavnsView, RavnProfile)
**Web-next source:** `packages/plugin-mimir/src/ui/RavnsPage.tsx`, `packages/plugin-mimir/src/ui/RavnsPage.css`

---

## Summary

The Ravns page shows a warden directory (card grid) and a profile detail view. Key gaps compared to web2: missing bio text in directory cards, missing pages-touched metric, missing expertise chips in the profile view, and missing tools list in the profile. Web-next extras to keep: dream cycle stats in cards, `RavnAvatar` component, and the profile back-navigation.

---

## Required changes

### 1. Bio text in directory cards

**Web2 spec**: Each warden card shows a 1-2 line bio below the head row (name, state, persona). The bio is a brief description of the ravn's function (e.g. "Synthesises infrastructure documentation from git commits and runbooks").
**Web-next currently**: Cards show: avatar + identity (name, state) → role chip → mount chips → dream stats. No bio text.
**What to do:** Add a bio paragraph between the role chip row and the mount chips row. Source from `RavnBinding.bio` (extend domain model if the field does not exist). Style as `text-xs text-text-secondary` with 2-line clamp.
**Files to modify:** `packages/plugin-mimir/src/ui/RavnsPage.tsx`, `packages/plugin-mimir/src/domain/ravn-binding.ts`

### 2. Pages-touched metric in directory cards

**Web2 spec**: Each card shows `<N> pages touched` in a metrics row along with `last dream <timestamp>`.
**Web-next currently**: Cards show last dream cycle stats (pages updated, entities created, duration) but not a cumulative pages-touched count.
**What to do:** Add a metrics row below the mount chips showing: `<pagesTouched> pages touched` and `last dream <timestamp>`. These are aggregate lifetime stats, distinct from the single-cycle dream stats.
**Files to modify:** `packages/plugin-mimir/src/ui/RavnsPage.tsx`, `packages/plugin-mimir/src/domain/ravn-binding.ts`

### 3. Expertise chips in profile view

**Web2 spec**: The ravn profile shows an "Areas of expertise" panel with a flex-wrap row of accent-colored chips (e.g. "kubernetes", "networking", "observability").
**Web-next currently**: Profile shows mount bindings section and last dream stats section. No expertise section.
**What to do:** Add an "Areas of expertise" section to `RavnProfile` between mount bindings and dream stats. Render `ravn.expertise` (string array) as accent-toned `Chip` components. Extend `RavnBinding` domain model with `expertise: string[]` if not present.
**Files to modify:** `packages/plugin-mimir/src/ui/RavnsPage.tsx`, `packages/plugin-mimir/src/domain/ravn-binding.ts`

### 4. Tools list in profile view

**Web2 spec**: The profile shows the ravn's tool list in a mono-font row (e.g. "tools: search · compile · lint-fix").
**Web-next currently**: No tools display in the profile.
**What to do:** Add a tools row to the profile hero section (below the state pill). Show as mono text: "tools: " followed by tool names joined by " · ". Source from `RavnBinding.tools` (extend model if needed).
**Files to modify:** `packages/plugin-mimir/src/ui/RavnsPage.tsx`, `packages/plugin-mimir/src/domain/ravn-binding.ts`

### 5. Migrate RavnsPage.css to Tailwind

**Web2 spec**: N/A — code-quality requirement.
**Web-next currently**: Uses `RavnsPage.css` with BEM classes (`.ravns-page`, `.ravn-card`, `.ravn-profile`, etc.).
**What to do:** Replace all class-based styling with Tailwind utilities using `niuu-` prefix. Delete `RavnsPage.css`. Ensure the card grid uses `niuu-grid niuu-grid-cols-[repeat(auto-fill,minmax(280px,1fr))]` or similar.
**Files to modify:** `packages/plugin-mimir/src/ui/RavnsPage.tsx`, `packages/plugin-mimir/src/ui/RavnsPage.css` (delete)

---

## What to keep as-is

- `RavnAvatar` component with role-based rune and state-dot
- Directory card grid layout (auto-fill responsive)
- Profile back-navigation button
- Profile hero section with large avatar
- Mount bindings section in profile
- Last dream cycle stats (pages updated, entities created, lint fixes, duration)
- Loading state with `StateDot`
- Error state and empty state rendering
- Card click → profile navigation

## Shared components

- `Chip` from `@niuulabs/ui`
- `StateDot` from `@niuulabs/ui`
- `RavnAvatar` from `@niuulabs/ui`

## Acceptance criteria

1. Directory cards show bio text (truncated to 2 lines)
2. Directory cards show pages-touched count and last dream timestamp in a metrics row
3. Profile view includes an "Areas of expertise" section with chips
4. Profile view shows tools list in the hero area
5. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
6. `RavnsPage.css` is deleted
7. Visual test passes with ≤5% pixel diff against `e2e/__screenshots__/web2/mimir/ravns.png`
8. Card → profile navigation and back button remain functional
9. Dream cycle stats in cards and profile remain accurate
