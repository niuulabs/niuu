# Personas — Visual Parity with web2

**Visual test:** `e2e/visual/ravn.visual.spec.ts` → `ravn personas matches web2`
**Status:** FAIL (web2 has 873 lines of rich editor UI; web-next has the structure but several sub-components need enrichment — syntax highlighting in YAML tab, fan-in strategy cards, full subscription visualization)
**Web2 baseline:** `e2e/__screenshots__/web2/ravn/personas.png`
**Web2 source:** `web2/niuu_handoff/ravn/design/personas.jsx`, `web2/niuu_handoff/ravn/design/personas-editor.css`
**Web-next source:** `packages/plugin-ravn/src/ui/PersonasPage.tsx`, `packages/plugin-ravn/src/ui/PersonaForm.tsx`, `packages/plugin-ravn/src/ui/PersonaYaml.tsx`, `packages/plugin-ravn/src/ui/PersonaSubs.tsx`, `packages/plugin-ravn/src/ui/PersonaList.tsx`

---

## Summary

The Personas page has the correct split-pane structure (subnav persona list grouped by role, detail pane with 3 tabs: Form/YAML/Subs). The PersonaForm is substantially implemented (6 sections: identity, LLM, tool access, produces, consumes, fan-in, plus mimir write routing and iteration budget). However, web2 has richer form interactions (fan-in strategy cards with visual descriptions, YAML syntax highlighting, and a more detailed subscription visualization), and several visual polish items are missing.

---

## Required changes

### 1. Add YAML syntax highlighting

**Web2 spec** (personas.jsx YamlTab): The YAML tab shows the persona definition with full syntax highlighting — keys in cyan, strings in green, numbers in amber, booleans in purple, comments in muted gray. Uses a monospace font with line numbers in the gutter.
**Web-next currently**: `PersonaYaml.tsx` renders YAML as a plain `<pre>` with monospace text in a single muted color. No syntax highlighting, no line numbers.
**What to do:**

1. Integrate `shiki` (lightweight syntax highlighter, recommended in CLAUDE.md over Monaco) for YAML highlighting.
2. Create a `HighlightedYaml` component that takes a YAML string and renders it with token-based coloring using the Niuu dark theme (map shiki token colors to design tokens).
3. Add a line-numbers gutter on the left side.
4. Keep the component read-only (no editing in this tab — editing is done via the Form tab).
   **Files to modify:**

- `packages/plugin-ravn/src/ui/PersonaYaml.tsx`
- `packages/plugin-ravn/package.json` (add `shiki` dependency)

### 2. Add fan-in strategy cards with visual descriptions

**Web2 spec** (personas.jsx FanInSection): Instead of a plain `<select>` dropdown, web2 renders fan-in strategies as selectable cards. Each card shows: strategy name, a 1-line description, and a small SVG diagram illustrating the merge behavior (e.g., arrows converging for "all_must_pass", single arrow for "first_wins", weighted bars for "weighted_score").
**Web-next currently**: Fan-in section has a simple `<select>` dropdown with strategy names. No descriptions, no visual cards.
**What to do:**

1. Replace the `<select>` with a grid of selectable strategy cards (`.rv-fanin-card`).
2. Each card: strategy name (bold), 1-line description text, and a small inline SVG (32x20) illustrating the pattern.
3. Selected card gets a highlighted border (`var(--brand-400)`).
4. Add descriptions object mapping each strategy to its explanation:
   - `all_must_pass`: "All upstream events must arrive before processing"
   - `any_passes`: "First arriving event triggers processing"
   - `quorum`: "N of M events must arrive"
   - `merge`: "All events merged into a single context"
   - `first_wins`: "First event wins, others discarded"
   - `weighted_score`: "Events scored and ranked by weight"
5. When a strategy that takes params is selected (e.g., quorum, weighted_score), show a params editor below the cards (quorum count, weight sliders).
   **Files to modify:**

- `packages/plugin-ravn/src/ui/PersonaForm.tsx` (Fan-in section)
- `packages/plugin-ravn/src/ui/PersonaForm.css` (or Tailwind classes for cards)

### 3. Enrich subscription visualization with interactivity

**Web2 spec** (personas.jsx SubsTab): The subscription graph in web2 is interactive: hovering a node highlights its connected edges, clicking a node navigates to that persona's detail, edges show event payload size indicators, and a legend explains the color coding. There is also a "zoom to fit" button and the graph auto-layouts based on node count.
**Web-next currently**: `PersonaSubs.tsx` renders a static SVG graph with nodes and cubic bezier edges. No hover states, no click navigation, no legend, no payload indicators, no zoom control.
**What to do:**

1. Add hover interactivity: hovering a node dims unconnected edges and nodes.
2. Add click behavior: clicking a non-focus node dispatches `ravn:persona-selected` event to navigate.
3. Add a legend below the SVG explaining: producer (left), focus (center, highlighted), consumer (right), edge = event name.
4. Add a "Zoom to fit" button that resets the viewport (useful when graph is large).
5. Add edge payload indicators — small text showing schema field count or byte estimate on each edge.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/PersonaSubs.tsx`
- `packages/plugin-ravn/src/ui/PersonaSubs.css` (or inline styles to Tailwind)

### 4. Add system prompt template section to form

**Web2 spec** (personas.jsx FormTab): The Identity section includes a "System prompt template" field — a tall textarea (8+ rows) with monospace font, showing the Jinja2/handlebars template that becomes the persona's system prompt. It has subtle syntax highlighting for `{{variables}}` using a regex-based inline highlight.
**Web-next currently**: `PersonaForm.tsx` has `systemPromptTemplate` in the form state (it is in `detailToRequest`) but there is no visible field for it in the rendered form. The Identity section only shows name, role, letter, summary, and description.
**What to do:**

1. Add a "System prompt" field in the Identity section (or as a new "Prompt" section below Identity).
2. Render as a tall monospace textarea (rows={8}).
3. Add inline highlighting for `{{variable}}` patterns: wrap matches in a `<mark>` or similar with brand color. This can be a controlled div with contenteditable=false overlay, or simply a highlighted preview below the textarea.
4. Add a character/token count indicator below the textarea.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/PersonaForm.tsx`

### 5. Polish persona list subnav styling

**Web2 spec** (personas.jsx PersonaList): The subnav list in web2 has: role group headers with a colored left border accent per role category, persona rows with a larger avatar (24px), and a "new persona" button at the bottom with a dashed border. Selected persona has a left border indicator (3px brand color) in addition to background highlight.
**Web-next currently**: `PersonaList.tsx` has role headers (uppercase mono text) and persona rows with `PersonaAvatar` (20px). No colored role borders, no left-border selection indicator, no "new persona" button.
**What to do:**

1. Add colored left border to role group headers (each role gets a unique accent color from the palette).
2. Change avatar size from 20 to 24.
3. Add left border indicator (3px `var(--brand-400)`) to the selected persona row.
4. Add a "New persona" button at the bottom of the list with dashed border styling.
   **Files to modify:**

- `packages/plugin-ravn/src/ui/PersonaList.tsx`

---

## Shared components

- `PersonaAvatar`, `MountChip`, `EventPicker`, `SchemaEditor`, `ToolPicker`, `ValidationSummary` — from `@niuulabs/ui` (already used)
- `Rune` — from `@niuulabs/ui` (used in empty state)
- PersonaForm, PersonaYaml, PersonaSubs, PersonaList — plugin-local
- Syntax highlighting (shiki) — new dependency for YAML tab

## Acceptance criteria

1. YAML tab renders persona definition with syntax highlighting (keys, strings, numbers, booleans in distinct colors) and line numbers.
2. Fan-in section shows selectable strategy cards with name, description, and illustrative SVG diagram instead of a plain dropdown.
3. Subscription graph has hover interactivity (dim unconnected nodes), click navigation, legend, and edge payload indicators.
4. System prompt template field is visible in the form with monospace textarea, `{{variable}}` highlighting, and character count.
5. Persona list subnav has colored role borders, 24px avatars, left-border selection indicator, and "New persona" button.
6. Visual regression test `ravn personas matches web2` passes within acceptable diff threshold.
7. Unit tests cover YAML highlighting rendering, fan-in card selection, subscription graph interactions, system prompt field, and persona list enhancements.
8. Coverage remains at or above 85%.
