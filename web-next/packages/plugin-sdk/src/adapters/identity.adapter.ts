/**
 * Identity adapter — fetches the current user's identity from the API.
 *
 * Targets the canonical shared identity surface.
 */

import type { AppIdentity, IIdentityService } from '../ports/identity.port';

/** Minimal HTTP client interface — structurally compatible with ApiClient from @niuulabs/query. */
interface HttpClient {
  get<T>(endpoint: string): Promise<T>;
}

interface ApiIdentityResponse {
  user_id: string;
  email: string;
  tenant_id: string;
  roles: string[];
  display_name: string;
  status: string;
}

function rowToIdentity(r: ApiIdentityResponse): AppIdentity {
  return {
    userId: r.user_id,
    email: r.email,
    tenantId: r.tenant_id,
    roles: r.roles,
    displayName: r.display_name,
    status: r.status,
  };
}

/**
 * Build an identity service backed by a live HTTP client.
 * Pass `createApiClient('/api/v1')` from @niuulabs/query.
 */
export function buildIdentityAdapter(client: HttpClient): IIdentityService {
  return {
    async getIdentity(): Promise<AppIdentity> {
      const r = await client.get<ApiIdentityResponse>('/identity/me');
      return rowToIdentity(r);
    },
  };
}

/**
 * In-memory mock — suitable for dev, Storybook, and tests.
 */
export function createMockIdentityService(): IIdentityService {
  return {
    async getIdentity(): Promise<AppIdentity> {
      return {
        userId: 'dev-user',
        email: 'dev@localhost',
        tenantId: 'default',
        roles: ['volundr:admin'],
        displayName: 'Dev User',
        status: 'active',
      };
    },
  };
}
