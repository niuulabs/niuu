import { expect, test } from '@playwright/test';

test.describe('Research Center live workflow launch', () => {
  test('launches a real research campaign through the UI', async ({ page, request }) => {
    const nonce = Date.now();
    const question = `Should Ting add a dedicated Research Center tab for workflow-backed research? ${nonce}`;
    const audience = `product-${nonce}`;
    const deliverable = `decision memo ${nonce}`;

    await page.goto('/ting/research?config=/config.live.json');

    await page.waitForFunction(() => {
      const backends = (window as Window & { __NIUU_SERVICE_BACKENDS__?: Record<string, unknown> })
        .__NIUU_SERVICE_BACKENDS__;
      return Boolean(backends && backends['ting.research']);
    });

    const serviceBackends = await page.evaluate(() => {
      return (
        (window as Window & { __NIUU_SERVICE_BACKENDS__?: Record<string, unknown> })
          .__NIUU_SERVICE_BACKENDS__ ?? {}
      );
    });
    expect(serviceBackends['ting.research']).toMatchObject({
      mode: 'live',
      transport: 'http',
      target: '/api/v1/ting/research',
    });

    await expect(
      page.getByRole('heading', {
        name: 'Campaigns, artifacts, and durable memory in one place.',
      }),
    ).toBeVisible({ timeout: 15000 });

    await page.getByRole('button', { name: 'New campaign' }).click();
    await expect(page.getByRole('heading', { name: 'Start a campaign' })).toBeVisible();

    await page.getByLabel('Question').fill(question);
    await page.getByLabel('Audience').fill(audience);
    await page.getByLabel('Deliverable').fill(deliverable);
    await page.getByLabel('Success criteria').fill('create a clear recommendation');
    await page
      .getByLabel('Constraints')
      .fill('use existing infrastructure\navoid tracker coupling');

    const createResponsePromise = page.waitForResponse((response) => {
      return (
        response.request().method() === 'POST' &&
        response.url().includes('/api/v1/ting/research/campaigns')
      );
    });

    await page.getByRole('button', { name: 'Launch campaign' }).click();

    const createResponse = await createResponsePromise;
    if (createResponse.status() !== 201) {
      throw new Error(
        `Expected research campaign launch to return 201, got ${createResponse.status()}: ${await createResponse.text()}`,
      );
    }
    const created = (await createResponse.json()) as {
      slug: string;
      name: string;
      sessionId: string;
    };

    await page.waitForURL(new RegExp(`/ting/research/${created.slug}(?:\\?.*)?$`), {
      timeout: 15000,
    });

    await expect(page.getByRole('heading', { name: created.name })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText('Frame the inquiry')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open live session' })).toBeVisible();

    const campaignResponse = await request.get(`/api/v1/ting/research/campaigns/${created.slug}`);
    expect(campaignResponse.ok()).toBeTruthy();
    const campaign = (await campaignResponse.json()) as {
      slug: string;
      sessionId: string;
      workflowName: string;
      stageState: Array<{ stageId: string; status: string }>;
    };
    expect(campaign.slug).toBe(created.slug);
    expect(campaign.sessionId).toBe(created.sessionId);
    expect(campaign.workflowName).toBe('Research Campaign');
    expect(campaign.stageState[0]?.stageId).toBe('research-frame');

    const artifactsResponse = await request.get(
      `/api/v1/ting/research/campaigns/${created.slug}/artifacts`,
    );
    expect(artifactsResponse.ok()).toBeTruthy();

    const sessionResponse = await request.get(`/api/v1/forge/sessions/${created.sessionId}`);
    expect(sessionResponse.ok()).toBeTruthy();
    const session = (await sessionResponse.json()) as { id: string; status: string };
    expect(session.id).toBe(created.sessionId);
    expect(typeof session.status).toBe('string');
    expect(session.status.length).toBeGreaterThan(0);
  });
});
