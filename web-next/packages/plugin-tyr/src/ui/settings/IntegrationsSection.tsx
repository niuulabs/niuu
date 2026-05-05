/**
 * Integrations section — shows tracker/repo/notifier connections.
 * Matches web2's `tyr.integrations` section.
 */

import { cn } from '@niuulabs/ui';

interface Integration {
  name: string;
  letter: string;
  connected: boolean;
  detail: string;
}

const INTEGRATIONS: Integration[] = [
  { name: 'Linear', letter: 'L', connected: true, detail: 'api key · ends ···g84' },
  { name: 'GitHub', letter: 'G', connected: true, detail: 'api key · ends ···g84' },
  { name: 'Jira', letter: 'J', connected: false, detail: 'not connected' },
  { name: 'Slack', letter: 'S', connected: true, detail: 'api key · ends ···g84' },
  { name: 'PagerDuty', letter: 'P', connected: false, detail: 'not connected' },
];

interface IntegrationCardProps {
  integration: Integration;
}

function IntegrationCard({ integration }: IntegrationCardProps) {
  return (
    <div
      className="niuu-flex niuu-items-center niuu-gap-3 niuu-p-3 niuu-border niuu-border-border niuu-rounded-md"
      data-testid={`integration-${integration.name.toLowerCase()}`}
    >
      <div className="niuu-w-8 niuu-h-8 niuu-rounded-md niuu-bg-bg-elevated niuu-flex niuu-items-center niuu-justify-center niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-shrink-0">
        {integration.letter}
      </div>
      <div className="niuu-flex-1 niuu-min-w-0">
        <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary">{integration.name}</p>
        <p className="niuu-text-xs niuu-font-mono niuu-text-text-muted niuu-mt-0.5">
          {integration.detail}
        </p>
      </div>
      <button
        type="button"
        className={cn(
          'niuu-px-3 niuu-py-1.5 niuu-rounded-md niuu-text-xs niuu-font-medium niuu-transition-colors niuu-shrink-0',
          integration.connected
            ? 'niuu-text-text-secondary niuu-border niuu-border-border hover:niuu-bg-bg-secondary'
            : 'niuu-bg-brand niuu-text-white',
        )}
      >
        {integration.connected ? 'Disconnect' : 'Connect'}
      </button>
    </div>
  );
}

export function IntegrationsSection() {
  return (
    <section aria-label="Integrations">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        Integrations
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        Trackers, repos, notifiers reachable by the saga coordinator.
      </p>

      <div
        className="niuu-flex niuu-flex-col niuu-gap-2 niuu-max-w-lg"
        role="list"
        aria-label="Integration list"
      >
        {INTEGRATIONS.map((integration) => (
          <div key={integration.name} role="listitem">
            <IntegrationCard integration={integration} />
          </div>
        ))}
      </div>
    </section>
  );
}
