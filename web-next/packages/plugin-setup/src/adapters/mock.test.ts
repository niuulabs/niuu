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

  it('turns a sign-in on once the person registers their own application', async () => {
    const catalog = MOCK_CATALOG.map((e) =>
      e.slug === 'github' ? { ...e, signInAvailable: false, signInNeedsApp: true } : e,
    );
    const service = createMockSetupService({ latencyMs: 0, catalog });
    await expect(service.startEnrollment('github', 'github-signin')).rejects.toThrow(
      'not configured',
    );
    await expect(service.registerOAuthClient('github', { clientId: ' ' })).rejects.toThrow(
      'client id is required',
    );
    await expect(service.registerOAuthClient('anthropic', { clientId: 'x' })).rejects.toThrow(
      'does not sign in through',
    );
    await service.registerOAuthClient('github', { clientId: 'Iv1.mine' });
    const github = (await service.listCatalog()).find((e) => e.slug === 'github');
    expect(github).toMatchObject({ signInAvailable: true, signInNeedsApp: false });
    expect((await service.startEnrollment('github', 'github-signin')).state).toBe('pending');
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

  it('simulates device-code and browser sign-ins', async () => {
    const service = createMockSetupService({ latencyMs: 0, enrollmentPolls: 1 });
    await expect(service.startEnrollment('nope', 'n')).rejects.toThrow('Unknown integration');
    await expect(service.startEnrollment('anthropic', 'n')).rejects.toThrow(
      'does not support interactive',
    );

    const codex = await service.startEnrollment('codex', 'codex-setup');
    expect(codex.state).toBe('pending');
    expect(await service.startEnrollment('codex', 'codex-setup')).toEqual(codex);
    const waiting = await service.getEnrollment(codex.id);
    expect(waiting.state).toBe('awaiting_user');
    expect(waiting.userCode).toBe('MOCK-1234');
    await expect(service.submitEnrollmentCode(codex.id, 'x')).rejects.toThrow('does not accept');
    const done = await service.getEnrollment(codex.id);
    expect(done.state).toBe('complete');
    expect((await service.listIntegrations()).map((c) => c.slug)).toEqual(['codex']);
    expect((await service.cancelEnrollment(codex.id)).state).toBe('complete');

    const claude = await service.startEnrollment('claude-code', 'claude-setup');
    await expect(service.submitEnrollmentCode(claude.id, 'code')).rejects.toThrow('not running');
    const claudeWaiting = await service.getEnrollment(claude.id);
    expect(claudeWaiting.inputRequired).toBe(true);
    expect(claudeWaiting.verificationUri).toContain('claude.ai');
    expect(await service.getEnrollment(claude.id)).toEqual(claudeWaiting);
    await expect(service.submitEnrollmentCode(claude.id, '  ')).rejects.toThrow('required');
    expect((await service.submitEnrollmentCode(claude.id, 'code')).state).toBe('complete');
    expect(await service.listIntegrations()).toHaveLength(2);

    const cancelled = await service.startEnrollment('claude-code', 'again');
    expect((await service.cancelEnrollment(cancelled.id)).state).toBe('cancelled');
    await expect(service.getEnrollment('missing')).rejects.toThrow('not found');
    await expect(service.cancelEnrollment('missing')).rejects.toThrow('not found');
    await expect(service.submitEnrollmentCode('missing', 'c')).rejects.toThrow('not found');
  });
});
