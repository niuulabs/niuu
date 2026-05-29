import { chromium } from '@playwright/test';

const base = 'http://192.168.1.106:8080/ting/research/assess-whether-adding-a-dedicated-research-center-tab-in-ting-i?config=/config.live.json';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1050 } });
await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(2500);
await page.locator('.ting-research-detail__citation').first().click();
await page.waitForTimeout(500);
await page.evaluate(() => window.scrollTo(0, 620));
await page.waitForTimeout(500);
await page.screenshot({ path: '../build/dev-run/research-detail-proof-popover-v4.png', fullPage: false });
await browser.close();
