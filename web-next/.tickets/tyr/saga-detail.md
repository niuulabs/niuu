# Saga Detail — Visual Parity with web2

**Visual test:** `e2e/visual/tyr.visual.spec.ts` → `tyr saga detail matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/tyr/sagas.png` (detail portion)
**Web2 source:** `web2/niuu_handoff/tyr/design/pages.jsx` (SagasView saga-detail section, lines 337-426)
**Web-next source:** `packages/plugin-tyr/src/ui/SagaDetailPage.tsx`

---

## Summary

The standalone Saga Detail route (`/tyr/sagas/:id`) shares the same underlying component as the embedded detail in Sagas List. This ticket covers the dedicated route version (accessed via direct URL). It inherits the same gaps as the sagas-list detail — missing workflow card, stage progress rail, confidence drift chart — plus additional parity items specific to the standalone view: the raid row layout differences, persona avatar display, and the overall visual density.

---

## Required changes

### 1. Raid row visual density — match web2's compact grid

**Web2 spec**: Each raid row in a phase uses a compact horizontal grid: `StatusDot | identifier | name | PersonaAvatar | StatusBadge | Confidence`. The identifier is rendered in a monospace `r-id` class. The whole row is flat — no button wrapper, no expandable behavior in the list view itself.

**Web-next currently**: Raid rows are rendered as full-width `<button>` elements with expand/collapse behavior (`aria-expanded`). They show `StatusBadge | name` on the left and `PersonaAvatars | ConfidenceBadge` on the right. No raid identifier is shown in the collapsed row.

**What to do**: Add the raid identifier (e.g., `NIU-214.2`) to each raid row, displayed in monospace text between the StatusBadge and the raid name. This matches web2's information density. The expand/collapse behavior is a web-next improvement and should remain, but the collapsed row should show all the metadata web2 shows.

**Files to modify:**

- `packages/plugin-tyr/src/ui/SagaDetailPage.tsx` — add `raid.trackerId` to collapsed row
- `packages/plugin-tyr/src/domain/saga.ts` — ensure `Raid` has `trackerId` field

---

### 2. Phase header — add StatusDot with pulse for active phases

**Web2 spec**: Phase headers include `<StatusDot status={ph.status} pulsing={ph.status==='active'}/> Phase {ph.number} · {ph.name}` — a pulsing dot for active phases provides immediate visual feedback.

**Web-next currently**: Phase headers show a `<span>Phase {phase.number}</span>` label, phase name, StatusBadge, and ConfidenceBadge. No StatusDot with pulse animation.

**What to do**: Add a `<StateDot state={phase.status} pulse={phase.status === 'active'} />` before the phase number label.

**Files to modify:**

- `packages/plugin-tyr/src/ui/SagaDetailPage.tsx` — add StateDot to phase header

---

### 3. Match web2 detail header structure

**Web2 spec**: The detail section header is: `<sec-head> {saga.identifier} · {saga.name} </sec-head>` followed by a branch/base subline: `<span className="eyebrow mono">{saga.branch} -> {saga.base}</span>`.

**Web-next currently**: The header shows a Rune glyph, saga name, StatusBadge, and ConfidenceBadge. The branch info is shown as `{saga.trackerId} · {saga.featureBranch} · Created {date}` which includes the date but not the base branch reference.

**What to do**: Add the base branch to the subline: `{saga.trackerId} · {saga.featureBranch} -> {saga.baseBranch}`. The `Saga` domain model needs a `baseBranch` field (defaulting to `main`).

**Files to modify:**

- `packages/plugin-tyr/src/domain/saga.ts` — add `baseBranch` field
- `packages/plugin-tyr/src/ui/SagaDetailPage.tsx` — update subline text
- Mock adapter — supply baseBranch values

---

### 4. Right-column cards (same as sagas-list ticket)

**Web2 spec**: Workflow card, Stage progress rail, Confidence drift chart — rendered in a right column.

**Web-next currently**: Not present. Single-column layout.

**What to do**: Same as sagas-list ticket items 1-4. When `SagaDetailPage` is used standalone (not embedded via `hideBackButton`), render the 2-column layout with the cards on the right.

**Files to modify:**

- Same as sagas-list ticket — the component is shared

---

### 5. "Create new saga" modal trigger

**Web2 spec**: The sagas page includes a "+ New saga" button that opens a modal asking if the user wants to go to Plan. The modal has Cancel/Go to Plan buttons.

**Web-next currently**: The "+ New Saga" button directly navigates to `/tyr/plan` without a confirmation modal.

**What to do**: Add a lightweight confirmation modal matching web2: "New sagas start from a prompt... Want to go there now?" with Cancel and "Go to Plan ->" buttons. Use a shared Modal component from `@niuulabs/ui` or create one if not available.

**Files to modify:**

- `packages/plugin-tyr/src/ui/SagasPage.tsx` — add modal state and render
- Possibly `packages/ui/src/Modal/` — if no modal exists yet

---

## Shared components

- `StateDot` — already in `@niuulabs/ui`
- `PersonaAvatar` — already in `@niuulabs/ui`
- `Sparkline` — already in `@niuulabs/ui`
- `Pipe` — already in `@niuulabs/ui`
- `Modal` — may need to be added to `@niuulabs/ui`
- `WorkflowCard`, `StageProgressRail`, `ConfidenceDriftCard` — plugin-local, covered in sagas-list ticket

## Acceptance criteria

- [ ] Raid rows display raid identifier in monospace between status and name
- [ ] Active phase headers show a pulsing StateDot
- [ ] Detail subline includes base branch reference (e.g., `feat/branch -> main`)
- [ ] Standalone detail view renders right-column cards (workflow, progress rail, confidence drift)
- [ ] New saga button triggers confirmation modal before navigating to Plan
- [ ] `Saga` domain model includes `baseBranch` and `workflow`/`workflowVersion` fields
- [ ] Visual regression test `tyr saga detail matches web2` passes
- [ ] All new/modified components maintain 85%+ test coverage
