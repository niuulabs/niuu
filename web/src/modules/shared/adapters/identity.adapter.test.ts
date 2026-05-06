import { describe, it, expect, vi } from 'vitest';
import type { IIdentityService } from '@/modules/shared/ports/identity.port';

// Mock the API client to return data matching MockIdentityService defaults,
// so the test passes whether VITE_USE_REAL_API is set or not.
vi.mock('@/modules/shared/api/client', () => ({
  createApiClient: () => ({
    get: vi.fn().mockResolvedValue({
      user_id: 'dev-user',
      email: 'dev@localhost',
      tenant_id: 'default',
      roles: ['volundr:admin'],
      display_name: 'Dev User',
      status: 'active',
    }),
  }),
}));

import { identityService } from './identity.adapter';

describe('identityService', () => {
  it('returns a valid identity', async () => {
    const identity = await identityService.getIdentity();
    expect(identity).toHaveProperty('userId');
    expect(identity).toHaveProperty('email');
    expect(identity).toHaveProperty('tenantId');
    expect(identity).toHaveProperty('roles');
    expect(identity).toHaveProperty('displayName');
    expect(identity).toHaveProperty('status');
  });

  it('returns expected identity values', async () => {
    const identity = await identityService.getIdentity();
    expect(identity.userId).toBe('dev-user');
    expect(identity.email).toBe('dev@localhost');
    expect(identity.tenantId).toBe('default');
    expect(identity.roles).toContain('volundr:admin');
    expect(identity.displayName).toBe('Dev User');
    expect(identity.status).toBe('active');
  });

  it('implements IIdentityService interface', () => {
    const service: IIdentityService = identityService;
    expect(typeof service.getIdentity).toBe('function');
  });
});
