# Settings — Visual Parity with web2

**Visual test:** `e2e/visual/tyr.visual.spec.ts` → `tyr settings matches web2`
**Status:** FAIL
**Web2 baseline:** `e2e/__screenshots__/web2/tyr/settings.png`
**Web2 source:** `web2/niuu_handoff/tyr/design/settings_plugin.jsx` (TYR_SECTIONS, lines 71-190)
**Web-next source:** `packages/plugin-tyr/src/ui/settings/SettingsPage.tsx`, `packages/plugin-tyr/src/ui/settings/PersonasSection.tsx`, `packages/plugin-tyr/src/ui/settings/FlockConfigSection.tsx`, `packages/plugin-tyr/src/ui/settings/DispatchDefaultsSection.tsx`, `packages/plugin-tyr/src/ui/settings/NotificationsSection.tsx`, `packages/plugin-tyr/src/ui/settings/AuditLogSection.tsx`

---

## Summary

Web2's Tyr settings surface has 8 sections: General, Dispatch rules, Integrations, Persona overrides, Gates & reviewers, Notifications, Advanced, plus 2 Niuu-level sections (Workspace, Appearance). Web-next has 5 sections: Personas, Flock Config, Dispatch Defaults, Notifications, Audit Log. Three Tyr sections are missing (General, Integrations, Gates & reviewers) and one is missing entirely (Advanced). The naming also differs — web2 uses "Dispatch rules" vs web-next's "Dispatch Defaults", and web2's "Persona overrides" vs web-next's "Personas". The cross-plugin aggregated nature of settings (where each plugin contributes sections) is designed in web2 but not yet implemented in web-next.

---

## Required changes

### 1. Add "General" section

**Web2 spec**: `tyr.general` section (lines 73-85) shows core service bindings:

- Service URL: `https://tyr.niuu.internal`
- Event backbone: `sleipnir · nats`
- Knowledge store: `mimir · qdrant:/niuu`
- Default workflow: `tpl-ship v1.4.2`

Each displayed as a key-value row with monospace values.

**Web-next currently**: No General section exists.

**What to do**: Create a `GeneralSection` component that renders read-only KV rows for service bindings. Data comes from runtime config or a mock. Add it to the settings navigation and page switch.

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/GeneralSection.tsx` — new component
- `packages/plugin-tyr/src/ui/settings/GeneralSection.test.tsx` — tests
- `packages/plugin-tyr/src/ui/settings/SettingsPage.tsx` — add to section list and switch

---

### 2. Add "Integrations" section

**Web2 spec**: `tyr.integrations` section (lines 103-127) shows a list of integration cards:

- Linear (connected), GitHub (connected), Jira (disconnected), Slack (connected), PagerDuty (disconnected)
- Each card has: logo letter, name, status line (api key ending or "not connected"), and Connect/Disconnect button

**Web-next currently**: No Integrations section exists.

**What to do**: Create an `IntegrationsSection` component showing integration cards in a list. Each card shows the integration name, connection status, and a toggle button. Use mock data for the 5 integrations.

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/IntegrationsSection.tsx` — new component
- `packages/plugin-tyr/src/ui/settings/IntegrationsSection.test.tsx` — tests
- `packages/plugin-tyr/src/ui/settings/SettingsPage.tsx` — add to section list and switch

---

### 3. Add "Gates & Reviewers" section

**Web2 spec**: `tyr.gates` section (lines 150-162) shows gate reviewer routing:

- List of reviewers (emails) with routing rules: "all gates · auto-forward after 30m"
- Simple KV layout

**Web-next currently**: No Gates & Reviewers section exists.

**What to do**: Create a `GatesReviewersSection` showing a list of reviewer email addresses with their routing configuration. Read-only display with mock data.

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/GatesReviewersSection.tsx` — new component
- `packages/plugin-tyr/src/ui/settings/GatesReviewersSection.test.tsx` — tests
- `packages/plugin-tyr/src/ui/settings/SettingsPage.tsx` — add to section list and switch

---

### 4. Add "Advanced" section (danger zone)

**Web2 spec**: `tyr.advanced` section (lines 178-189) shows destructive actions:

- Flush queue: danger button
- Reset dispatcher: danger button
- Rebuild confidence scores: normal button

**Web-next currently**: No Advanced section exists.

**What to do**: Create an `AdvancedSection` with danger-zone actions. Buttons should use critical/danger styling. Consider adding a confirmation dialog before destructive actions.

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/AdvancedSection.tsx` — new component
- `packages/plugin-tyr/src/ui/settings/AdvancedSection.test.tsx` — tests
- `packages/plugin-tyr/src/ui/settings/SettingsPage.tsx` — add to section list and switch

---

### 5. Align section naming with web2

**Web2 spec**: Section labels are "General", "Dispatch rules", "Integrations", "Persona overrides", "Gates & reviewers", "Notifications", "Advanced".

**Web-next currently**: Labels are "Personas", "Flock Config", "Dispatch Defaults", "Notifications", "Audit Log".

**What to do**: Rename to match web2 where the concepts overlap:

- "Personas" -> "Persona overrides" (matches web2 label)
- "Dispatch Defaults" -> "Dispatch rules" (matches web2 label)
- "Flock Config" — keep as-is (web2 doesn't have an equivalent; it's a web-next addition)
- "Audit Log" — keep as-is (web2 doesn't have an equivalent; it's a web-next addition)

Update the `SECTION_ITEMS` array labels and descriptions accordingly.

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/SettingsPage.tsx` — rename labels in SECTION_ITEMS

---

### 6. Add editable form inputs to Dispatch rules section

**Web2 spec**: The dispatch rules section (lines 88-100) shows editable inputs:

- Confidence threshold: `<input defaultValue="0.70">`
- Max concurrent raids: `<input defaultValue="5">`
- Auto-continue phases: `<Switch on={true}>`
- Retry on fail: `<input defaultValue="2">`
- Quiet hours: `<input defaultValue="22:00-07:00 UTC">`
- Escalate after (review): `<input defaultValue="30m">`

**Web-next currently**: `DispatchDefaultsSection.tsx` exists but may show read-only values or only a subset of fields.

**What to do**: Ensure all 6 fields from web2 are present with editable inputs. Add "Quiet hours" and "Escalate after" fields if missing. Use Switch component for auto-continue toggle.

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/DispatchDefaultsSection.tsx` — add missing fields, ensure editable inputs
- Tests — update to cover all fields

---

### 7. Persona overrides — add budget and model chips

**Web2 spec**: Lines 135-148 show persona rows with: `PersonaAvatar | name + produces line | budget chip | model chip | Edit button`. Each persona has a budget value (e.g., "budget 40") and model (e.g., "model · sonnet-4.5") displayed as chips.

**Web-next currently**: `PersonasSection.tsx` shows personas but may not include budget/model chips or an Edit button.

**What to do**: Add budget and model chip badges to each persona row, plus an "Edit" button on the right. Data comes from persona config (mock values acceptable).

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/PersonasSection.tsx` — add budget/model chips + Edit button
- Tests — verify chip rendering

---

### 8. Settings navigation order matches web2

**Web2 spec**: Section order is: General, Dispatch rules, Integrations, Persona overrides, Gates & reviewers, Notifications, Advanced.

**Web-next currently**: Order is Personas, Flock Config, Dispatch Defaults, Notifications, Audit Log.

**What to do**: Reorder SECTION_ITEMS to: General, Dispatch rules (renamed), Integrations, Persona overrides (renamed), Gates & reviewers, Flock Config, Notifications, Advanced, Audit Log. Web-next additions (Flock Config, Audit Log) go after the web2 sections.

**Files to modify:**

- `packages/plugin-tyr/src/ui/settings/SettingsPage.tsx` — reorder SECTION_ITEMS
- `packages/plugin-tyr/src/ui/settings/SettingsRail.tsx` — ensure nav renders in correct order

---

## Shared components

- `PersonaAvatar` — already in `@niuulabs/ui`
- `Switch` — may need to be added to `@niuulabs/ui` (or use a local toggle component)
- `cn()` — already in `@niuulabs/ui`

## Acceptance criteria

- [ ] General section shows 4 KV rows for service bindings
- [ ] Integrations section shows 5 integration cards with connect/disconnect state
- [ ] Gates & Reviewers section shows reviewer list with routing rules
- [ ] Advanced section shows 3 action buttons (2 danger, 1 normal)
- [ ] Section labels match web2 naming (Persona overrides, Dispatch rules, etc.)
- [ ] Dispatch rules section has all 6 editable fields (threshold, concurrent, auto-continue, retry, quiet hours, escalate after)
- [ ] Persona rows show budget and model chips + Edit button
- [ ] Navigation order matches web2 (General first, web-next additions last)
- [ ] All new sections have 85%+ test coverage
- [ ] Visual regression test `tyr settings matches web2` passes
- [ ] Settings page type union updated to include new section IDs
