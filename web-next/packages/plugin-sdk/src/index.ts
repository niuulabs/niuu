export {
  definePlugin,
  type PluginDescriptor,
  type PluginCtx,
  type PluginTab,
} from './PluginDescriptor';
export { ServicesProvider, useService, type ServicesMap } from './ServicesProvider';
export { ConfigProvider, useConfig } from './ConfigProvider';
export { FeatureCatalogProvider, useFeatureCatalog, type FeatureCatalog } from './FeatureCatalog';
export {
  niuuConfigSchema,
  pluginConfigSchema,
  serviceConfigSchema,
  type NiuuConfig,
  type PluginConfig,
  type ServiceConfig,
} from './config';
export type {
  SettingsScope,
  MountedSettingsSectionDescriptor,
  MountedSettingsProviderDescriptor,
} from './mounted-settings';

// Ports
export type { AppIdentity, IIdentityService } from './ports/identity.port';
export type {
  FeatureScope,
  FeatureModule,
  UserFeaturePreference,
  IFeatureCatalogService,
} from './ports/feature-catalog.port';

// Adapters
export { buildIdentityAdapter, createMockIdentityService } from './adapters/identity.adapter';
export {
  buildFeatureCatalogAdapter,
  createMockFeatureCatalogService,
} from './adapters/feature-catalog.adapter';
