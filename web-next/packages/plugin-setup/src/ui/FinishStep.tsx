import {
  describeStagedChanges,
  type ApplyStatus,
  type IntegrationConnection,
  type SetupState,
  type StackView,
} from '../domain/setup';
import { AlertIcon, CheckIcon } from './icons';

export interface FinishStepProps {
  state: SetupState | undefined;
  connections: IntegrationConnection[] | undefined;
  stack: StackView | undefined;
  /** Progress of the apply started by "Open Niuu", when there were staged changes. */
  apply: ApplyStatus | undefined;
  applying: boolean;
  /** True while the platform is restarting and not answering. */
  reconnecting: boolean;
  /** The address to open once the current one stops being served. */
  newAddress: string | null;
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

export function FinishStep({
  state,
  connections,
  stack,
  apply,
  applying,
  reconnecting,
  newAddress,
  finishing,
  error,
}: FinishStepProps) {
  const rows = summarizeConnections(connections);
  const staged = describeStagedChanges(stack);
  const vllm = stack?.effective.vllm;
  return (
    <div className="setup-col" data-testid="setup-finish">
      <div className="setup-card">
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">Your setup</h3>
            <p className="setup-card__desc">Everything here can be changed later in Settings.</p>
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
          <span className={`setup-row__icon ${vllm?.enabled ? 'setup-row__icon--ok' : ''}`}>
            {vllm?.enabled ? <CheckIcon /> : null}
          </span>
          <div className="setup-row__body">
            <span className="setup-row__title">Local model</span>
            <span className="setup-row__detail" data-testid="setup-finish-model">
              {vllm?.enabled ? `${vllm.model} · vLLM on this host` : 'none · cloud models only'}
            </span>
          </div>
        </div>
        <div className="setup-row">
          <span className="setup-row__icon setup-row__icon--ok">
            <CheckIcon />
          </span>
          <div className="setup-row__body">
            <span className="setup-row__title">Runtime & access</span>
            <span className="setup-row__detail" data-testid="setup-finish-access">
              sessions in containers ·{' '}
              {stack
                ? stack.effective.bindHost === '127.0.0.1'
                  ? 'this machine only'
                  : `your network (${stack.effective.accessUrls[1] ?? stack.effective.accessUrls[0]})`
                : state?.mode
                  ? `${state.mode} mode`
                  : 'running'}{' '}
              · sign-in off until you turn it on in Settings → Access
            </span>
          </div>
        </div>
      </div>

      {staged.length > 0 && !applying && !apply ? (
        <div className="setup-card" data-testid="setup-finish-staged">
          <div className="setup-card__head">
            <div>
              <h3 className="setup-card__title">Applied when you open Niuu</h3>
              <p className="setup-card__desc">
                The platform restarts the services whose configuration changed. This takes about a
                minute; a local model starts downloading right after.
              </p>
            </div>
          </div>
          {staged.map((line) => (
            <div className="setup-row" key={line}>
              <span className="setup-row__icon setup-row__icon--ok">
                <CheckIcon />
              </span>
              <div className="setup-row__body">
                <span className="setup-row__title">{line}</span>
              </div>
            </div>
          ))}
          {newAddress ? (
            <div className="setup-note setup-note--warn" data-testid="setup-finish-new-address">
              <AlertIcon size={13} /> This address stops working after the change. Continue at{' '}
              {newAddress}.
            </div>
          ) : null}
        </div>
      ) : null}

      {applying || apply ? (
        <div className="setup-card" data-testid="setup-finish-progress">
          {apply?.state === 'failed' ? (
            <div className="setup-error" role="alert" data-testid="setup-apply-failed">
              Applying the changes failed: {apply.detail}
            </div>
          ) : apply?.state === 'applied' ? (
            <div className="setup-note" data-testid="setup-apply-done">
              <CheckIcon size={13} /> Changes applied.{' '}
              {apply.vllm ? `Local model: ${apply.vllm.state}.` : ''}
            </div>
          ) : (
            <div className="setup-progress" data-testid="setup-apply-progress">
              <span className="setup-dot" />
              <span>
                {reconnecting
                  ? 'Restarting the platform… waiting for it to answer again.'
                  : 'Applying your changes…'}
              </span>
            </div>
          )}
          {apply?.vllm && apply.state !== 'applied' ? (
            <div className="setup-note" data-testid="setup-apply-vllm">
              Local model: {apply.vllm.state}
              {apply.vllm.detail ? ` · ${apply.vllm.detail}` : ''}
            </div>
          ) : null}
        </div>
      ) : null}

      {finishing && !applying ? <div className="setup-note">Saving…</div> : null}
      {error ? (
        <div className="setup-error" role="alert">
          Could not finish setup: {error.message}
        </div>
      ) : null}
    </div>
  );
}
