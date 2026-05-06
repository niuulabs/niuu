import type { ReactNode } from 'react';

export type SettingsScope = 'user' | 'service' | 'admin';

export interface MountedSettingsSectionDescriptor {
  id: string;
  label: string;
  description: string;
  render: () => ReactNode;
}

export interface MountedSettingsProviderDescriptor {
  id: string;
  pluginId: string;
  title: string;
  subtitle?: string;
  scope: SettingsScope;
  sections: MountedSettingsSectionDescriptor[];
  defaultSectionId?: string;
}
