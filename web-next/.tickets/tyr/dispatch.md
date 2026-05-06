# Dispatch — Visual Parity with web2

**Visual test:** `e2e/visual/tyr.visual.spec.ts` → `tyr dispatch matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/tyr/dispatch.png`
**Web2 source:** `web2/niuu_handoff/tyr/design/pages.jsx` (DispatchView, lines 443-690)
**Web-next source:** `packages/plugin-tyr/src/ui/DispatchView.tsx`

---

## Summary

The Dispatch page is well-implemented in web-next: split layout (queue left, rules right), segmented filter with 4 tabs, batch dispatch bar, saga group headers, raid rows with feasibility gate chips, and a search input. Web-next adds improvements over web2 (feasibility gate chips with tooltips, confidence bar visualization). Three modals from web2 are missing: workflow override, threshold override, and edit rules. The right panel also lacks the "Recent dispatches" data and the "Edit" button for rules.

---

## Required changes

### 1. Add "Apply workflow" modal

**Web2 spec**: When raids are selected, clicking "Apply workflow..." opens a modal (`modal === 'apply-workflow'`) showing a list of available workflow templates. Each template row shows name, summary/stage count, and version. Clicking a template applies it as an override to the selected raids' sagas.

**Web-next currently**: The `BatchDispatchBar` has an `onApplyWorkflow` prop but it is not wired — it is passed as `undefined`. No modal exists.

**What to do**: Create a `WorkflowOverrideModal` component that:

- Lists available workflows from `useWorkflows()`
- Shows name, version, stage count for each
- On selection, applies the workflow override to the sagas of selected raids
- Renders with a Cancel button

Wire it into `DispatchView` with state management (`showWorkflowModal`).

**Files to modify:**

- `packages/plugin-tyr/src/ui/WorkflowOverrideModal.tsx` — new component
- `packages/plugin-tyr/src/ui/WorkflowOverrideModal.test.tsx` — tests
- `packages/plugin-tyr/src/ui/DispatchView.tsx` — add modal state, wire `onApplyWorkflow`

---

### 2. Add "Override threshold" modal

**Web2 spec**: The threshold modal (lines 657-671) shows:

- Description text about what the threshold does
- A range slider (0 to 1, step 0.05) with current value displayed prominently
- Cancel and Apply buttons
- On apply, updates the local threshold state

**Web-next currently**: The `BatchDispatchBar` has an `onOverrideThreshold` prop but it is not wired.

**What to do**: Create a `ThresholdOverrideModal` with a slider input and the same UX as web2. On apply, update local dispatcher state (or call a service method).

**Files to modify:**

- `packages/plugin-tyr/src/ui/ThresholdOverrideModal.tsx` — new component
- `packages/plugin-tyr/src/ui/ThresholdOverrideModal.test.tsx` — tests
- `packages/plugin-tyr/src/ui/DispatchView.tsx` — add modal state, wire `onOverrideThreshold`

---

### 3. Add "Edit rules" modal with full rules form

**Web2 spec**: The right panel's dispatch rules card has an "Edit" button (line 560) that opens an `EditRulesModal` (lines 673-689) with inputs for: confidence threshold, max concurrent raids, auto-continue toggle, retry count. Cancel and Save buttons.

**Web-next currently**: The `DispatchRulesPanel` renders rules as read-only `dl` items. No edit button, no modal.

**What to do**: Add an "Edit" button to the rules card header. Create an `EditRulesModal` with form inputs matching web2. On save, update local state (and optionally call `IDispatchBus.updateRules()` if the port supports it).

**Files to modify:**

- `packages/plugin-tyr/src/ui/EditRulesModal.tsx` — new component
- `packages/plugin-tyr/src/ui/EditRulesModal.test.tsx` — tests
- `packages/plugin-tyr/src/ui/DispatchView.tsx` — add Edit button to rules panel, modal state

---

### 4. Populate "Recent dispatches" panel with data

**Web2 spec**: The right column's "Recent dispatches" card (lines 569-585) shows a list of recent dispatch events: raid identifier, workflow name, and timestamp. Web2 uses 5 hardcoded entries.

**Web-next currently**: The panel renders "No recent dispatches." as a placeholder.

**What to do**: Add mock recent dispatch data (matching the web2 pattern) and render it as a list of rows with: id-chip, workflow name, timestamp. In the future this will come from a service call.

**Files to modify:**

- `packages/plugin-tyr/src/ui/DispatchView.tsx` — add mock data and render in `DispatchRulesPanel`

---

### 5. Add "Pause dispatcher" button

**Web2 spec**: The dispatch header area (line 483) includes a `"Pause dispatcher"` button that would toggle the dispatcher on/off state.

**Web-next currently**: No pause button exists. The header shows threshold and concurrent chips but no toggle control.

**What to do**: Add a "Pause dispatcher" / "Resume dispatcher" button in the dispatch header. Wire it to toggle `dispatcherState.enabled` through the dispatch bus port.

**Files to modify:**

- `packages/plugin-tyr/src/ui/DispatchView.tsx` — add pause button in header
- `packages/plugin-tyr/src/ports/index.ts` — ensure `IDispatchBus` has a `togglePause()` method

---

### 6. Toast notifications for dispatch actions

**Web2 spec**: Dispatching raids shows `flash("Dispatched N raids")`. Applying workflow shows `flash("Applied 'ship' to N raids")`. Threshold change shows `flash("Threshold -> 0.80")`.

**Web-next currently**: No toast feedback on any dispatch action.

**What to do**: Wire toast notifications (from the shared Toast component in dashboard ticket) to dispatch, workflow-apply, threshold-change, and rules-save actions.

**Files to modify:**

- `packages/plugin-tyr/src/ui/DispatchView.tsx` — add toast triggers on actions

---

## Shared components

- `StateDot` — already in `@niuulabs/ui`
- `StatusBadge` — already in `@niuulabs/ui`
- `ConfidenceBar` — already in `@niuulabs/ui`
- `Tooltip`, `TooltipProvider` — already in `@niuulabs/ui`
- `Toast` — shared, covered in dashboard ticket
- `Modal` — may need to be added to `@niuulabs/ui`

## Acceptance criteria

- [ ] "Apply workflow" modal opens on batch bar button click, lists templates, applies override
- [ ] "Override threshold" modal shows slider (0-1, step 0.05), applies new threshold
- [ ] "Edit rules" modal opens from rules panel Edit button, saves all 4 fields
- [ ] Recent dispatches panel shows 5 mock entries (id, workflow, time)
- [ ] Pause/Resume dispatcher button visible in header, toggles state
- [ ] Toast notifications fire on dispatch, workflow-apply, threshold-change, rules-save
- [ ] All new modals have Cancel + primary action buttons, dismiss on backdrop click
- [ ] Visual regression test `tyr dispatch matches web2` passes
- [ ] 85%+ test coverage on all new and modified files
