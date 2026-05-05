import { describe, it, expect, vi } from 'vitest';
import { buildIdentityAdapter, createMockIdentityService } from './identity.adapter';
import type { IIdentityService } from '../ports/identity.port';

const apiResponse = {
  user_id: 'u-1',
  email: 'alice@example.com',
  tenant_id: 'acme',
  roles: ['admin', 'user'],
  display_name: 'Alice',
  status: 'active',
};

function makeClient(response = apiResponse) {
  return { get: vi.fn().mockResolvedValue(response) };
}

describe('buildIdentityAdapter', () => {
  it('maps snake_case API response to camelCase domain', async () => {
    const service = buildIdentityAdapter(makeClient());
    const identity = await service.getIdentity();
    expect(identity.userId).toBe('u-1');
    expect(identity.email).toBe('alice@example.com');
    expect(identity.tenantId).toBe('acme');
    expect(identity.roles).toEqual(['admin', 'user']);
    expect(identity.displayName).toBe('Alice');
    expect(identity.status).toBe('active');
  });

  it('calls GET /identity/me on the provided client', async () => {
    const client = makeClient();
    await buildIdentityAdapter(client).getIdentity();
    expect(client.get).toHaveBeenCalledWith('/identity/me');
  });

  it('propagates errors from the HTTP client', async () => {
    const client = { get: vi.fn().mockRejectedValue(new Error('network error')) };
    await expect(buildIdentityAdapter(client).getIdentity()).rejects.toThrow('network error');
  });

  it('satisfies IIdentityService interface', () => {
    const service: IIdentityService = buildIdentityAdapter(makeClient());
    expect(typeof service.getIdentity).toBe('function');
  });
});

describe('createMockIdentityService', () => {
  it('returns a valid identity shape', async () => {
    const service = createMockIdentityService();
    const identity = await service.getIdentity();
    expect(identity.userId).toBeTruthy();
    expect(identity.email).toBeTruthy();
    expect(identity.tenantId).toBeTruthy();
    expect(Array.isArray(identity.roles)).toBe(true);
    expect(identity.displayName).toBeTruthy();
    expect(identity.status).toBeTruthy();
  });

  it('returns consistent dev defaults', async () => {
    const service = createMockIdentityService();
    const identity = await service.getIdentity();
    expect(identity.userId).toBe('dev-user');
    expect(identity.email).toBe('dev@localhost');
    expect(identity.tenantId).toBe('default');
    expect(identity.roles).toContain('volundr:admin');
    expect(identity.displayName).toBe('Dev User');
    expect(identity.status).toBe('active');
  });

  it('satisfies IIdentityService interface', () => {
    const service: IIdentityService = createMockIdentityService();
    expect(typeof service.getIdentity).toBe('function');
  });
});
