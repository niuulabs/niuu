/* global console */

import { chromium } from '@playwright/test';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1800, height: 1200 } });
await page.goto('http://192.168.1.106:8080/ting/research/assess-whether-adding-a-dedicated-research-center-tab-in-ting-i?config=/config.live.json', { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(2500);
await page.getByRole('button', { name: /open in mímir/i }).click();
await page.waitForTimeout(1500);
await page.screenshot({ path: '../build/dev-run/research-open-in-mimir-ui.png', fullPage: false });
console.log(page.url());
await browser.close();
