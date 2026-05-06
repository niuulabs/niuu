# Plan — Visual Parity with web2

**Visual test:** `e2e/visual/tyr.visual.spec.ts` → `tyr plan matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/tyr/plan.png`
**Web2 source:** `web2/niuu_handoff/tyr/design/pages.jsx` (PlanView, lines 692-947)
**Web-next source:** `packages/plugin-tyr/src/ui/PlanWizard.tsx`, `packages/plugin-tyr/src/ui/PlanPrompt.tsx`, `packages/plugin-tyr/src/ui/PlanQuestions.tsx`, `packages/plugin-tyr/src/ui/PlanRaiding.tsx`, `packages/plugin-tyr/src/ui/PlanDraft.tsx`, `packages/plugin-tyr/src/ui/PlanApproved.tsx`, `packages/plugin-tyr/src/ui/PlanGuidanceRail.tsx`

---

## Summary

The Plan wizard is structurally well-matched: 5-step flow (prompt, questions, raiding, draft, approved), StepDots progress indicator, 2-column layout on steps 1/2/4 (content left, guidance rail right), full-width on steps 3/5, and right-side guidance cards. The gaps are mostly cosmetic: hint chips on the prompt step, the "Your Brief" quote card on questions step, workflow template picker styling, sub-tasks table in draft, risk rows styling, and the "Re-plan" button in draft actions.

---

## Required changes

### 1. Add hint chips to Prompt step

**Web2 spec**: Below the textarea on the prompt step (line 769-771), there are clickable "hint chips" that pre-fill example prompts: `+ Example: subscription validation` and `+ Example: simple endpoint`. They use class `hint-chip`.

**Web-next currently**: `PlanPrompt.tsx` likely renders a textarea and a Continue button, but no hint chips for example prompts.

**What to do**: Add 2-3 clickable hint chips below the textarea that pre-fill the prompt input with example text. Style as small rounded chips with a `+` prefix, subtle background, and hover state.

**Files to modify:**

- `packages/plugin-tyr/src/ui/PlanPrompt.tsx` — add hint chip buttons
- Tests for PlanPrompt — verify chip click fills textarea

---

### 2. Add "Your Brief" quote card to Questions step

**Web2 spec**: The questions step (lines 791-794) shows a styled quote block above the questions: a box with `EYEBROW: "YOUR BRIEF"` header and the user's prompt text below in 12px.

**Web-next currently**: `PlanQuestions.tsx` likely shows questions directly without re-displaying the user's prompt.

**What to do**: Add a styled quote/callout card at the top of the questions form showing the user's original prompt. Style with subtle background, monospace eyebrow label "YOUR BRIEF", and the prompt text below.

**Files to modify:**

- `packages/plugin-tyr/src/ui/PlanQuestions.tsx` — add quote card (needs prompt passed as prop)
- `packages/plugin-tyr/src/ui/PlanWizard.tsx` — pass prompt to PlanQuestions if not already

---

### 3. Workflow template picker in Questions step

**Web2 spec**: The last question (`kind: 'workflow'`, lines 800-806) renders a 3-column grid of workflow template buttons. Each shows name, version, and stage count. Selected template gets an `on` class.

**Web-next currently**: Unknown if the workflow question is rendered or if it uses a different input type.

**What to do**: Ensure the workflow question renders as a 3-column grid of selectable template cards (not a text input). Each card shows the workflow name, version badge, and stage count.

**Files to modify:**

- `packages/plugin-tyr/src/ui/PlanQuestions.tsx` — add workflow picker grid
- `packages/plugin-tyr/src/ui/useWorkflows.ts` — reuse existing hook for template list

---

### 4. Sub-tasks table styling in Draft step

**Web2 spec**: The draft step (lines 918-937) renders sub-tasks in a grid layout: `PersonaAvatar | name+meta | size pill | "Own saga" button | delete button`. Size pills have status-colored classes (`size-S`, `size-M`, `size-L`).

**Web-next currently**: `PlanDraft.tsx` shows the draft plan. The sub-tasks section may not match the exact grid layout and size-pill styling.

**What to do**: Ensure the sub-tasks list matches: persona avatar, name with phase/persona/estimate meta line, size pill (S/M/L colored), promote-to-saga button, and remove button. Use Tailwind grid with 5 columns.

**Files to modify:**

- `packages/plugin-tyr/src/ui/PlanDraft.tsx` — match sub-task row layout and size pills

---

### 5. Risk rows styling

**Web2 spec**: Lines 898-907 show risk rows with a `risk-kind` badge (e.g., "blast", "untested") and the risk message. The kind badge uses a distinct warning/caution style.

**Web-next currently**: `PlanDraft.tsx` may render risks but possibly without the kind badge styling.

**What to do**: Ensure risk rows show a colored kind badge (using critical/warning token colors) followed by the risk message text.

**Files to modify:**

- `packages/plugin-tyr/src/ui/PlanDraft.tsx` — style risk rows with kind badges

---

### 6. Draft action bar — add "Re-plan" and "Save as draft" buttons

**Web2 spec**: Lines 940-946 show a draft action bar with: "Revise answers" (back), "Re-plan" (restart planning raid), spacer, "Save as draft", and "Approve & create saga" (primary). Five buttons total.

**Web-next currently**: `PlanDraft.tsx` likely has Back and Approve buttons. "Re-plan" and "Save as draft" may be missing.

**What to do**: Add "Re-plan" button (restarts the planning raid with same inputs) and "Save as draft" button (persists current state without creating the saga). Place them matching web2's layout: back left, re-plan left-center, save-draft right, approve right.

**Files to modify:**

- `packages/plugin-tyr/src/ui/PlanDraft.tsx` — add Re-plan and Save as draft buttons
- `packages/plugin-tyr/src/ui/usePlanWizard.ts` — add `replan()` and `saveDraft()` actions if missing

---

### 7. Raiding animation matches web2

**Web2 spec**: The raiding step (lines 838-846) shows a `<RaidingAnim/>` component (pulsing animation) and 3 lines of mono text showing what the ravens are doing: decomposer analyzing, investigator probing, mimir-indexer pulling.

**Web-next currently**: `PlanRaiding.tsx` shows a loading/progress state. The specific raven activity lines may be missing.

**What to do**: Ensure the raiding step shows descriptive raven activity text (3 lines with rune prefix) below the loading animation.

**Files to modify:**

- `packages/plugin-tyr/src/ui/PlanRaiding.tsx` — add raven activity text lines

---

## Shared components

- `Rune` — already in `@niuulabs/ui`
- `StepDots` — plugin-local in `packages/plugin-tyr/src/ui/StepDots.tsx`
- `PersonaAvatar` — already in `@niuulabs/ui`
- `PlanGuidanceRail` — plugin-local, already implemented

## Acceptance criteria

- [ ] Prompt step shows 2-3 clickable hint chips that pre-fill the textarea
- [ ] Questions step displays "YOUR BRIEF" quote card with the user's original prompt
- [ ] Workflow question renders as a 3-column grid of selectable template cards
- [ ] Draft sub-tasks use 5-column grid with persona avatar, name+meta, size pill, promote button, delete button
- [ ] Risk rows show colored kind badge (blast/untested) before message text
- [ ] Draft actions include "Re-plan" and "Save as draft" buttons in correct positions
- [ ] Raiding step shows 3 raven activity description lines with rune prefixes
- [ ] Visual regression test `tyr plan matches web2` passes
- [ ] 85%+ test coverage on all modified components
