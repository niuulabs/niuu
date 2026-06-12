// Drive the live ODIN review inbox with a real browser: screenshot the
// pending held build, approve it through the actual UI, and capture the
// settled ledger. Run from web-next/ so the playwright dependency resolves:
//   node e2e/valkyrie-odin-ui-proof.mjs http://127.0.0.1:8080 /tmp/out
import { chromium } from '@playwright/test';

const [baseUrl, outDir] = process.argv.slice(2);
if (!baseUrl || !outDir) {
  console.error('usage: node valkyrie_odin_ui_proof.mjs <baseUrl> <outDir>');
  process.exit(2);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  console.log(`ui-proof: opening ${baseUrl}/valkyrie`);
  await page.goto(`${baseUrl}/valkyrie`, { waitUntil: 'networkidle' });
  await page.getByTestId('review-card').first().waitFor({ timeout: 90_000 });
  await page.screenshot({ path: `${outDir}/odin-inbox-pending.png`, fullPage: true });
  console.log('ui-proof: pending inbox captured');

  await page
    .getByLabel('Decision reason')
    .fill('Approved from the live ODIN inbox after inspecting the tool.');
  await page.getByRole('button', { name: /approve/i }).click();
  console.log('ui-proof: clicked approve');

  await page.getByTestId('inbox-empty').waitFor({ timeout: 60_000 });
  await page.screenshot({ path: `${outDir}/odin-inbox-after-approve.png`, fullPage: true });
  console.log('ui-proof: inbox drained after approval');

  await page.goto(`${baseUrl}/valkyrie/activity`, { waitUntil: 'networkidle' });
  await page.getByTestId('activity-row').first().waitFor({ timeout: 90_000 });
  await page.screenshot({ path: `${outDir}/odin-activity.png`, fullPage: true });
  console.log('ui-proof: settled ledger captured');

  await page.goto(`${baseUrl}/valkyrie/fleet`, { waitUntil: 'networkidle' });
  await page.getByTestId('fleet-page').waitFor({ timeout: 30_000 });
  await page.screenshot({ path: `${outDir}/odin-fleet.png`, fullPage: true });
  console.log('ui-proof: fleet captured');
} finally {
  await browser.close();
}
