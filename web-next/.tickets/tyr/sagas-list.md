# Sagas List — Visual Parity with web2

**Visual test:** `e2e/visual/tyr.visual.spec.ts` → `tyr sagas list matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/tyr/sagas.png`
**Web2 source:** `web2/niuu_handoff/tyr/design/pages.jsx` (SagasView, lines 266-441)
**Web-next source:** `packages/plugin-tyr/src/ui/SagasPage.tsx`

---

## Summary

The Sagas List uses a left panel + right detail split layout in both web2 and web-next. Web-next's left panel is functionally complete (search, status filter tabs, saga rows with glyph/name/meta/pipe). The right detail panel embeds `SagaDetailPage` which shows phases and raids. However, web2's detail panel includes three additional cards on the right side: a workflow card, a stage progress rail, and a confidence drift sparkline chart. These are absent in web-next.

---

## Required changes

### 1. Add workflow card to saga detail (right column)

**Web2 spec**: The saga detail right column (lines 363-386) includes a "Workflow" card showing:

- Applied workflow name + version chip
- Description line ("qa -> pre-ship review -> version bump -> release PR")
- Info row about override capability
- Flock section showing persona avatars for the workflow's participants

**Web-next currently**: `SagaDetailPage.tsx` renders phases and raids in a single column. There is no workflow card. The saga domain model likely does not carry workflow/version info.

**What to do**: Add a `WorkflowCard` component rendered in the saga detail view. It should show the applied workflow name, version, description, and participating personas. This requires extending the `Saga` domain model with `workflow` and `workflowVersion` fields, and the mock adapter should supply values.

**Files to modify:**

- `packages/plugin-tyr/src/domain/saga.ts` — add `workflow?: string`, `workflowVersion?: string` fields
- `packages/plugin-tyr/src/ui/SagaDetailPage.tsx` — add WorkflowCard in a right-column layout
- `packages/plugin-tyr/src/ui/SagaDetailPage.test.tsx` — test new card rendering
- Mock adapter — supply workflow data

---

### 2. Add stage progress rail to saga detail

**Web2 spec**: Lines 389-406 render a "Stage progress" card with numbered dots connected by bars. Complete stages have filled dots and colored bars. Active stage is highlighted. Labels appear below the rail.

**Web-next currently**: The detail page shows a `Pipe` component (linear colored cells) but not a vertical/horizontal progress rail with numbered step dots and connecting bars.

**What to do**: Create a `StageProgressRail` component showing numbered dots (1, 2, 3...) with connecting bars between them. Each dot is colored by status (complete = filled, active = pulsing/highlighted, pending = gray). Labels appear below. Render it in the saga detail right column.

**Files to modify:**

- `packages/plugin-tyr/src/ui/StageProgressRail.tsx` — new component
- `packages/plugin-tyr/src/ui/StageProgressRail.test.tsx` — tests
- `packages/plugin-tyr/src/ui/SagaDetailPage.tsx` — integrate StageProgressRail

---

### 3. Add confidence drift sparkline chart to saga detail

**Web2 spec**: Lines 408-425 render a "Confidence drift" card containing:

- A sparkline showing how confidence has moved over time as raids reported back
- A description paragraph
- A footer row showing start confidence, current confidence, scope_adherence, and test coverage

**Web-next currently**: No confidence history chart exists. The detail page only shows a static `ConfidenceBadge` for the current value.

**What to do**: Add a `ConfidenceDriftCard` that renders a Sparkline of historical confidence values (from the mock adapter), plus the footer metrics. The mock data can use a deterministic sine-based array (matching web2's pattern).

**Files to modify:**

- `packages/plugin-tyr/src/ui/ConfidenceDriftCard.tsx` — new component
- `packages/plugin-tyr/src/ui/ConfidenceDriftCard.test.tsx` — tests
- `packages/plugin-tyr/src/ui/SagaDetailPage.tsx` — integrate card

---

### 4. Convert detail to 2-column layout (content left, cards right)

**Web2 spec**: The saga detail area uses a 2-column layout: left column shows phases/raids, right column shows the three cards (workflow, stage progress, confidence drift). Implemented as `.saga-detail { display: grid; grid-template-columns: 1fr 320px; }`.

**Web-next currently**: `SagaDetailPage` uses a single-column `niuu-p-6 niuu-space-y-6` layout.

**What to do**: Restructure the detail page layout into a 2-column grid. The left column contains header + phases/raids. The right column contains the three cards (workflow, stage progress, confidence drift) stacked vertically.

**Files to modify:**

- `packages/plugin-tyr/src/ui/SagaDetailPage.tsx` — wrap content in 2-column grid

---

### 5. Add toast notification for export action

**Web2 spec**: The export button triggers `flash("Exported N sagas")` which shows a toast notification.

**Web-next currently**: The Export button in SagasPage triggers a download but shows no feedback.

**What to do**: After the Toast component is available (see dashboard ticket), wire the export action to show a toast confirming the export.

**Files to modify:**

- `packages/plugin-tyr/src/ui/SagasPage.tsx` — add toast on export

---

## Shared components

- `Sparkline` — already in `@niuulabs/ui`
- `Pipe` — already in `@niuulabs/ui`
- `PersonaAvatar` — already in `@niuulabs/ui`
- `StatusBadge`, `ConfidenceBadge` — already in `@niuulabs/ui`
- `StageProgressRail` — new, plugin-local (only Tyr uses it)
- `Toast` — shared, covered in dashboard ticket

## Acceptance criteria

- [ ] Saga detail right column shows workflow card with name, version, description, personas
- [ ] Stage progress rail renders numbered dots with connecting bars, colored by phase status
- [ ] Confidence drift card shows sparkline of historical confidence + footer metrics
- [ ] Detail area uses 2-column grid layout matching web2
- [ ] Export action triggers a toast notification
- [ ] All new components have 85%+ test coverage
- [ ] Visual regression test `tyr sagas list matches web2` passes
