# Templates — Visual Parity with web2

**Visual test:** `e2e/visual/volundr.visual.spec.ts` → `templates`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/volundr/templates.png`
**Web2 source:** `web2/niuu_handoff/volundr/design/pages.jsx`
**Web-next source:** `packages/plugin-volundr/src/ui/TemplatesPage.tsx`

---

## Summary

The Templates page is close to parity but missing description text and usage counts on template list cards, clone/edit action buttons in the template detail header, and the MCP tab incorrectly shows individual tools instead of MCP server definitions with their associated tool lists.

---

## Required changes

### 1. Description + usage count on template list cards

**Web2 spec**: Each template card in the list view shows: (1) template name (bold), (2) a 1-2 line description in muted text below the name, and (3) a usage count pill (`12 sessions`) in the bottom-right. The description is clamped to 2 lines with ellipsis overflow.
**Web-next currently**: Template cards show only the name and an icon/badge for the template category.
**What to do:** Add a description paragraph with `niuu-text-sm niuu-text-zinc-400 niuu-line-clamp-2 niuu-mt-1`. Add a usage count pill with `niuu-text-xs niuu-text-zinc-500 niuu-bg-zinc-800 niuu-px-2 niuu-py-0.5 niuu-rounded-full`. Source from `template.description` and `template.sessionCount`.
**Files to modify:**

- `packages/plugin-volundr/src/ui/TemplatesPage.tsx`
- `packages/plugin-volundr/src/ui/components/TemplateCard.tsx` (if exists)

### 2. Clone/edit action buttons in detail header

**Web2 spec**: When viewing a template's detail page, the header area (right-aligned) contains two action buttons: "Clone" (secondary/outline style) and "Edit" (primary style). Clone duplicates the template; Edit navigates to the template editor.
**Web-next currently**: The detail header shows the template name and metadata but has no action buttons.
**What to do:** Add a button group in the detail header. Use `@niuulabs/ui` `Button` component with `variant="outline"` for Clone and `variant="primary"` for Edit. Wrap in a flex container: `niuu-flex niuu-items-center niuu-gap-2 niuu-ml-auto`. Wire click handlers to the appropriate plugin actions/routes.
**Files to modify:**

- `packages/plugin-volundr/src/ui/TemplatesPage.tsx` (detail view section)
- `packages/plugin-volundr/src/ui/components/TemplateDetailHeader.tsx` (new or existing)

### 3. MCP tab: show server definitions, not individual tools

**Web2 spec**: The MCP tab in the template detail view lists MCP server definitions. Each server entry shows: server name, connection URL/command, and a collapsible list of tools that server exposes. Servers are grouped as cards with a disclosure triangle for the tool list.
**Web-next currently**: The MCP tab renders a flat list of individual tool names with no server grouping or connection metadata.
**What to do:** Refactor the MCP tab to group tools by their parent server. Render each server as a card with: server name (`niuu-font-medium niuu-text-zinc-200`), connection string (`niuu-text-xs niuu-font-mono niuu-text-zinc-500`), and a collapsible tool list using a disclosure/accordion pattern. Use `niuu-border niuu-border-zinc-800 niuu-rounded-lg niuu-p-4` for server cards. Data model may need updating to include `server` grouping — check if the API already provides this or if it needs to be derived from tool metadata.
**Files to modify:**

- `packages/plugin-volundr/src/ui/TemplatesPage.tsx` (MCP tab section)
- `packages/plugin-volundr/src/ui/components/McpServerCard.tsx` (new)
- `packages/plugin-volundr/src/ui/components/McpTab.tsx` (new or existing)

---

## What to keep as-is

| Element                                                | Reason                                    |
| ------------------------------------------------------ | ----------------------------------------- |
| Template list grid layout                              | Already matches web2 spacing and columns  |
| Category filter tabs (All, Coding, Research, etc.)     | Already correct                           |
| Template detail tab bar (Overview, MCP, System Prompt) | Structure matches, only MCP content wrong |
| Template icon/avatar display                           | Already matches web2                      |
| Search input in list header                            | Already present and styled correctly      |

## Shared components

| Component                  | Source                                                          |
| -------------------------- | --------------------------------------------------------------- |
| `Button`                   | `@niuulabs/ui` — use for Clone/Edit actions                     |
| `Badge` / `Pill`           | `@niuulabs/ui` — use for usage count display                    |
| `Disclosure` / `Accordion` | Check `@niuulabs/ui`; if absent, build plugin-local collapsible |
| `McpServerCard`            | Plugin-local — Volundr-specific MCP display                     |

## Acceptance criteria

1. Template list cards display a 2-line-clamped description below the name
2. Template list cards show a usage count pill (e.g. "12 sessions") in the card footer
3. Template detail header includes Clone (outline) and Edit (primary) action buttons
4. MCP tab groups tools under their parent MCP server definition
5. Each MCP server card shows name, connection string, and a collapsible tool list
6. All styling uses Tailwind with `niuu-` prefix — no CSS modules, no inline styles, no hard-coded hex values
7. Visual test passes with <=5% pixel diff against web2 baseline
8. No regressions to existing passing visual tests
