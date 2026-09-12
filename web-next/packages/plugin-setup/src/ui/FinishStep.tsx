import type { IntegrationConnection, SetupState } from '../domain/setup';
import { CheckIcon } from './icons';

export interface FinishStepProps {
  state: SetupState | undefined;
  connections: IntegrationConnection[] | undefined;
  finishing: boolean;
  error: Error | null;
}

const TYPE_LABELS: Record<string, string> = {
  ai_provider: 'AI providers',
  source_control: 'Git',
  issue_tracker: 'Tickets',
};

export function summarizeConnections(
  connections: IntegrationConnection[] | undefined,
): Array<{ label: string; value: string }> {
  const groups = new Map<string, string[]>();
  for (const connection of connections ?? []) {
    if (!connection.enabled) continue;
    const label = TYPE_LABELS[connection.integrationType] ?? connection.integrationType;
    const names = groups.get(label) ?? [];
    names.push(connection.slug || connection.credentialName);
    groups.set(label, names);
  }
  return Object.values(TYPE_LABELS).map((label) => ({
    label,
    value: (groups.get(label) ?? []).join(', ') || 'none',
  }));
}

export function FinishStep({ state, connections, finishing, error }: FinishStepProps) {
  const rows = summarizeConnections(connections);
  return (
    <div className="setup-col" data-testid="setup-finish">
      <div className="setup-card">
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">Your setup</h3>
            <p className="setup-card__desc">
              Everything here can be changed later in Settings → Integrations.
            </p>
          </div>
        </div>
        {rows.map((row) => (
          <div className="setup-row" key={row.label}>
            <span
              className={`setup-row__icon ${row.value === 'none' ? '' : 'setup-row__icon--ok'}`}
            >
              {row.value === 'none' ? null : <CheckIcon />}
            </span>
            <div className="setup-row__body">
              <span className="setup-row__title">{row.label}</span>
              <span className="setup-row__detail">{row.value}</span>
            </div>
          </div>
        ))}
        <div className="setup-row">
          <span className="setup-row__icon setup-row__icon--ok">
            <CheckIcon />
          </span>
          <div className="setup-row__body">
            <span className="setup-row__title">Platform</span>
            <span className="setup-row__detail">
              {state?.mode ? `${state.mode} mode` : 'running'} · sign-in is off on this install
              until you turn it on in Settings → Access
            </span>
          </div>
        </div>
      </div>
      {finishing ? <div className="setup-note">Saving…</div> : null}
      {error ? (
        <div className="setup-error" role="alert">
          Could not finish setup: {error.message}
        </div>
      ) : null}
    </div>
  );
}
