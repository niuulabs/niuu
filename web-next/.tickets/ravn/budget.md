# Budget — Visual Parity with web2

**Visual test:** `e2e/visual/ravn.visual.spec.ts` → `ravn budget matches web2`
**Status:** FAIL (missing runway bar visualization, missing burn projection, missing fleet sparkline chart, missing per-ravn sparklines in drivers, missing apply-cap button in recommendations)
**Web2 baseline:** `e2e/__screenshots__/web2/ravn/budget.png`
**Web2 source:** `web2/niuu_handoff/ravn/design/overview.jsx` (budget section), `web2/niuu_handoff/ravn/design/data.jsx`
**Web-next source:** `packages/plugin-ravn/src/ui/BudgetView.tsx`, `packages/plugin-ravn/src/application/budgetAttention.ts`

---

## Summary

The Budget page has the correct structural sections (hero, attention columns, top drivers, recommendations, fleet table) but is missing several visual elements from web2: the runway bar is a plain number instead of a visual progress bar, there is no burn-rate projection or trend line, the fleet sparkline chart (1200x140) is absent, per-ravn sparklines are missing from the top drivers section, and recommendations lack actionable "Apply cap" buttons.

---

## Required changes

### 1. Add runway bar visualization to hero card

**Web2 spec** (overview.jsx HeroCard): Below the three KPIs (spent, cap, runway), web2 shows a horizontal runway bar — a visual representation of remaining budget as a colored track (green fading to amber as it depletes). It includes a projected depletion time label ("~4.2h remaining at current rate").
**Web-next currently**: Hero card shows three KPIs and a `BudgetBar` (spent vs cap). The runway value is displayed as a plain dollar amount. No visual runway bar, no projection text.
**What to do:**

1. Add a runway bar component below the BudgetBar — a track showing time-to-exhaustion visually (full width = 24h, filled portion = estimated remaining hours).
2. Compute burn rate from current spend and elapsed time.
3. Display projection text: "~Xh remaining at current rate" or "Will exceed cap by HH:MM" if on track to breach.
4. Color the runway bar: green when > 50% remaining, amber when 20-50%, red when < 20%.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/BudgetView.tsx` (HeroCard)
- `packages/plugin-ravn/src/ui/BudgetView.css` (or co-located CSS)
- `packages/plugin-ravn/src/application/budgetAttention.ts` (add burn-rate / projection calculation)

### 2. Add fleet sparkline chart (1200x140)

**Web2 spec** (overview.jsx): Between the attention columns and the top drivers table, web2 shows a large fleet sparkline chart (1200px wide, 140px tall) displaying fleet-wide spend over the last 24 hours. It has an x-axis (time labels: 24h ago ... now) and a y-axis (dollar amounts). The line is filled with a gradient below it.
**Web-next currently**: No fleet sparkline chart in the budget view. The Overview page has one, but the Budget page does not.
**What to do:**

1. Add a fleet sparkline section between attention columns and top drivers.
2. Use the `Sparkline` component from `@niuulabs/ui` with `width={1200}` and `height={140}`.
3. Add axis labels: x-axis (24h ago, 18h, 12h, 6h, now), y-axis (dollar values).
4. Feed it hourly fleet spend data (similar to the Overview page's `generateHourlySpend` but from actual data or more realistic seed).
5. Wrap in a section card with header "Fleet spend (24h)".
   **Files to modify:**

- `packages/plugin-ravn/src/ui/BudgetView.tsx`
- `packages/plugin-ravn/src/ui/BudgetView.css`

### 3. Add per-ravn sparklines to top drivers table

**Web2 spec** (overview.jsx TopDrivers): Each driver row shows: persona name, a mini sparkline (80x20) showing that ravn's spend trend over 24h, the share percentage bar, the percentage text, and the dollar amount.
**Web-next currently**: Driver rows show: persona name, a share bar (div with width%), percentage text, and dollar amount. No per-ravn sparkline.
**What to do:**

1. Add a `Sparkline` (from `@niuulabs/ui`) to each driver row, placed between the name and the share bar.
2. Size: `width={80} height={20}`.
3. Feed per-ravn hourly spend data (can be seeded/derived from the ravn's current spend for now).
4. Adjust the grid layout of `.rv-budget-driver-row` to accommodate the new sparkline column.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/BudgetView.tsx` (TopDriversTable)
- `packages/plugin-ravn/src/ui/BudgetView.css`

### 4. Add actionable recommendations with "Apply cap" button

**Web2 spec** (overview.jsx Recommendations): Each recommendation row shows: persona name, attention badge (colored), message text, and an action button ("Apply cap" for over-cap, "Suspend" for idle, "Reduce budget" for burning-fast). Buttons are styled as small outlined pill buttons.
**Web-next currently**: Recommendation rows show: persona name and message text. No action buttons, no attention badge styling beyond a `data-attention` attribute.
**What to do:**

1. Add a colored attention badge to each recommendation row (red for over-cap, amber for burning-fast, muted for idle).
2. Add an action button per recommendation: "Apply cap" (over-cap), "Reduce budget" (burning-fast), "Suspend" (idle).
3. Style buttons as small outlined pills (`.rv-budget-rec-action`).
4. Wire button clicks to dispatch actions (can be no-op stubs initially, wired to port later).
   **Files to modify:**

- `packages/plugin-ravn/src/ui/BudgetView.tsx` (RecommendedChanges)
- `packages/plugin-ravn/src/ui/BudgetView.css`

### 5. Add burn-rate trend and projection calculation

**Web2 spec** (overview.jsx): The hero section includes a "Burn trend" indicator showing whether spend is accelerating, steady, or decelerating (with an arrow icon and percentage change). This informs the runway projection and the "will-exceed" / "accelerating" attention classification.
**Web-next currently**: `budgetAttention.ts` classifies ravens into attention categories but does not compute burn rate or trend. The `budgetRunway` function returns a simple (cap - spent) value, not a time-based projection.
**What to do:**

1. Extend `budgetAttention.ts` with:
   - `burnRate(budget, elapsedHours)`: returns $/hour rate.
   - `projectedDepletion(budget, rate)`: returns hours until cap breach.
   - `burnTrend(currentRate, previousRate)`: returns 'accelerating' | 'steady' | 'decelerating'.
2. Use these in the hero card to show trend arrow and projection.
3. Use burn rate in attention classification to improve "burning-fast" detection.
   **Files to modify:**

- `packages/plugin-ravn/src/application/budgetAttention.ts`
- `packages/plugin-ravn/src/ui/BudgetView.tsx` (HeroCard)
- `packages/plugin-ravn/src/ui/BudgetView.css`

---

## Shared components

- `BudgetBar`, `Sparkline`, `StateDot` — from `@niuulabs/ui` (already used or available)
- Runway bar visualization — could be a variant of `BudgetBar` or a new plugin-local component
- Fleet sparkline chart section — plugin-local
- Recommendation action buttons — plugin-local

## Acceptance criteria

1. Hero card includes a visual runway bar showing time-to-exhaustion with color coding (green/amber/red) and projection text.
2. Fleet sparkline chart (1200x140) appears between attention columns and top drivers with axis labels.
3. Top drivers table rows include a per-ravn mini sparkline (80x20) showing 24h spend trend.
4. Recommendation rows show colored attention badges and actionable buttons (Apply cap / Reduce budget / Suspend).
5. Burn rate and projection are computed and displayed in the hero card with trend indicator (arrow + percentage).
6. Visual regression test `ravn budget matches web2` passes within acceptable diff threshold.
7. Unit tests cover runway bar, fleet sparkline, per-ravn sparklines, recommendation actions, and burn-rate calculations.
8. Coverage remains at or above 85%.
