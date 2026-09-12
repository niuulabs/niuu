import { describe, expect, it, vi } from 'vitest';
import { buildSetupHttpAdapter, mapCatalogEntry, mapIntegration, mapTestResult } from './http';

function clients() {
  return {
    setup: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
    integrations: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
}

describe('buildSetupHttpAdapter', () => {
  it('reads state and system from the setup base', async () => {
    const c = clients();
    c.setup.get.mockResolvedValueOnce({ enabled: true }).mockResolvedValueOnce({ healthy: true });
    const adapter = buildSetupHttpAdapter(c);
    expect(await adapter.getState()).toEqual({ enabled: true });
    expect(await adapter.getSystem()).toEqual({ healthy: true });
    expect(c.setup.get).toHaveBeenNthCalledWith(1, '');
    expect(c.setup.get).toHaveBeenNthCalledWith(2, '/system');
  });

  it('records steps and completion', async () => {
    const c = clients();
    c.setup.put.mockResolvedValue({ completed: false });
    c.setup.post.mockResolvedValue({ completed: true });
    const adapter = buildSetupHttpAdapter(c);
    await adapter.completeStep('git', { via: 'wizard' });
    await adapter.completeStep('a b');
    await adapter.complete();
    expect(c.setup.put).toHaveBeenCalledWith('/steps/git', { data: { via: 'wizard' } });
    expect(c.setup.put).toHaveBeenCalledWith('/steps/a%20b', { data: {} });
    expect(c.setup.post).toHaveBeenCalledWith('/complete');
  });

  it('maps catalog and connections from snake_case', async () => {
    const c = clients();
    c.integrations.get
      .mockResolvedValueOnce([
        {
          slug: 'github',
          name: 'GitHub',
          description: 'd',
          integration_type: 'source_control',
          credential_schema: { required: ['token'] },
        },
      ])
      .mockResolvedValueOnce([
        { id: '1', integration_type: 'source_control', credential_name: 'c', enabled: true },
      ]);
    const adapter = buildSetupHttpAdapter(c);
    const [entry] = await adapter.listCatalog();
    expect(entry).toEqual({
      slug: 'github',
      name: 'GitHub',
      description: 'd',
      integrationType: 'source_control',
      authType: 'api_key',
      credentialSchema: { required: ['token'] },
      configSchema: {},
    });
    const [connection] = await adapter.listIntegrations();
    expect(connection).toEqual({
      id: '1',
      slug: '',
      integrationType: 'source_control',
      credentialName: 'c',
      enabled: true,
      config: {},
      credentialStatus: 'unknown',
    });
    expect(c.integrations.get).toHaveBeenNthCalledWith(1, '/catalog');
    expect(c.integrations.get).toHaveBeenNthCalledWith(2, '');
  });

  it('connects with an inline credential and tests by id', async () => {
    const c = clients();
    c.integrations.post
      .mockResolvedValueOnce({
        id: '9',
        slug: 'linear',
        integration_type: 'issue_tracker',
        credential_name: 'linear-setup',
        enabled: true,
        config: { x: 1 },
        credential_status: 'valid',
      })
      .mockResolvedValueOnce({ success: false, provider: 'Linear', error: 'bad key' });
    const adapter = buildSetupHttpAdapter(c);
    const connection = await adapter.connectIntegration({
      slug: 'linear',
      credentialName: 'linear-setup',
      credential: { api_key: 'k' },
      config: { x: 1 },
    });
    expect(connection.id).toBe('9');
    expect(connection.credentialStatus).toBe('valid');
    expect(c.integrations.post).toHaveBeenCalledWith('', {
      slug: 'linear',
      config: { x: 1 },
      credential: { name: 'linear-setup', data: { api_key: 'k' } },
    });
    const result = await adapter.testIntegration('9');
    expect(result).toEqual({
      success: false,
      provider: 'Linear',
      workspace: null,
      user: null,
      error: 'bad key',
    });
    expect(c.integrations.post).toHaveBeenLastCalledWith('/9/test');
  });

  it('propagates client errors', async () => {
    const c = clients();
    c.setup.get.mockRejectedValue(new Error('offline'));
    await expect(buildSetupHttpAdapter(c).getState()).rejects.toThrow('offline');
  });

  it('exposes the mappers', () => {
    expect(
      mapCatalogEntry({
        slug: 's',
        name: 'n',
        description: 'd',
        integration_type: 'ai_provider',
        auth_type: 'browser_login',
      }).authType,
    ).toBe('browser_login');
    expect(
      mapIntegration({
        id: '1',
        slug: 'x',
        integration_type: 't',
        credential_name: 'c',
        enabled: false,
        config: { a: 1 },
        credential_status: 'expired',
      }),
    ).toMatchObject({ slug: 'x', enabled: false, credentialStatus: 'expired', config: { a: 1 } });
    expect(mapTestResult({ success: true, provider: 'p', workspace: 'w', user: 'u' })).toEqual({
      success: true,
      provider: 'p',
      workspace: 'w',
      user: 'u',
      error: null,
    });
  });

  it('drives interactive sign-ins through the enrollment routes', async () => {
    const c = clients();
    const wire = {
      id: 'e1',
      connectionId: 'c1',
      providerSlug: 'codex',
      credentialName: 'codex-setup',
      state: 'awaiting_user',
      verificationUri: 'https://auth.openai.com/codex/device',
      userCode: 'AB-12',
      expiresAt: 'later',
      inputRequired: false,
    };
    c.integrations.post
      .mockResolvedValueOnce(wire)
      .mockResolvedValueOnce({ ...wire, state: 'complete' });
    c.integrations.get.mockResolvedValue({
      id: 'e1',
      connectionId: 'c1',
      providerSlug: 'codex',
      credentialName: 'n',
      state: 'pending',
      expiresAt: 'later',
    });
    c.integrations.delete.mockResolvedValue({ ...wire, state: 'cancelled' });
    const adapter = buildSetupHttpAdapter(c);

    const started = await adapter.startEnrollment('codex', 'codex-setup');
    expect(started).toEqual({
      id: 'e1',
      connectionId: 'c1',
      providerSlug: 'codex',
      credentialName: 'codex-setup',
      state: 'awaiting_user',
      verificationUri: 'https://auth.openai.com/codex/device',
      userCode: 'AB-12',
      expiresAt: 'later',
      errorCode: '',
      inputRequired: false,
    });
    expect(c.integrations.post).toHaveBeenNthCalledWith(1, '/enrollments', {
      slug: 'codex',
      credential_name: 'codex-setup',
    });

    const pending = await adapter.getEnrollment('e 1');
    expect(pending.verificationUri).toBe('');
    expect(pending.inputRequired).toBe(false);
    expect(c.integrations.get).toHaveBeenCalledWith('/enrollments/e%201');

    expect((await adapter.cancelEnrollment('e1')).state).toBe('cancelled');
    expect(c.integrations.delete).toHaveBeenCalledWith('/enrollments/e1');

    expect((await adapter.submitEnrollmentCode('e1', 'code')).state).toBe('complete');
    expect(c.integrations.post).toHaveBeenNthCalledWith(2, '/enrollments/e1/code', {
      code: 'code',
    });
  });
});
