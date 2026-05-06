import { describe, it, expect, vi } from 'vitest';
import {
  buildFeatureCatalogAdapter,
  createMockFeatureCatalogService,
} from './feature-catalog.adapter';
import type { IFeatureCatalogService } from '../ports/feature-catalog.port';

const mockApiModules = [
  {
    key: 'users',
    label: 'Users',
    icon: 'Users',
    scope: 'admin',
    enabled: true,
    default_enabled: true,
    admin_only: true,
    order: 1,
  },
  {
    key: 'tenants',
    label: 'Tenants',
    icon: 'Building2',
    scope: 'admin',
    enabled: true,
    default_enabled: true,
    admin_only: true,
    order: 2,
  },
  {
    key: 'tokens',
    label: 'Access Tokens',
    icon: 'ShieldCheck',
    scope: 'user',
    enabled: true,
    default_enabled: true,
    admin_only: false,
    order: 1,
  },
  {
    key: 'credentials',
    label: 'Credentials',
    icon: 'KeyRound',
    scope: 'user',
    enabled: true,
    default_enabled: true,
    admin_only: false,
    order: 2,
  },
];

const mockApiPrefs = [{ feature_key: 'users', visible: true, sort_order: 1 }];

function makeClient() {
  return {
    get: vi.fn().mockImplementation((path: string) => {
      if (path.includes('preferences')) return Promise.resolve(mockApiPrefs);
      const scopeMatch = path.match(/scope=(\w+)/);
      if (scopeMatch)
        return Promise.resolve(mockApiModules.filter((f) => f.scope === scopeMatch[1]));
      return Promise.resolve(mockApiModules);
    }),
    put: vi.fn().mockImplementation((_path: string, body: unknown) => Promise.resolve(body)),
    patch: vi.fn().mockImplementation((path: string, body: { enabled: boolean }) => {
      const keyMatch = path.match(/features\/modules\/([^/]+)/);
      const key = keyMatch?.[1] ?? 'unknown';
      const base = mockApiModules.find((f) => f.key === key) ?? mockApiModules[0]!;
      return Promise.resolve({ ...base, key, enabled: body.enabled });
    }),
  };
}

describe('buildFeatureCatalogAdapter', () => {
  describe('getFeatureModules', () => {
    it('maps snake_case API response to camelCase domain', async () => {
      const service = buildFeatureCatalogAdapter(makeClient());
      const modules = await service.getFeatureModules();
      const first = modules[0]!;
      expect(first).toHaveProperty('defaultEnabled');
      expect(first).toHaveProperty('adminOnly');
      expect(first.defaultEnabled).toBe(true);
      expect(first.adminOnly).toBe(true);
    });

    it('returns all modules when no scope is provided', async () => {
      const service = buildFeatureCatalogAdapter(makeClient());
      const modules = await service.getFeatureModules();
      expect(modules.length).toBe(4);
      const scopes = new Set(modules.map((m) => m.scope));
      expect(scopes.has('admin')).toBe(true);
      expect(scopes.has('user')).toBe(true);
    });

    it('filters by admin scope', async () => {
      const service = buildFeatureCatalogAdapter(makeClient());
      const modules = await service.getFeatureModules('admin');
      expect(modules.every((m) => m.scope === 'admin')).toBe(true);
    });

    it('filters by user scope', async () => {
      const service = buildFeatureCatalogAdapter(makeClient());
      const modules = await service.getFeatureModules('user');
      expect(modules.every((m) => m.scope === 'user')).toBe(true);
    });

    it('appends ?scope= param when scope is provided', async () => {
      const client = makeClient();
      await buildFeatureCatalogAdapter(client).getFeatureModules('admin');
      expect(client.get).toHaveBeenCalledWith('/features/modules?scope=admin');
    });

    it('omits query param when no scope', async () => {
      const client = makeClient();
      await buildFeatureCatalogAdapter(client).getFeatureModules();
      expect(client.get).toHaveBeenCalledWith('/features/modules');
    });
  });

  describe('getUserFeaturePreferences', () => {
    it('maps snake_case preferences to camelCase', async () => {
      const service = buildFeatureCatalogAdapter(makeClient());
      const prefs = await service.getUserFeaturePreferences();
      expect(prefs[0]).toEqual({ featureKey: 'users', visible: true, sortOrder: 1 });
    });
  });

  describe('updateUserFeaturePreferences', () => {
    it('sends camelCase body as snake_case and maps response back', async () => {
      const client = makeClient();
      client.put = vi
        .fn()
        .mockResolvedValue([{ feature_key: 'tokens', visible: false, sort_order: 2 }]);
      const service = buildFeatureCatalogAdapter(client);
      const result = await service.updateUserFeaturePreferences([
        { featureKey: 'tokens', visible: false, sortOrder: 2 },
      ]);
      expect(result[0]).toEqual({ featureKey: 'tokens', visible: false, sortOrder: 2 });
      const [, body] = client.put.mock.calls[0] as [string, unknown[]];
      expect(body[0]).toEqual({ feature_key: 'tokens', visible: false, sort_order: 2 });
    });
  });

  describe('toggleFeature', () => {
    it('sends PATCH with enabled state and returns updated module', async () => {
      const service = buildFeatureCatalogAdapter(makeClient());
      const result = await service.toggleFeature('users', false);
      expect(result.key).toBe('users');
      expect(result.enabled).toBe(false);
    });

    it('can toggle feature on', async () => {
      const service = buildFeatureCatalogAdapter(makeClient());
      const result = await service.toggleFeature('tokens', true);
      expect(result.key).toBe('tokens');
      expect(result.enabled).toBe(true);
    });

    it('uses the canonical feature toggle route', async () => {
      const client = makeClient();
      await buildFeatureCatalogAdapter(client).toggleFeature('users', false);
      expect(client.patch).toHaveBeenCalledWith('/features/modules/users/toggle', {
        enabled: false,
      });
    });
  });

  it('satisfies IFeatureCatalogService interface', () => {
    const service: IFeatureCatalogService = buildFeatureCatalogAdapter(makeClient());
    expect(typeof service.getFeatureModules).toBe('function');
    expect(typeof service.getUserFeaturePreferences).toBe('function');
    expect(typeof service.updateUserFeaturePreferences).toBe('function');
    expect(typeof service.toggleFeature).toBe('function');
  });
});

describe('createMockFeatureCatalogService', () => {
  it('returns all 4 seed modules when no scope is given', async () => {
    const service = createMockFeatureCatalogService();
    const modules = await service.getFeatureModules();
    expect(modules.length).toBe(4);
  });

  it('filters by admin scope', async () => {
    const service = createMockFeatureCatalogService();
    const modules = await service.getFeatureModules('admin');
    expect(modules.length).toBeGreaterThan(0);
    expect(modules.every((m) => m.scope === 'admin')).toBe(true);
  });

  it('filters by user scope', async () => {
    const service = createMockFeatureCatalogService();
    const modules = await service.getFeatureModules('user');
    expect(modules.every((m) => m.scope === 'user')).toBe(true);
  });

  it('returns empty preferences', async () => {
    const service = createMockFeatureCatalogService();
    expect(await service.getUserFeaturePreferences()).toEqual([]);
  });

  it('echo-returns preferences on update', async () => {
    const service = createMockFeatureCatalogService();
    const input = [{ featureKey: 'tokens', visible: false, sortOrder: 2 }];
    expect(await service.updateUserFeaturePreferences(input)).toEqual(input);
  });

  it('toggles a feature', async () => {
    const service = createMockFeatureCatalogService();
    const result = await service.toggleFeature('users', false);
    expect(result.key).toBe('users');
    expect(result.enabled).toBe(false);
  });

  it('satisfies IFeatureCatalogService interface', () => {
    const service: IFeatureCatalogService = createMockFeatureCatalogService();
    expect(typeof service.getFeatureModules).toBe('function');
  });
});
