import { describe, it, expect, vi } from 'vitest';
import type { IFeatureCatalogService } from '@/modules/shared/ports/feature-catalog.port';

const mockFeatures = [
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

vi.mock('@/modules/shared/api/client', () => ({
  createApiClient: () => ({
    get: vi.fn().mockImplementation((path: string) => {
      if (path.includes('preferences')) return Promise.resolve([]);
      const scopeMatch = path.match(/scope=(\w+)/);
      if (scopeMatch) return Promise.resolve(mockFeatures.filter(f => f.scope === scopeMatch[1]));
      return Promise.resolve(mockFeatures);
    }),
    put: vi.fn().mockImplementation((_path: string, body: unknown) => Promise.resolve(body)),
    patch: vi.fn().mockImplementation((path: string, body: { enabled: boolean }) => {
      const keyMatch = path.match(/features\/(\w+)/);
      const key = keyMatch?.[1] ?? 'unknown';
      const base = mockFeatures.find(f => f.key === key) ?? mockFeatures[0];
      return Promise.resolve({ ...base, key, enabled: body.enabled });
    }),
  }),
}));

import { featureCatalogService } from './feature-catalog.adapter';

describe('featureCatalogService', () => {
  describe('getFeatureModules', () => {
    it('returns all features when no scope is provided', async () => {
      const modules = await featureCatalogService.getFeatureModules();
      expect(modules.length).toBeGreaterThan(0);
      const scopes = new Set(modules.map(m => m.scope));
      expect(scopes.has('admin')).toBe(true);
      expect(scopes.has('user')).toBe(true);
    });

    it('filters by admin scope', async () => {
      const modules = await featureCatalogService.getFeatureModules('admin');
      expect(modules.length).toBeGreaterThan(0);
      expect(modules.every(m => m.scope === 'admin')).toBe(true);
    });

    it('filters by user scope', async () => {
      const modules = await featureCatalogService.getFeatureModules('user');
      expect(modules.length).toBeGreaterThan(0);
      expect(modules.every(m => m.scope === 'user')).toBe(true);
    });

    it('returns feature modules with expected shape', async () => {
      const modules = await featureCatalogService.getFeatureModules();
      const first = modules[0];
      expect(first).toHaveProperty('key');
      expect(first).toHaveProperty('label');
      expect(first).toHaveProperty('icon');
      expect(first).toHaveProperty('scope');
      expect(first).toHaveProperty('enabled');
      expect(first).toHaveProperty('defaultEnabled');
      expect(first).toHaveProperty('adminOnly');
      expect(first).toHaveProperty('order');
    });
  });

  describe('getUserFeaturePreferences', () => {
    it('returns empty array', async () => {
      const prefs = await featureCatalogService.getUserFeaturePreferences();
      expect(prefs).toEqual([]);
    });
  });

  describe('updateUserFeaturePreferences', () => {
    it('returns the same preferences passed in', async () => {
      const input = [
        { featureKey: 'users', visible: true, sortOrder: 1 },
        { featureKey: 'tokens', visible: false, sortOrder: 2 },
      ];
      const result = await featureCatalogService.updateUserFeaturePreferences(input);
      expect(result).toEqual(input);
    });
  });

  describe('toggleFeature', () => {
    it('returns a feature module with the toggled enabled state', async () => {
      const result = await featureCatalogService.toggleFeature('users', false);
      expect(result.key).toBe('users');
      expect(result.enabled).toBe(false);
    });

    it('returns enabled true when toggled on', async () => {
      const result = await featureCatalogService.toggleFeature('tokens', true);
      expect(result.key).toBe('tokens');
      expect(result.enabled).toBe(true);
    });
  });

  it('implements all IFeatureCatalogService methods', () => {
    const service: IFeatureCatalogService = featureCatalogService;
    expect(typeof service.getFeatureModules).toBe('function');
    expect(typeof service.getUserFeaturePreferences).toBe('function');
    expect(typeof service.updateUserFeaturePreferences).toBe('function');
    expect(typeof service.toggleFeature).toBe('function');
  });
});
