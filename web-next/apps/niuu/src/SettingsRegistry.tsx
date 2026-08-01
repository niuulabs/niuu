import { useMemo } from 'react';
import {
  useConfig,
  type MountedSettingsProviderDescriptor,
  type NiuuConfig,
  type SettingsScope,
} from '@niuulabs/plugin-sdk';
import { resolveSettingsServiceBase } from './services';

export interface RemoteSettingsOption {
  label: string;
  value: string;
}

export interface RemoteSettingsField {
  key: string;
  label: string;
  type: 'text' | 'textarea' | 'number' | 'boolean' | 'select';
  value: unknown;
  description?: string;
  placeholder?: string;
  readOnly?: boolean;
  secret?: boolean;
  options?: RemoteSettingsOption[];
}

export interface RemoteSettingsTokensResource {
  id: string;
  type: 'tokens';
  label: string;
  description?: string;
  writable?: boolean;
  listPath: string;
  createPath: string;
  deletePath: string;
}

export interface RemoteSettingsCredentialsResource {
  id: string;
  type: 'credentials';
  label: string;
  description?: string;
  writable?: boolean;
  listPath: string;
  typesPath: string;
  createPath: string;
  deletePath: string;
}

export interface RemoteSettingsIntegrationsResource {
  id: string;
  type: 'integrations';
  label: string;
  description?: string;
  writable?: boolean;
  listPath: string;
  catalogPath: string;
  createPath: string;
  deletePath: string;
  credentialListPath: string;
  testPath: string;
  oauthAuthorizePath: string;
  oauthDisconnectPath: string;
  enrollmentStartPath?: string;
  enrollmentStatusPath?: string;
  enrollmentCancelPath?: string;
}

export type RemoteSettingsResource =
  | RemoteSettingsTokensResource
  | RemoteSettingsCredentialsResource
  | RemoteSettingsIntegrationsResource;

export interface RemoteSettingsSectionSchema {
  id: string;
  label: string;
  description?: string;
  path?: string;
  saveLabel?: string;
  fields: RemoteSettingsField[];
  resources?: RemoteSettingsResource[];
}

export interface RemoteSettingsProviderSchema {
  title?: string;
  subtitle?: string;
  scope?: SettingsScope;
  sections: RemoteSettingsSectionSchema[];
}

export type MountedSettingsProvider =
  | ({ source: 'local' } & MountedSettingsProviderDescriptor)
  | {
      source: 'remote';
      id: string;
      pluginId: string;
      title: string;
      subtitle?: string;
      scope: SettingsScope;
      baseUrl: string | null;
      defaultSectionId?: string;
    };

const LOCAL_PROVIDERS: MountedSettingsProviderDescriptor[] = [];

const REMOTE_PROVIDER_DEFS = [
  {
    id: 'identity',
    pluginId: 'login',
    title: 'You',
    subtitle: 'personal settings',
    scope: 'user' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'identity'),
  },
  {
    id: 'credentials',
    pluginId: 'credentials',
    title: 'Credentials',
    subtitle: 'stored secrets and runtime keys',
    scope: 'user' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'credentials'),
  },
  {
    id: 'integrations',
    pluginId: 'integrations',
    title: 'Integrations',
    subtitle: 'connected services and providers',
    scope: 'user' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'integrations'),
  },
  {
    id: 'volundr',
    pluginId: 'volundr',
    title: 'Volundr',
    subtitle: 'forge platform settings',
    scope: 'service' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'volundr'),
  },
  {
    id: 'ting',
    pluginId: 'ting',
    title: 'Ting',
    subtitle: 'saga coordinator settings',
    scope: 'service' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'ting'),
  },
  {
    id: 'mimir',
    pluginId: 'mimir',
    title: 'Mimir',
    subtitle: 'knowledge system settings',
    scope: 'service' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'mimir'),
  },
  {
    id: 'bifrost',
    pluginId: 'bifrost',
    title: 'Bifrost',
    subtitle: 'model catalog and routing settings',
    scope: 'service' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'bifrost'),
  },
  {
    id: 'ravn',
    pluginId: 'ravn',
    title: 'Ravn',
    subtitle: 'runtime and agent settings',
    scope: 'service' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'ravn'),
  },
  {
    id: 'observatory',
    pluginId: 'observatory',
    title: 'Observatory',
    subtitle: 'topology and event settings',
    scope: 'service' as const,
    resolver: (config: NiuuConfig) => resolveSettingsServiceBase(config, 'observatory'),
  },
] as const;

function isPluginEnabled(config: NiuuConfig, pluginId: string): boolean {
  return config.plugins[pluginId]?.enabled === true;
}

export function buildMountedSettingsProviders(config: NiuuConfig): MountedSettingsProvider[] {
  const localPluginIds = new Set(LOCAL_PROVIDERS.map((provider) => provider.pluginId));

  const providers: MountedSettingsProvider[] = LOCAL_PROVIDERS.filter((provider) =>
    isPluginEnabled(config, provider.pluginId),
  ).map((provider) => ({ ...provider, source: 'local' as const }));

  for (const def of REMOTE_PROVIDER_DEFS) {
    if (localPluginIds.has(def.pluginId)) continue;
    if (
      !isPluginEnabled(config, def.pluginId) &&
      !(def.id === 'identity' && def.resolver(config)) &&
      !(def.id === 'credentials' && def.resolver(config)) &&
      !(def.id === 'integrations' && def.resolver(config))
    ) {
      continue;
    }
    providers.push({
      source: 'remote',
      id: def.id,
      pluginId: def.pluginId,
      title: def.title,
      subtitle: def.subtitle,
      scope: def.scope,
      baseUrl: def.resolver(config),
    });
  }

  providers.sort((left, right) => {
    const leftOrder = left.id === 'identity' ? -1 : (config.plugins[left.pluginId]?.order ?? 100);
    const rightOrder =
      right.id === 'identity' ? -1 : (config.plugins[right.pluginId]?.order ?? 100);
    return leftOrder - rightOrder || left.title.localeCompare(right.title);
  });

  return providers;
}

export function useMountedSettingsProviders(): MountedSettingsProvider[] {
  const config = useConfig();
  return useMemo(() => buildMountedSettingsProviders(config), [config]);
}
