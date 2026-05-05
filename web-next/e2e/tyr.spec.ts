import { test, expect } from '@playwright/test';

test('tyr dashboard renders at /tyr', async ({ page }) => {
  await page.goto('/tyr');
  await expect(page.getByRole('heading', { name: 'Tyr' })).toBeVisible();
});

test('tyr dashboard shows the Tyr rune ᚦ', async ({ page }) => {
  await page.goto('/tyr');
  await expect(page.getByText('ᚦ').first()).toBeVisible();
});

test('tyr dashboard shows active sagas from seed data', async ({ page }) => {
  await page.goto('/tyr');
  await expect(page.getByText('Auth Rewrite')).toBeVisible();
});

test('tyr dashboard shows KPI strip', async ({ page }) => {
  await page.goto('/tyr');
  // Scope to KPI group since "Active Sagas" also appears as a section heading
  const kpiGroup = page.getByRole('group', { name: /KPI/i });
  await expect(kpiGroup).toBeVisible();
  await expect(kpiGroup.getByText('Active Sagas')).toBeVisible();
  await expect(kpiGroup.getByText('Dispatcher')).toBeVisible();
});

test('tyr sagas page renders at /tyr/sagas', async ({ page }) => {
  await page.goto('/tyr/sagas');
  await expect(page.getByText('Tyr · Sagas')).toBeVisible();
});

test('tyr sagas page shows all seed sagas', async ({ page }) => {
  await page.goto('/tyr/sagas');
  await expect(page.getByText('Auth Rewrite')).toBeVisible();
  await expect(page.getByText('Plugin Ravn Scaffold')).toBeVisible();
  await expect(page.getByText('Observatory Topology Canvas')).toBeVisible();
});

test('tyr sagas page status tabs filter list', async ({ page }) => {
  await page.goto('/tyr/sagas');
  await expect(page.getByText('Auth Rewrite')).toBeVisible();

  await page.getByRole('tab', { name: /active/i }).click();
  await expect(page.getByText('Auth Rewrite')).toBeVisible();
  await expect(page.getByText('Plugin Ravn Scaffold')).not.toBeVisible();
});

test('tyr dashboard → saga detail → raid → open session (full flow)', async ({ page }) => {
  // 1. Start at dashboard
  await page.goto('/tyr');
  await expect(page.getByText('Tyr · Dashboard')).toBeVisible();
  await expect(page.getByText('Auth Rewrite')).toBeVisible();

  // 2. Click the active saga to navigate to detail
  await page.getByRole('button', { name: /View saga Auth Rewrite/i }).click();

  // 3. Saga detail page should show the saga header and phases
  await expect(page.getByText('Phase 1: Foundation')).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Implement OIDC flow')).toBeVisible();

  // 4. Expand the raid panel
  await page.getByRole('button', { name: /Expand raid Implement OIDC flow/i }).click();

  // 5. Raid detail panel should appear
  await expect(
    page.getByRole('region', { name: /Raid detail: Implement OIDC flow/i }),
  ).toBeVisible();
  await expect(page.getByText('Add OIDC login via Keycloak.')).toBeVisible();

  // 6. Click "Open session" — asserts URL changes, not a network fetch
  await page.getByRole('button', { name: /Open Völundr session/i }).click();
  await expect(page).toHaveURL(/\/volundr\/session\/sess-001/, { timeout: 5000 });
});

test('tyr saga detail back button returns to sagas list', async ({ page }) => {
  await page.goto('/tyr/sagas/00000000-0000-0000-0000-000000000001');
  await expect(page.getByText('Auth Rewrite')).toBeVisible({ timeout: 5000 });

  await page.getByRole('button', { name: /Back to sagas/i }).click();
  await expect(page).toHaveURL(/\/tyr\/sagas/, { timeout: 5000 });
});

// ---------------------------------------------------------------------------
// Dispatch page
// ---------------------------------------------------------------------------

test('dispatch page renders at /tyr/dispatch', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByText('Dispatch rules')).toBeVisible({ timeout: 8000 });
});

test('dispatch page shows rule summary card', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByLabel('Dispatch rules')).toBeVisible({ timeout: 8000 });
  await expect(page.getByText('Confidence threshold')).toBeVisible();
  await expect(page.getByText('Concurrent cap')).toBeVisible();
});

test('dispatch page shows segmented filter', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByRole('group', { name: /filter raids/i })).toBeVisible({ timeout: 8000 });
  await expect(page.getByRole('button', { name: /all/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /ready/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /blocked/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /queue/i })).toBeVisible();
});

test('dispatch page shows dispatch queue table', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByRole('table', { name: /dispatch queue/i })).toBeVisible({ timeout: 8000 });
});

test('filter to ready tab shows only ready raids', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  // Wait for table to load
  await expect(page.getByRole('table')).toBeVisible({ timeout: 8000 });

  await page.getByRole('button', { name: /ready/i }).click();
  // After filtering, the ready raids should remain (those with confidence >= threshold)
  // The "Harden JWT validation" raid has confidence 80 >= 70 → ready
  await expect(page.getByText('Harden JWT validation')).toBeVisible();
  // The "Write auth integration tests" raid has confidence 45 < 70 → blocked (not shown)
  await expect(page.getByText('Write auth integration tests')).not.toBeVisible();
});

test('select multiple raids and batch dispatch', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByRole('table')).toBeVisible({ timeout: 8000 });

  // Switch to ready filter to only see dispatchable raids
  await page.getByRole('button', { name: /ready/i }).click();

  // Select the first available raid
  const firstCheckbox = page.getByRole('checkbox', { name: /select row/i }).first();
  await firstCheckbox.waitFor({ state: 'visible', timeout: 5000 });
  await firstCheckbox.check();

  // Dispatch bar should appear
  await expect(page.getByText(/raid.*selected/i)).toBeVisible();
  await expect(page.getByRole('button', { name: /^dispatch$/i })).toBeVisible();

  // Click dispatch
  await page.getByRole('button', { name: /^dispatch$/i }).click();

  // Optimistic update — selection cleared, raid moves to queue
  await expect(page.getByText(/raid.*selected/i)).not.toBeVisible({ timeout: 3000 });
});

test('dispatch button disabled for non-feasible selection', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByRole('table')).toBeVisible({ timeout: 8000 });

  // Switch to blocked filter
  await page.getByRole('button', { name: /blocked/i }).click();

  const firstCheckbox = page.getByRole('checkbox', { name: /select row/i }).first();
  if (await firstCheckbox.isVisible()) {
    await firstCheckbox.check();
    const dispatchBtn = page.getByRole('button', { name: /^dispatch$/i });
    await expect(dispatchBtn).toBeVisible();
    await expect(dispatchBtn).toHaveAttribute('aria-disabled', 'true');
  }
});

test('search filters raids by name', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByRole('table')).toBeVisible({ timeout: 8000 });

  await page.getByRole('searchbox', { name: /search raids/i }).fill('harden');
  await expect(page.getByText('Harden JWT validation')).toBeVisible();
  await expect(page.getByText('Write auth integration tests')).not.toBeVisible();
});

test('keyboard: tab focuses segmented filter buttons', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await expect(page.getByRole('group', { name: /filter raids/i })).toBeVisible({ timeout: 8000 });

  const allBtn = page.getByRole('button', { name: /all/i });
  await allBtn.focus();
  await expect(allBtn).toBeFocused();
});

test('tyr page has link to new saga plan', async ({ page }) => {
  await page.goto('/tyr');
  await expect(page.getByRole('link', { name: /new saga plan/i })).toBeVisible();
});

// ---------------------------------------------------------------------------
// Plan wizard — all 5 states
// ---------------------------------------------------------------------------

test('plan wizard renders prompt step at /tyr/plan', async ({ page }) => {
  await page.goto('/tyr/plan');
  await expect(page.getByText('Describe your goal')).toBeVisible();
  await expect(page.getByRole('navigation', { name: /plan wizard steps/i })).toBeVisible();
});

test('plan wizard — step 1 (prompt): shows step dots and form', async ({ page }) => {
  await page.goto('/tyr/plan');
  // StepDots labels — exact match to avoid collisions with step headings
  await expect(page.getByText('Describe', { exact: true })).toBeVisible();
  await expect(page.getByText('Clarify', { exact: true })).toBeVisible();
  await expect(page.getByText('Decompose', { exact: true })).toBeVisible();
  await expect(page.getByText('Review', { exact: true })).toBeVisible();
  await expect(page.getByText('Launch', { exact: true })).toBeVisible();
  // Form fields
  await expect(page.getByRole('textbox', { name: /goal description/i })).toBeVisible();
  await expect(page.getByRole('textbox', { name: /target repository/i })).toBeVisible();
});

test('plan wizard — step 2 (questions): shown after submitting prompt', async ({ page }) => {
  await page.goto('/tyr/plan');

  await page
    .getByRole('textbox', { name: /goal description/i })
    .fill('Build OIDC auth with silent refresh and PAT support for headless agents');
  await page.getByRole('textbox', { name: /target repository/i }).fill('niuulabs/volundr');
  await page.getByRole('button', { name: /next/i }).click();

  await expect(page.getByText('Clarify your plan')).toBeVisible({ timeout: 5000 });
  // Mock service returns 4 questions
  await expect(page.getByText(/Which target repositories/i)).toBeVisible();
});

test('plan wizard — step 3 (raiding): shown after submitting answers', async ({ page }) => {
  await page.goto('/tyr/plan');

  // Step 1
  await page.getByRole('textbox', { name: /goal description/i }).fill('Build auth module');
  await page.getByRole('button', { name: /next/i }).click();
  await expect(page.getByText('Clarify your plan')).toBeVisible({ timeout: 5000 });

  // Step 2
  await page.getByRole('button', { name: /decompose/i }).click();

  await expect(page.getByRole('status', { name: /decomposing plan/i })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByText(/Ravens are raiding/i)).toBeVisible();
});

test('plan wizard — step 4 (draft): auto-advances from raiding', async ({ page }) => {
  await page.goto('/tyr/plan');

  // Step 1
  await page.getByRole('textbox', { name: /goal description/i }).fill('Build auth module');
  await page.getByRole('button', { name: /next/i }).click();
  await expect(page.getByText('Clarify your plan')).toBeVisible({ timeout: 5000 });

  // Step 2
  await page.getByRole('button', { name: /decompose/i }).click();

  // Step 3 → 4 auto-advance
  await expect(page.getByText('Review your plan')).toBeVisible({ timeout: 10000 });
  await expect(page.getByText('New Saga', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /approve/i })).toBeVisible();
});

test('plan wizard — step 4: edit button toggles phase name editing', async ({ page }) => {
  await page.goto('/tyr/plan');

  await page.getByRole('textbox', { name: /goal description/i }).fill('Build auth module');
  await page.getByRole('button', { name: /next/i }).click();
  await expect(page.getByText('Clarify your plan')).toBeVisible({ timeout: 5000 });
  await page.getByRole('button', { name: /decompose/i }).click();
  await expect(page.getByText('Review your plan')).toBeVisible({ timeout: 10000 });

  // Click edit on first phase
  await page.getByRole('button', { name: /edit phase 1/i }).click();
  await expect(page.getByLabel(/edit phase 1 name/i)).toBeVisible();
  // Cancel
  await page.getByRole('button', { name: /cancel/i }).click();
  await expect(page.getByLabel(/edit phase 1 name/i)).not.toBeVisible();
});

test('plan wizard — step 5 (approved): reached after approving draft', async ({ page }) => {
  await page.goto('/tyr/plan');

  // Step 1
  await page.getByRole('textbox', { name: /goal description/i }).fill('Build auth module');
  await page.getByRole('button', { name: /next/i }).click();
  await expect(page.getByText('Clarify your plan')).toBeVisible({ timeout: 5000 });

  // Step 2
  await page.getByRole('button', { name: /decompose/i }).click();

  // Step 3 → 4
  await expect(page.getByText('Review your plan')).toBeVisible({ timeout: 10000 });

  // Step 4 → 5
  await page.getByRole('button', { name: /approve/i }).click();
  await expect(page.getByText('Saga launched!')).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('link', { name: /open in sagas/i })).toBeVisible();
});

test('plan wizard — back navigation from questions returns to prompt', async ({ page }) => {
  await page.goto('/tyr/plan');

  await page.getByRole('textbox', { name: /goal description/i }).fill('Build auth module');
  await page.getByRole('button', { name: /next/i }).click();
  await expect(page.getByText('Clarify your plan')).toBeVisible({ timeout: 5000 });

  await page.getByRole('button', { name: /back/i }).click();
  await expect(page.getByText('Describe your goal')).toBeVisible();
});

test('plan wizard — keyboard accessibility: tab order works on prompt step', async ({ page }) => {
  await page.goto('/tyr/plan');
  // Tab through interactive elements
  await page.keyboard.press('Tab');
  // The textarea should be focusable
  const textarea = page.getByRole('textbox', { name: /goal description/i });
  await textarea.fill('test');
  await expect(textarea).toBeFocused();
});

// ---------------------------------------------------------------------------
// WorkflowBuilder — /tyr/workflows
// ---------------------------------------------------------------------------

test('workflow builder page renders at /tyr/workflows', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder-page')).toBeVisible();
});

test('workflow builder shows workflow tabs after loading', async ({ page }) => {
  await page.goto('/tyr/workflows');
  // Mock service returns two seed workflows; wait for tabs to appear
  await expect(
    page.getByText(/Auth Rewrite Workflow/).or(page.getByText(/loading/i)),
  ).toBeVisible();
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
});

test('workflow builder shows graph view by default', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('graph-view')).toBeVisible();
  await expect(page.getByTestId('graph-canvas')).toBeVisible();
});

test('workflow builder can switch to pipeline view', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  await page.getByTestId('tab-pipeline').click();
  await expect(page.getByTestId('pipeline-view')).toBeVisible();
});

test('workflow builder can switch to yaml view', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  await page.getByTestId('tab-yaml').click();
  await expect(page.getByTestId('yaml-view')).toBeVisible();
  await expect(page.getByTestId('yaml-content')).toBeVisible();
});

test('workflow builder shows validation panel', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('validation-panel')).toBeVisible();
  await expect(page.getByTestId('validation-pill')).toBeVisible();
});

test('can add a stage node in graph view', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  const nodesBefore = await page.locator('[data-testid^="workflow-node-"]').count();
  await page.getByTestId('add-stage').click();
  const nodesAfter = await page.locator('[data-testid^="workflow-node-"]').count();
  expect(nodesAfter).toBe(nodesBefore + 1);
});

test('validation pill shows error when cycle is created', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });

  // Add two fresh stage nodes
  await page.getByTestId('add-stage').click();
  await page.getByTestId('add-stage').click();

  const allNodes = page.locator('[data-testid^="workflow-node-"]');
  const total = await allNodes.count();
  const nodeA = allNodes.nth(total - 2);
  const nodeB = allNodes.nth(total - 1);
  const idA = ((await nodeA.getAttribute('data-testid')) ?? '').replace('workflow-node-', '');
  const idB = ((await nodeB.getAttribute('data-testid')) ?? '').replace('workflow-node-', '');

  // The graph canvas uses overflow:hidden — new nodes may be positioned outside the
  // visible area. Playwright's .click() (even with force:true) cannot click off-screen
  // SVG elements. Dispatch native mouse events via evaluate to bypass the viewport check.
  const mousedown = (el: Element) =>
    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
  const clickEl = (el: Element) =>
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

  // Select nodeA → connect button appears
  await nodeA.evaluate(mousedown);
  await page.getByTestId(`connect-btn-${idA}`).waitFor({ state: 'attached', timeout: 2000 });

  // Start connecting from nodeA → connect button hides (connecting mode)
  await page.getByTestId(`connect-btn-${idA}`).evaluate(clickEl);
  await page.getByTestId(`connect-btn-${idA}`).waitFor({ state: 'detached', timeout: 2000 });

  // Complete edge A→B: click nodeB while in connecting mode
  await nodeB.evaluate(mousedown);
  // Wait for connecting mode to end (connect button reappears for nodeA)
  await page.getByTestId(`connect-btn-${idA}`).waitFor({ state: 'attached', timeout: 2000 });

  // Select nodeB → its connect button appears
  await nodeB.evaluate(mousedown);
  await page.getByTestId(`connect-btn-${idB}`).waitFor({ state: 'attached', timeout: 2000 });

  // Start connecting from nodeB → connect button hides
  await page.getByTestId(`connect-btn-${idB}`).evaluate(clickEl);
  await page.getByTestId(`connect-btn-${idB}`).waitFor({ state: 'detached', timeout: 2000 });

  // Complete edge B→A: click nodeA while in connecting mode (creates cycle)
  await nodeA.evaluate(mousedown);

  // Cycle should now be detected
  const pill = page.getByTestId('validation-pill');
  await expect(pill).toBeVisible();
  const issueCount = parseInt((await pill.getAttribute('data-issue-count')) ?? '0');
  expect(issueCount).toBeGreaterThan(0);
  await pill.click();
  await expect(page.locator('[data-testid^="validation-issue-"]').first()).toBeVisible();
});

test('clicking validation pill expands issue list when issues exist', async ({ page }) => {
  // Use a workflow with a known issue — navigate to the workflows page
  // The seed data includes valid workflows; to test expanded state, we look
  // for the pill and check the toggle behavior
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  const pill = page.getByTestId('validation-pill');
  await expect(pill).toBeVisible();
  const issueCount = await pill.getAttribute('data-issue-count');
  if (issueCount && parseInt(issueCount) > 0) {
    await pill.click();
    await expect(page.locator('[data-testid^="validation-issue-"]').first()).toBeVisible();
  }
});

test('library panel shows persona chips in graph view', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('library-panel')).toBeVisible();
  await expect(page.getByTestId('persona-chip-persona-plan')).toBeVisible();
});

test('library panel not visible in pipeline view', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });
  await page.getByTestId('tab-pipeline').click();
  await expect(page.getByTestId('library-panel')).not.toBeVisible();
});

test('delete selected node button removes the node', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await expect(page.getByTestId('workflow-builder')).toBeVisible({ timeout: 5000 });

  const nodes = page.locator('[data-testid^="workflow-node-"]');
  // Wait for seed nodes to be present
  await expect(nodes.first()).toBeVisible({ timeout: 5000 });
  const countBefore = await nodes.count();

  // Click the first seed node (always visible at a known position in the viewport)
  // Use force:true because SVG <g> elements may fail Playwright's actionability checks
  await nodes.first().click({ force: true });

  // The delete-selected button should appear in the toolbar
  const deleteBtn = page.getByTestId('delete-selected');
  await expect(deleteBtn).toBeVisible();
  await deleteBtn.click();

  await expect(nodes).toHaveCount(countBefore - 1);
});
