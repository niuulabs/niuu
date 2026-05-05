# Workflows — Visual Parity with web2

**Visual test:** `e2e/visual/tyr.visual.spec.ts` → `tyr workflows matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/tyr/workflows.png`
**Web2 source:** `web2/niuu_handoff/tyr/design/workflow_builder.jsx`
**Web-next source:** `packages/plugin-tyr/src/ui/WorkflowBuilderPage.tsx`, `packages/plugin-tyr/src/ui/WorkflowBuilder/`

---

## Summary

The Workflows page is unique: web2 has no dedicated "workflows page" — it only has a workflow builder used within the subnav. Web-next has a full `WorkflowBuilderPage` with tabs for switching workflows, plus the `WorkflowBuilder` component featuring GraphView, PipelineView, LibraryPanel, NodeInspector, ValidationPanel, and YamlView. The primary issue is that the page component (`WorkflowBuilderPage.tsx`) uses **inline styles exclusively** instead of Tailwind classes. Since web-next is the source of truth for this page (no web2 equivalent to match), the work is purely a styling migration.

---

## Required changes

### 1. Migrate WorkflowBuilderPage from inline styles to Tailwind

**Web2 spec**: N/A — web-next is the source of truth for this page's design.

**Web-next currently**: `WorkflowBuilderPage.tsx` uses `style={{ ... }}` for all layout: `display: 'flex'`, `flexDirection: 'column'`, `height: '100%'`, `fontFamily: 'var(--font-sans)'`, `background: 'var(--color-bg-primary)'`, padding values, borders, gap, alignment, colors, and font sizes. This violates CLAUDE.md rule 6 ("No inline styles... Tailwind + tokens covers the surface").

**What to do**: Replace all inline `style={{ ... }}` props with equivalent Tailwind utility classes using the `niuu-` prefix. Examples:

- `style={{ display: 'flex', flexDirection: 'column', height: '100%' }}` becomes `className="niuu-flex niuu-flex-col niuu-h-full"`
- `style={{ padding: '12px 20px', borderBottom: '1px solid var(--color-border)' }}` becomes `className="niuu-px-5 niuu-py-3 niuu-border-b niuu-border-border"`
- `style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)' }}` becomes `className="niuu-text-base niuu-font-semibold niuu-text-text-primary"`

**Files to modify:**

- `packages/plugin-tyr/src/ui/WorkflowBuilderPage.tsx` — replace all inline styles with Tailwind
- `packages/plugin-tyr/src/ui/WorkflowBuilderPage.test.tsx` — update any snapshot tests

---

### 2. Migrate workflow tab buttons from inline styles to Tailwind

**Web2 spec**: N/A.

**Web-next currently**: The workflow tab buttons use inline style objects with conditional logic: `background: isActive ? 'var(--color-bg-elevated)' : 'transparent'`, `border`, `borderRadius`, `padding`, `fontSize`, `color`, `cursor`, `fontFamily`.

**What to do**: Use Tailwind classes with `cn()` utility for conditional styling:

```tsx
className={cn(
  'niuu-rounded niuu-px-3 niuu-py-1 niuu-text-xs niuu-cursor-pointer niuu-font-sans niuu-border niuu-transition-colors',
  isActive
    ? 'niuu-bg-bg-elevated niuu-border-border niuu-text-text-primary'
    : 'niuu-bg-transparent niuu-border-transparent niuu-text-text-muted',
)}
```

**Files to modify:**

- `packages/plugin-tyr/src/ui/WorkflowBuilderPage.tsx` — refactor tab button styles

---

### 3. Migrate loading/error/empty states from inline styles to Tailwind

**Web-next currently**: Loading, error, and empty states use inline styles for flex centering, gap, colors, and font sizes.

**What to do**: Replace with Tailwind:

- Loading: `className="niuu-flex-1 niuu-flex niuu-items-center niuu-justify-center niuu-gap-2 niuu-text-text-secondary niuu-text-sm"`
- Error: same with `niuu-text-critical`
- Empty: same with `niuu-text-text-muted`

**Files to modify:**

- `packages/plugin-tyr/src/ui/WorkflowBuilderPage.tsx`

---

### 4. Audit WorkflowBuilder sub-components for inline styles

**Web-next currently**: The `WorkflowBuilder/` directory contains 5 view components (GraphView, PipelineView, LibraryPanel, NodeInspector, ValidationPanel, YamlView) plus the main `WorkflowBuilder.tsx`. These may also use inline styles.

**What to do**: Audit each sub-component for inline styles and migrate to Tailwind. If a component is too complex for pure utilities (e.g., canvas positioning, dynamic transforms), a co-located `.css` file using `@apply` is acceptable per CLAUDE.md.

**Files to modify:**

- `packages/plugin-tyr/src/ui/WorkflowBuilder/WorkflowBuilder.tsx`
- `packages/plugin-tyr/src/ui/WorkflowBuilder/GraphView.tsx`
- `packages/plugin-tyr/src/ui/WorkflowBuilder/PipelineView.tsx`
- `packages/plugin-tyr/src/ui/WorkflowBuilder/LibraryPanel.tsx`
- `packages/plugin-tyr/src/ui/WorkflowBuilder/NodeInspector.tsx`
- `packages/plugin-tyr/src/ui/WorkflowBuilder/ValidationPanel.tsx`
- `packages/plugin-tyr/src/ui/WorkflowBuilder/YamlView.tsx`

---

### 5. Add "New workflow" / "Delete workflow" actions

**Web2 spec**: The workflow builder in web2 supports creating and managing multiple templates. The subnav shows a template list with counts.

**Web-next currently**: Tabs exist for switching between workflows, but there is no button to create a new workflow or delete an existing one.

**What to do**: Add a "+ New" button in the page header (next to workflow tabs) and a delete action in the workflow tab context. New workflow creates a blank template; delete removes the selected workflow.

**Files to modify:**

- `packages/plugin-tyr/src/ui/WorkflowBuilderPage.tsx` — add New/Delete buttons
- `packages/plugin-tyr/src/ui/useWorkflows.ts` — add create/delete mutations if needed

---

## Shared components

- `StateDot` — already in `@niuulabs/ui`
- `cn()` — already available from `@niuulabs/ui`

## Acceptance criteria

- [ ] Zero inline `style={{ }}` props in `WorkflowBuilderPage.tsx`
- [ ] All workflow tab buttons use Tailwind classes with conditional logic via `cn()`
- [ ] Loading, error, and empty states use Tailwind classes
- [ ] WorkflowBuilder sub-components audited and migrated (inline styles removed)
- [ ] "+ New" workflow button present in page header
- [ ] No regression in visual appearance (same look, just different implementation)
- [ ] Visual regression test `tyr workflows matches web2` passes
- [ ] 85%+ test coverage maintained on modified files
- [ ] `pnpm lint` passes (no Tailwind arbitrary values like `bg-[#...]`)
