/* global console, document, window */

import { chromium } from '@playwright/test';
const base = 'http://192.168.1.106:8080/ting/research/assess-whether-adding-a-dedicated-research-center-tab-in-ting-i?config=/config.live.json';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1050 } });
await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(2500);
await page.locator('.ting-research-detail__citation').first().click();
await page.waitForTimeout(500);
const info = await page.evaluate(() => {
  const pop = document.querySelector('.ting-research-detail__popover');
  if (!pop) return null;
  const rect = pop.getBoundingClientRect();
  const style = window.getComputedStyle(pop);
  return { x: rect.x, y: rect.y, w: rect.width, h: rect.height, display: style.display, opacity: style.opacity, visibility: style.visibility, text: pop.textContent?.slice(0,120) };
});
console.log(JSON.stringify(info, null, 2));
await browser.close();
