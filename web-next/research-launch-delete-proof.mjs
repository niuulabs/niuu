/* global console, process, URL */

import { chromium } from '@playwright/test';

const baseUrl = process.env.RESEARCH_BASE_URL ?? 'http://192.168.1.106:8080';
const config = process.env.RESEARCH_CONFIG ?? '/config.live.json';
const outputDir =
  process.env.RESEARCH_OUTPUT_DIR ??
  '/Users/jozefvaneenbergen/git/niuu/software/volundr/build/dev-run';
const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
const question = `UI proof ${timestamp}: should research launch support tagged workflows and campaign delete?`;

function slugify(value) {
  const matches = value.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  return matches.join('-').slice(0, 96);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1720, height: 1180 } });

let dialogAccepted = false;
page.on('dialog', async (dialog) => {
  dialogAccepted = true;
  await dialog.accept();
});

const launchUrl = `${baseUrl}/ting/research/new?config=${encodeURIComponent(config)}`;
await page.goto(launchUrl, { waitUntil: 'networkidle' });
await page.waitForSelector('h1');

const workflowOptions = await page.locator('select').nth(0).evaluate((select) =>
  Array.from(select.options).map((option) => option.textContent?.trim() ?? ''),
);
const workflowHelperText =
  (await page.locator('text=Showing').first().textContent())?.trim() ??
  (await page.locator('text=No research-tagged workflows found').first().textContent())?.trim() ??
  '';

await page.screenshot({ path: `${outputDir}/research-launch-workflow-picker-proof.png`, fullPage: true });

await page.locator('textarea').first().fill(question);

const repoSelect = page.getByTestId('research-launch-repo-select');
let selectedRepoLabel = '';
let selectedBranch = '';
if (await repoSelect.count()) {
  const repoOptions = await repoSelect.evaluate((select) =>
    Array.from(select.options)
      .map((option) => ({
        value: option.value,
        label: option.textContent?.trim() ?? '',
      }))
      .filter((option) => option.value),
  );
  if (repoOptions.length > 0) {
    await repoSelect.selectOption(repoOptions[0].value);
    selectedRepoLabel = repoOptions[0].label;
  }
}

const branchSelect = page.getByTestId('research-launch-branch-select');
if (await branchSelect.count()) {
  const branchOptions = await branchSelect.evaluate((select) =>
    Array.from(select.options)
      .map((option) => option.value)
      .filter(Boolean),
  );
  if (branchOptions.length > 0) {
    await branchSelect.selectOption(branchOptions[0]);
    selectedBranch = branchOptions[0];
  }
}

await page.screenshot({ path: `${outputDir}/research-launch-dropdowns-proof.png`, fullPage: true });

await page.getByRole('button', { name: 'Launch campaign' }).click();

await page.waitForURL((url) => {
  return url.pathname.startsWith('/ting/research/') && url.pathname !== '/ting/research/new';
}, {
  timeout: 120000,
});
await page.waitForLoadState('networkidle');
const createdPath = new URL(page.url()).pathname;
const createdSlug = createdPath.split('/').filter(Boolean).at(-1) ?? slugify(question);

await page.screenshot({ path: `${outputDir}/research-launch-created-detail-proof.png`, fullPage: true });

await page.getByRole('button', { name: /Operator/ }).click();
await page.getByRole('button', { name: 'Actions' }).click();
await page.waitForSelector('text=Delete campaign');

await page.screenshot({ path: `${outputDir}/research-delete-action-proof.png`, fullPage: true });

await page.getByRole('button', { name: 'Delete campaign' }).click();
await page.waitForURL((url) => url.pathname === '/ting/research', { timeout: 120000 });
await page.waitForLoadState('networkidle');

await page.screenshot({ path: `${outputDir}/research-delete-redirect-proof.png`, fullPage: true });

await browser.close();

console.log(
  JSON.stringify(
    {
      slug: createdSlug,
      workflowOptions,
      workflowHelperText,
      selectedRepoLabel,
      selectedBranch,
      dialogAccepted,
      screenshots: [
        `${outputDir}/research-launch-workflow-picker-proof.png`,
        `${outputDir}/research-launch-dropdowns-proof.png`,
        `${outputDir}/research-launch-created-detail-proof.png`,
        `${outputDir}/research-delete-action-proof.png`,
        `${outputDir}/research-delete-redirect-proof.png`,
      ],
    },
    null,
    2,
  ),
);
