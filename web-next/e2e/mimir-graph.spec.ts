import { test, expect, type Page } from '@playwright/test';

const graph = {
  nodes: [
    {
      id: 'brain:research/a',
      path: 'research/a',
      title: 'Retrieval study',
      category: 'research',
      kind: 'observation',
      summary: 'Comparing hybrid retrieval',
      mount: 'brain',
    },
    {
      id: 'brain:concepts/b',
      path: 'concepts/b',
      title: 'Hybrid retrieval',
      category: 'concepts',
      kind: 'concept',
      summary: 'Vector and keyword search',
      mount: 'brain',
    },
    {
      id: 'brain:notes/c',
      path: 'notes/c',
      title: 'Meeting notes',
      category: 'notes',
      kind: 'page',
      summary: 'Next experiments',
      mount: 'brain',
    },
  ],
  edges: [{ source: 'brain:research/a', target: 'brain:concepts/b', type: 'wikilink' }],
};
async function setup(page: Page) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/config.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.mimir = { mode: 'http', baseUrl: '/graph-test-api' };
    await route.fulfill({ json: config });
  });
  await page.route('**/graph-test-api/**', (route) => route.fulfill({ json: [] }));
}

test('graph searches research, filters categories, orbits, and opens the selected page', async ({
  page,
}) => {
  await setup(page);
  await page.route('**/graph-test-api/graph*', (route) => route.fulfill({ json: graph }));
  await page.goto('/mimir/graph');
  const canvas = page.getByRole('group', { name: 'Knowledge graph' });
  await expect(canvas).toBeVisible();
  await expect(
    page.getByLabel('Graph legend').getByRole('button', { name: 'research 1' }),
  ).toBeVisible();
  const node = canvas.getByRole('button', { name: 'Retrieval study' });
  const before = await node.getAttribute('transform');
  await canvas.focus();
  await page.keyboard.press('ArrowRight');
  await expect(node).not.toHaveAttribute('transform', before!);
  await page.getByRole('searchbox', { name: 'Search graph' }).fill('research');
  await expect(canvas.getByRole('button')).toHaveCount(1);
  await page
    .getByLabel('Graph search results')
    .getByRole('button', { name: /Retrieval study/ })
    .click();
  await expect(page.getByRole('link', { name: 'Open page' })).toBeVisible();
  await page.screenshot({ path: 'test-results/mimir-graph-research.png' });
  await page.getByRole('button', { name: 'Reset filters' }).click();
  await page.getByLabel('Graph legend').getByRole('button', { name: 'notes 1' }).click();
  await expect(canvas.getByRole('button')).toHaveCount(2);
  await node.click();
  await expect(node).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('link', { name: 'Open page' }).click();
  await expect(page).toHaveURL(/\/mimir\/pages/);
});

test('graph announces loading and backend failure', async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/graph-test-api/graph*', async (route) => {
    await waiting;
    await route.fulfill({ status: 500, json: { detail: 'Graph unavailable' } });
  });
  await page.goto('/mimir/graph');
  await expect(page.getByText('loading graph…')).toBeVisible();
  release();
  await expect(page.getByRole('alert').filter({ hasText: /500|unavailable/i })).toBeVisible({
    timeout: 15000,
  });
});

test('renders a clustered corpus and keeps reduced-motion geometry still', async ({ page }) => {
  await setup(page);
  const categories = ['research', 'concepts', 'notes', 'projects', 'skills', 'decisions'];
  const nodes = Array.from({ length: 180 }, (_, i) => ({
    id: String(i),
    path: `knowledge/${i}`,
    title: i % 30 ? `Finding ${i}` : categories[i / 30]!,
    category: categories[Math.floor(i / 30)]!,
    kind: 'page',
    mount: 'brain',
    summary: 'Graph test fixture',
  }));
  const edges = nodes
    .filter((_, i) => i % 30)
    .map((node) => ({
      source: String(Math.floor(Number(node.id) / 30) * 30),
      target: node.id,
      type: 'wikilink',
    }));
  await page.route('**/graph-test-api/graph*', (route) =>
    route.fulfill({ json: { nodes, edges } }),
  );
  await page.goto('/mimir/graph');
  const canvas = page.getByRole('group', { name: 'Knowledge graph' });
  await expect(canvas.getByRole('button')).toHaveCount(nodes.length);
  await page.getByRole('button', { name: 'Fit', exact: true }).click();
  const hub = canvas.getByRole('button', { name: 'research', exact: true });
  const position = await hub.getAttribute('transform');
  await page.waitForTimeout(2800);
  await expect(hub).toHaveAttribute('transform', position!);
  await page.screenshot({ path: 'test-results/mimir-graph-clusters.png' });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect(hub).not.toHaveAttribute('transform', position!);
  await page.getByRole('button', { name: 'Motion on' }).click();
  const paused = await hub.getAttribute('transform');
  await page.waitForTimeout(300);
  await expect(hub).toHaveAttribute('transform', paused!);
});
