import { describe, expect, it } from 'vitest';
import { MOCK_CATALOG, MOCK_SYSTEM, createMockSetupService } from './mock';

describe('createMockSetupService', () => {
  it('serves catalog, system and progress in memory', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    expect(await service.listCatalog()).toBe(MOCK_CATALOG);
    expect(await service.getSystem()).toBe(MOCK_SYSTEM);
    expect((await service.getState()).completed).toBe(false);

    const afterStep = await service.completeStep('system', { ok: true });
    expect(afterStep.completedSteps.map((r) => r.step)).toEqual(['system']);
    const again = await service.completeStep('system');
    expect(again.completedSteps).toHaveLength(1);

    const done = await service.complete();
    expect(done.completed).toBe(true);
    expect(done.completedAt).toBeTruthy();
  });

  it('accepts initial state and custom catalog', async () => {
    const service = createMockSetupService({
      latencyMs: 0,
      initialState: { completed: true, mode: 'mini' },
      catalog: [],
      system: { host: null, checks: [], healthy: true },
    });
    expect((await service.getState()).mode).toBe('mini');
    expect(await service.listCatalog()).toEqual([]);
    expect((await service.getSystem()).host).toBeNull();
  });

  it('connects and tests integrations', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    expect(await service.listIntegrations()).toEqual([]);
    const connection = await service.connectIntegration({
      slug: 'github',
      credentialName: 'github-setup',
      credential: { token: 't' },
      config: { orgs: ['niuulabs'] },
    });
    expect(connection).toMatchObject({
      id: 'mock-1',
      slug: 'github',
      integrationType: 'source_control',
      enabled: true,
      config: { orgs: ['niuulabs'] },
    });
    expect(await service.listIntegrations()).toHaveLength(1);
    expect((await service.testIntegration('mock-1')).success).toBe(true);
    expect((await service.testIntegration('nope')).error).toBe('No such connection');
    await expect(
      service.connectIntegration({
        slug: 'unknown',
        credentialName: 'x',
        credential: {},
        config: {},
      }),
    ).rejects.toThrow('Unknown integration unknown');
  });
});
