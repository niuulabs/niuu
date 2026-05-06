/**
 * Feature catalog adapter — targets the canonical shared feature routes.
 */

import type {
  IFeatureCatalogService,
  FeatureScope,
  FeatureModule,
  UserFeaturePreference,
} from '../ports/feature-catalog.port';

/** Minimal HTTP client interface — structurally compatible with ApiClient from @niuulabs/query. */
interface HttpClient {
  get<T>(endpoint: string): Promise<T>;
  put<T>(endpoint: string, body: unknown): Promise<T>;
  patch<T>(endpoint: string, body: unknown): Promise<T>;
}

interface ApiFeatureModule {
  key: string;
  label: string;
  icon: string;
  scope: string;
  enabled: boolean;
  default_enabled: boolean;
  admin_only: boolean;
  order: number;
}

interface ApiUserFeaturePreference {
  feature_key: string;
  visible: boolean;
  sort_order: number;
}

function rowToModule(f: ApiFeatureModule): FeatureModule {
  return {
    key: f.key,
    label: f.label,
    icon: f.icon,
    scope: f.scope as FeatureScope,
    enabled: f.enabled,
    defaultEnabled: f.default_enabled,
    adminOnly: f.admin_only,
    order: f.order,
  };
}

function rowToPref(p: ApiUserFeaturePreference): UserFeaturePreference {
  return { featureKey: p.feature_key, visible: p.visible, sortOrder: p.sort_order };
}

/**
 * Build a feature-catalog service backed by a live HTTP client.
 * Pass `createApiClient('/api/v1')` from @niuulabs/query.
 */
export function buildFeatureCatalogAdapter(client: HttpClient): IFeatureCatalogService {
  return {
    async getFeatureModules(scope?: FeatureScope): Promise<FeatureModule[]> {
      const params = scope ? `?scope=${scope}` : '';
      const response = await client.get<ApiFeatureModule[]>(`/features/modules${params}`);
      return response.map(rowToModule);
    },

    async getUserFeaturePreferences(): Promise<UserFeaturePreference[]> {
      const response = await client.get<ApiUserFeaturePreference[]>('/features/preferences');
      return response.map(rowToPref);
    },

    async updateUserFeaturePreferences(
      preferences: UserFeaturePreference[],
    ): Promise<UserFeaturePreference[]> {
      const body = preferences.map((p) => ({
        feature_key: p.featureKey,
        visible: p.visible,
        sort_order: p.sortOrder,
      }));
      const response = await client.put<ApiUserFeaturePreference[]>('/features/preferences', body);
      return response.map(rowToPref);
    },

    async toggleFeature(key: string, enabled: boolean): Promise<FeatureModule> {
      const response = await client.patch<ApiFeatureModule>(`/features/modules/${key}/toggle`, {
        enabled,
      });
      return rowToModule(response);
    },
  };
}

/**
 * In-memory mock — suitable for dev, Storybook, and tests.
 */
export function createMockFeatureCatalogService(): IFeatureCatalogService {
  const all: FeatureModule[] = [
    {
      key: 'users',
      label: 'Users',
      icon: 'Users',
      scope: 'admin',
      enabled: true,
      defaultEnabled: true,
      adminOnly: true,
      order: 1,
    },
    {
      key: 'tenants',
      label: 'Tenants',
      icon: 'Building2',
      scope: 'admin',
      enabled: true,
      defaultEnabled: true,
      adminOnly: true,
      order: 2,
    },
    {
      key: 'tokens',
      label: 'Access Tokens',
      icon: 'ShieldCheck',
      scope: 'user',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 1,
    },
    {
      key: 'credentials',
      label: 'Credentials',
      icon: 'KeyRound',
      scope: 'user',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 2,
    },
  ];

  return {
    async getFeatureModules(scope?: FeatureScope): Promise<FeatureModule[]> {
      if (!scope) return all;
      return all.filter((f) => f.scope === scope);
    },

    async getUserFeaturePreferences(): Promise<UserFeaturePreference[]> {
      return [];
    },

    async updateUserFeaturePreferences(
      prefs: UserFeaturePreference[],
    ): Promise<UserFeaturePreference[]> {
      return prefs;
    },

    async toggleFeature(key: string, enabled: boolean): Promise<FeatureModule> {
      return {
        key,
        label: key,
        icon: 'Box',
        scope: 'user',
        enabled,
        defaultEnabled: true,
        adminOnly: false,
        order: 0,
      };
    },
  };
}
