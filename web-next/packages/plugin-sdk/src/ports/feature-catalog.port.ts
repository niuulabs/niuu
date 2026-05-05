/**
 * Feature catalog port — manages feature modules and per-user visibility preferences.
 *
 * The Settings and Admin pages use this to discover which sections to render,
 * independently of any specific module's service.
 */

export type FeatureScope = 'admin' | 'user' | 'session';

export interface FeatureModule {
  key: string;
  label: string;
  icon: string;
  scope: FeatureScope;
  enabled: boolean;
  defaultEnabled: boolean;
  adminOnly: boolean;
  order: number;
}

export interface UserFeaturePreference {
  featureKey: string;
  visible: boolean;
  sortOrder: number;
}

export interface IFeatureCatalogService {
  getFeatureModules(scope?: FeatureScope): Promise<FeatureModule[]>;
  getUserFeaturePreferences(): Promise<UserFeaturePreference[]>;
  updateUserFeaturePreferences(
    preferences: UserFeaturePreference[],
  ): Promise<UserFeaturePreference[]>;
  toggleFeature(key: string, enabled: boolean): Promise<FeatureModule>;
}
