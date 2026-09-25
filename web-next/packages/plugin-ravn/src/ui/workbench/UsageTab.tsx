import { useMemo } from 'react';
import { Info } from 'lucide-react';
import type { Ravn } from '../../domain/ravn';
import { formatTokens, formatUsd } from '../../application/ravnWorkbench';
import { belongsToRavn, conversationTitle } from '../../application/conversations';
import { useRavnBudget } from '../hooks/useBudget';
import { useSessions } from '../hooks/useSessions';
import { errorText } from './errorText';

function Budget({ ravn }: { ravn: Ravn }) {
  const budget = useRavnBudget(ravn.id);
  if (budget.isLoading) return <p className="rw-muted">Loading budget…</p>;
  if (budget.isError) {
    return (
      <div className="rw-banner" data-tone="quiet" data-testid="ravn-budget-unavailable">
        <span className="rw-banner__icon">
          <Info size={18} aria-hidden="true" />
        </span>
        <div>
          <strong className="rw-banner__title">No budget cap to show</strong>
          <p className="rw-banner__text">
            {errorText(budget.error, 'The budget service did not answer')}
          </p>
        </div>
        <span />
      </div>
    );
  }
  const state = budget.data;
  if (!state) return null;
  const ratio = state.capUsd > 0 ? state.spentUsd / state.capUsd : 0;
  return (
    <div className="rw-budget" data-testid="ravn-budget">
      <div className="rw-bar">
        <span className="rw-bar__label">
          {formatUsd(state.spentUsd)} of {formatUsd(state.capUsd)} today
        </span>
        <span className="rw-budget__track">
          <span
            className="rw-budget__fill"
            data-over={ratio >= state.warnAt}
            style={{ width: `${Math.min(ratio, 1) * 100}%` }}
          />
        </span>
        <span className="rw-bar__value">{Math.round(ratio * 100)}%</span>
      </div>
      <p className="rw-muted">Warns at {Math.round(state.warnAt * 100)}% of the cap.</p>
    </div>
  );
}

export function UsageTab({ ravn }: { ravn: Ravn }) {
  const sessions = useSessions();
  const breakdown = useMemo(
    () =>
      (sessions.data ?? [])
        .filter((session) => belongsToRavn(session, ravn) && (session.messageCount ?? 0) > 0)
        .map((session) => ({
          id: session.id,
          label: conversationTitle(session),
          value: session.messageCount ?? 0,
        }))
        .sort((left, right) => right.value - left.value),
    [sessions.data, ravn],
  );
  const max = Math.max(1, ...breakdown.map((row) => row.value));
  const metered = ravn.costUsd !== undefined;

  return (
    <div className="rw-sheet" data-testid="ravn-usage-tab">
      <div className="rw-stats">
        <div className="rw-stat">
          <div className="rw-stat__k">Messages</div>
          <div className="rw-stat__v">{(ravn.messageCount ?? 0).toLocaleString()}</div>
          <div className="rw-stat__s">since deploy</div>
        </div>
        <div className="rw-stat">
          <div className="rw-stat__k">Tokens</div>
          <div className="rw-stat__v">{formatTokens(ravn.tokenCount ?? 0)}</div>
          <div className="rw-stat__s">in and out</div>
        </div>
        <div className="rw-stat">
          <div className="rw-stat__k">Cost</div>
          <div className="rw-stat__v">{formatUsd(ravn.costUsd ?? 0)}</div>
          <div className="rw-stat__s">
            {metered ? 'as reported by the runtime' : 'not reported'}
          </div>
        </div>
      </div>

      {breakdown.length > 0 && (
        <section>
          <h3 className="rw-sec__title">Messages by conversation</h3>
          <div className="rw-bars">
            {breakdown.map((row) => (
              <div key={row.id} className="rw-bar">
                <span className="rw-bar__label" title={row.label}>
                  {row.label}
                </span>
                <span className="rw-bar__track">
                  <span className="rw-bar__fill" style={{ width: `${(row.value / max) * 100}%` }} />
                </span>
                <span className="rw-bar__value">{row.value}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h3 className="rw-sec__title">Budget</h3>
        <Budget ravn={ravn} />
      </section>
    </div>
  );
}
