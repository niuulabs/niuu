import { Link } from '@tanstack/react-router';
import { GeneralSection } from './GeneralSection';
import { DispatchDefaultsSection } from './DispatchDefaultsSection';
import { IntegrationsSection } from './IntegrationsSection';
import { PersonasSection } from './PersonasSection';
import { GatesReviewersSection } from './GatesReviewersSection';
import { FlockConfigSection } from './FlockConfigSection';
import { NotificationsSection } from './NotificationsSection';
import { AdvancedSection } from './AdvancedSection';
import { AuditLogSection } from './AuditLogSection';

export type SettingsSectionId =
  | 'general'
  | 'dispatch'
  | 'integrations'
  | 'personas'
  | 'gates'
  | 'flock'
  | 'notifications'
  | 'advanced'
  | 'audit';

interface SettingsPageProps {
  section: SettingsSectionId;
}

export function SettingsPage({ section }: SettingsPageProps) {
  return (
    <div className="niuu-p-6 niuu-max-w-[900px]">
      {section === 'general' && <GeneralSection />}
      {section === 'dispatch' && <DispatchDefaultsSection />}
      {section === 'integrations' && <IntegrationsSection />}
      {section === 'personas' && <PersonasSection />}
      {section === 'gates' && <GatesReviewersSection />}
      {section === 'flock' && <FlockConfigSection />}
      {section === 'notifications' && <NotificationsSection />}
      {section === 'advanced' && <AdvancedSection />}
      {section === 'audit' && <AuditLogSection />}
    </div>
  );
}

const SECTION_ITEMS = [
  {
    id: 'general',
    label: 'General',
    description: 'Core service bindings for the saga coordinator',
  },
  {
    id: 'dispatch',
    label: 'Dispatch rules',
    description: 'Confidence thresholds, batch sizes, and retry policy',
  },
  {
    id: 'integrations',
    label: 'Integrations',
    description: 'Trackers, repos, notifiers reachable by the saga coordinator',
  },
  {
    id: 'personas',
    label: 'Persona overrides',
    description: 'Browse and inspect Ravn persona configurations',
  },
  {
    id: 'gates',
    label: 'Gates & reviewers',
    description: 'Who can approve gates in workflows and routing rules',
  },
  {
    id: 'flock',
    label: 'Flock Config',
    description: 'Global defaults for new Sagas and Raids',
  },
  {
    id: 'notifications',
    label: 'Notifications',
    description: 'Event triggers and delivery channels',
  },
  {
    id: 'advanced',
    label: 'Advanced',
    description: 'Danger-zone actions for the dispatcher',
  },
  {
    id: 'audit',
    label: 'Audit Log',
    description: 'Immutable record of settings changes and dispatch events',
  },
] as const;

export function SettingsIndexPage() {
  return (
    <div className="niuu-p-6 niuu-max-w-[720px]">
      <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary niuu-mb-2">
        Tyr Settings
      </h2>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-6">
        Configure your Tyr deployment. Select a section from the left to get started.
      </p>

      <ul className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-list-none niuu-p-0 niuu-m-0">
        {SECTION_ITEMS.map((item) => (
          <li key={item.id}>
            <Link
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              to={`/tyr/settings/${item.id}` as any}
              className="niuu-block niuu-w-full niuu-text-left niuu-p-4 niuu-border niuu-border-border niuu-rounded-md hover:niuu-bg-bg-secondary niuu-transition-colors niuu-no-underline"
            >
              <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-1">
                {item.label}
              </p>
              <p className="niuu-text-xs niuu-text-text-secondary">{item.description}</p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
