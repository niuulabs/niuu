// One-off evidence capture for NIU-1061: screenshots every reworked Mimir view
// against the live dev stack. Run: node e2e/capture-mimir-evidence.mjs <baseUrl> <outDir>
import { chromium } from '@playwright/test';

const base = process.argv[2] ?? 'http://192.168.1.106:8080';
const out = process.argv[3] ?? '/tmp/niu-1055-proof/ui';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto(base + '/', { waitUntil: 'networkidle' });
await page.getByTitle(/^Mímir/).click();
await page.waitForTimeout(1500);

const shoot = async (name) => {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/${name}.png` });
  console.log('captured', name);
};

const tab = async (label) => {
  await page.locator(`a:has-text("${label}"), button:has-text("${label}")`).first().click();
  await page.waitForTimeout(800);
};

await shoot('01-overview-live-feed');

await tab('Search');
await page.getByRole('searchbox').fill('who uses Mimir');
await page.waitForTimeout(1500);
await shoot('02-search-results');
await page.getByTestId('debug-toggle').click();
await page.waitForTimeout(1500);
await shoot('03-search-debug-breakdown');

await tab('Analytics');
await shoot('04-analytics');

await tab('Doctor');
await shoot('05-doctor');

await tab('Graph');
await page.waitForTimeout(2000);
await shoot('06-graph');

await browser.close();
console.log('done');
