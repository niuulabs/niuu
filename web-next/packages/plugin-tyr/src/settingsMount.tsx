import type { MountedSettingsProviderDescriptor } from '@niuulabs/plugin-sdk';
import { GeneralSection } from './ui/settings/GeneralSection';
import { DispatchDefaultsSection } from './ui/settings/DispatchDefaultsSection';
import { IntegrationsSection } from './ui/settings/IntegrationsSection';
import { PersonasSection } from './ui/settings/PersonasSection';
import { GatesReviewersSection } from './ui/settings/GatesReviewersSection';
import { FlockConfigSection } from './ui/settings/FlockConfigSection';
import { NotificationsSection } from './ui/settings/NotificationsSection';
import { AdvancedSection } from './ui/settings/AdvancedSection';
import { AuditLogSection } from './ui/settings/AuditLogSection';

export const tyrMountedSettingsProvider: MountedSettingsProviderDescriptor = {
  id: 'tyr',
  pluginId: 'tyr',
  title: 'Tyr',
  subtitle: 'saga coordinator settings',
  scope: 'service',
  defaultSectionId: 'general',
  sections: [
    {
      id: 'general',
      label: 'General',
      description: 'Core service bindings for the saga coordinator',
      render: () => <GeneralSection />,
    },
    {
      id: 'dispatch',
      label: 'Dispatch rules',
      description: 'Confidence thresholds, batch sizes, and retry policy',
      render: () => <DispatchDefaultsSection />,
    },
    {
      id: 'integrations',
      label: 'Integrations',
      description: 'Trackers, repos, and notifiers reachable by the saga coordinator',
      render: () => <IntegrationsSection />,
    },
    {
      id: 'personas',
      label: 'Persona overrides',
      description: 'Browse and inspect Ravn persona configurations',
      render: () => <PersonasSection />,
    },
    {
      id: 'gates',
      label: 'Gates & reviewers',
      description: 'Who can approve gates in workflows and routing rules',
      render: () => <GatesReviewersSection />,
    },
    {
      id: 'flock',
      label: 'Flock Config',
      description: 'Global defaults for new sagas and raids',
      render: () => <FlockConfigSection />,
    },
    {
      id: 'notifications',
      label: 'Notifications',
      description: 'Event triggers and delivery channels',
      render: () => <NotificationsSection />,
    },
    {
      id: 'advanced',
      label: 'Advanced',
      description: 'Danger-zone actions for the dispatcher',
      render: () => <AdvancedSection />,
    },
    {
      id: 'audit',
      label: 'Audit Log',
      description: 'Immutable record of settings changes and dispatch events',
      render: () => <AuditLogSection />,
    },
  ],
};
