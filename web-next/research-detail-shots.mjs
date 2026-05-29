import { chromium } from '@playwright/test';

const base = 'http://192.168.1.106:8080/ting/research/assess-whether-adding-a-dedicated-research-center-tab-in-ting-i?config=/config.live.json';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1800, height: 1200 } });
await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(3000);
await page.screenshot({ path: '../build/dev-run/research-detail-proof-page.png', fullPage: false });
await page.getByRole('button', { name: '[s1]' }).first().click();
await page.waitForTimeout(750);
await page.screenshot({ path: '../build/dev-run/research-detail-proof-popover.png', fullPage: false });
await page.getByRole('button', { name: 'open', exact: true }).click();
await page.waitForTimeout(1000);
await page.screenshot({ path: '../build/dev-run/research-detail-proof-drawer.png', fullPage: false });
await browser.close();
