import { useMemo, type ReactNode } from 'react';
import {
  BudgetBar,
  Sparkline,
  StateDot,
  PersonaAvatar,
  LoadingState,
  ErrorState,
} from '@niuulabs/ui';
import type { ActivityLogEntry } from '../domain/activityLog';
import type { Ravn } from '../domain/ravn';
import type { Session } from '../domain/session';
import { useRavens } from './hooks/useRavens';
import { useTriggers } from './hooks/useTriggers';
import { useSessions } from './hooks/useSessions';
import { useFleetBudget, useRavnBudgets } from './hooks/useBudget';
import { useActivityLog } from './hooks/useActivityLog';
import { topBudgetSpenders } from './grouping';
import { formatTime } from './formatTime';
import './OverviewPage.css';

const TOP_SPENDERS_COUNT = 5;
const ACTIVE_RAVEN_LIMIT = 7;
const FLEET_SPARKLINE_WIDTH = 640;
const FLEET_SPARKLINE_HEIGHT = 132;
const FLEET_SPEND_SERIES = [
  0.56, 0.56, 0.58, 0.62, 0.67, 0.72, 0.72, 0.68, 0.64, 0.6, 0.6, 0.64, 0.69, 0.72, 0.73, 0.71,
  0.67, 0.63, 0.6, 0.62, 0.66, 0.71, 0.75, 0.76,
];

const ACTIVITY_KIND_LABEL: Record<ActivityLogEntry['kind'], string> = {
  session: 'ITER',
  trigger: 'TOOL',
  emit: 'EMIT',
};

function titleCase(value: string | undefined): string {
  if (!value) return 'unknown';
  return value.replace(/_/g, ' ').replace(/-/g, ' ');
}

function formatCurrency(value: number | undefined): string {
  if (value == null) return '—';
  return `$${value.toFixed(2)}`;
}

function formatTokenCount(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}

function percentOfCap(spent: number, cap: number): number {
  if (cap <= 0) return 0;
  return Math.round((spent / cap) * 100);
}

function sortByLocationCount(ravens: Ravn[]): Array<{ name: string; count: number }> {
  const counts = new Map<string, number>();

  for (const ravn of ravens) {
    const location = ravn.location ?? 'unknown';
    counts.set(location, (counts.get(location) ?? 0) + 1);
  }

  return Array.from(counts.entries())
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name));
}

function deriveAnchorTime(sessions: Session[]): string {
  if (sessions.length === 0) return new Date().toISOString();
  const newest = [...sessions].sort((left, right) =>
    right.createdAt.localeCompare(left.createdAt),
  )[0]!;
  return new Date(new Date(newest.createdAt).getTime() + 2 * 60 * 1000).toISOString();
}

function relativeAge(iso: string | undefined, anchorIso: string): string {
  if (!iso) return '—';
  const deltaMs = new Date(anchorIso).getTime() - new Date(iso).getTime();
  const deltaMinutes = Math.max(0, Math.round(deltaMs / 60000));
  if (deltaMinutes <= 1) return 'just now';
  if (deltaMinutes < 60) return `${deltaMinutes}m ago`;
  return `${Math.round(deltaMinutes / 60)}h ago`;
}

function roleLabel(role: string | undefined): string {
  return titleCase(role ?? 'raven');
}

function OverviewMetricCard({
  label,
  value,
  subline,
  accent = false,
  testId,
}: {
  label: string;
  value: string | number;
  subline: ReactNode;
  accent?: boolean;
  testId: string;
}) {
  return (
    <section className="rv-ov__metric" data-testid={testId}>
      <div className="rv-ov__metric-label">{label}</div>
      <div className={`rv-ov__metric-value${accent ? ' rv-ov__metric-value--accent' : ''}`}>
        {value}
      </div>
      <div className="rv-ov__metric-sub">{subline}</div>
    </section>
  );
}

function ActivityTail({ entries }: { entries: ActivityLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="rv-ov__log" data-testid="activity-log">
        <div className="rv-ov__log-empty">No recent activity</div>
      </div>
    );
  }

  return (
    <div className="rv-ov__log" data-testid="activity-log">
      {entries.map((entry) => (
        <div key={entry.id} className="rv-ov__log-row" data-testid="activity-log-row">
          <span className="rv-ov__log-time">{formatTime(entry.ts)}</span>
          <span className={`rv-ov__log-kind rv-ov__log-kind--${entry.kind}`}>
            {ACTIVITY_KIND_LABEL[entry.kind]}
          </span>
          <span className="rv-ov__log-raven">{entry.ravnId}</span>
          <span className="rv-ov__log-message">{entry.message}</span>
        </div>
      ))}
    </div>
  );
}

export function OverviewPage() {
  const ravens = useRavens();
  const triggers = useTriggers();
  const sessions = useSessions();
  const fleetBudget = useFleetBudget();
  const activityLog = useActivityLog();

  const ravnList = useMemo(() => ravens.data ?? [], [ravens.data]);
  const sessionList = useMemo(() => sessions.data ?? [], [sessions.data]);
  const triggerList = triggers.data ?? [];
  const ravnIds = ravnList.map((ravn) => ravn.id);
  const budgets = useRavnBudgets(ravnIds);

  const activeRavens = useMemo(
    () => ravnList.filter((ravn) => ravn.status === 'active'),
    [ravnList],
  );
  const idleCount = ravnList.filter((ravn) => ravn.status === 'idle').length;
  const failedCount = ravnList.filter((ravn) => ravn.status === 'failed').length;
  const suspendedCount = ravnList.filter((ravn) => ravn.status === 'suspended').length;

  const activeTriggers = triggerList.filter((trigger) => trigger.enabled).length;
  const pausedTriggers = triggerList.filter((trigger) => !trigger.enabled).length;
  const openSessions = sessionList.filter((session) => session.status === 'running');

  const totalMsgs = sessionList.reduce((sum, session) => sum + (session.messageCount ?? 0), 0);
  const totalTokens = sessionList.reduce((sum, session) => sum + (session.tokenCount ?? 0), 0);
  const fleetBudgetData = fleetBudget.data;

  const sessionsByRavn = useMemo(() => {
    const openCounts = new Map<string, number>();
    const latestSession = new Map<string, string>();

    for (const session of sessionList) {
      if (session.status === 'running') {
        openCounts.set(session.ravnId, (openCounts.get(session.ravnId) ?? 0) + 1);
      }

      const currentLatest = latestSession.get(session.ravnId);
      if (!currentLatest || session.createdAt > currentLatest) {
        latestSession.set(session.ravnId, session.createdAt);
      }
    }

    return { openCounts, latestSession };
  }, [sessionList]);

  const anchorTime = deriveAnchorTime(sessionList);

  const activeRows = useMemo(() => {
    return [...activeRavens]
      .sort((left, right) => {
        const leftLatest = sessionsByRavn.latestSession.get(left.id) ?? left.createdAt;
        const rightLatest = sessionsByRavn.latestSession.get(right.id) ?? right.createdAt;
        return rightLatest.localeCompare(leftLatest);
      })
      .slice(0, ACTIVE_RAVEN_LIMIT);
  }, [activeRavens, sessionsByRavn.latestSession]);

  const locationGroups = useMemo(() => sortByLocationCount(activeRavens), [activeRavens]);
  const maxLocationCount = locationGroups[0]?.count ?? 1;
  const topBurners = topBudgetSpenders(ravnList, budgets, TOP_SPENDERS_COUNT);

  if (ravens.isLoading || sessions.isLoading || triggers.isLoading) {
    return (
      <div data-testid="overview-loading">
        <LoadingState label="Loading fleet overview…" />
      </div>
    );
  }

  if (ravens.isError) {
    return (
      <div data-testid="overview-error">
        <ErrorState
          message={ravens.error instanceof Error ? ravens.error.message : 'Failed to load ravens'}
        />
      </div>
    );
  }

  return (
    <div data-testid="overview-page" className="rv-ov">
      <div className="rv-ov__kpis">
        <OverviewMetricCard
          testId="kpi-ravens"
          label="Ravens"
          value={ravnList.length}
          subline={
            <>
              <span className="rv-ov__sub-ok">{activeRavens.length} active</span>
              <span className="rv-ov__sub-sep">·</span>
              <span>{idleCount} idle</span>
              {failedCount > 0 && (
                <>
                  <span className="rv-ov__sub-sep">·</span>
                  <span className="rv-ov__sub-bad">{failedCount} failed</span>
                </>
              )}
              {suspendedCount > 0 && (
                <>
                  <span className="rv-ov__sub-sep">·</span>
                  <span className="rv-ov__sub-warn">{suspendedCount} suspended</span>
                </>
              )}
            </>
          }
        />

        <OverviewMetricCard
          testId="kpi-sessions"
          label="Open sessions"
          value={openSessions.length}
          subline={
            <>
              <span>{totalMsgs} msgs</span>
              <span className="rv-ov__sub-sep">·</span>
              <span>{formatTokenCount(totalTokens)} tok</span>
            </>
          }
        />

        <OverviewMetricCard
          testId="kpi-spend"
          label="Spend today"
          value={fleetBudgetData ? formatCurrency(fleetBudgetData.spentUsd) : '—'}
          accent
          subline={
            fleetBudgetData ? (
              <>
                <span>of {formatCurrency(fleetBudgetData.capUsd)} cap</span>
                <span className="rv-ov__sub-sep">·</span>
                <span>{percentOfCap(fleetBudgetData.spentUsd, fleetBudgetData.capUsd)}%</span>
              </>
            ) : (
              '—'
            )
          }
        />

        <OverviewMetricCard
          testId="kpi-triggers"
          label="Active triggers"
          value={activeTriggers}
          subline={<span>{pausedTriggers} paused</span>}
        />
      </div>

      <div className="rv-ov__body">
        <div className="rv-ov__col">
          <section>
            <div className="rv-ov__section-head">
              <h3 className="rv-ov__section-title">Active ravens</h3>
              <a className="rv-ov__section-link" href="/ravn/ravens">
                open directory →
              </a>
            </div>

            {activeRows.length === 0 ? (
              <p className="rv-ov__empty-text">No active ravens</p>
            ) : (
              <div className="rv-ov__active" data-testid="active-ravens-list">
                {activeRows.map((ravn) => {
                  const budgetState = budgets[ravn.id];
                  const openCount = sessionsByRavn.openCounts.get(ravn.id) ?? 0;
                  const latestAt = sessionsByRavn.latestSession.get(ravn.id) ?? ravn.createdAt;

                  return (
                    <div key={ravn.id} className="rv-ov__active-row" data-testid="active-ravn-row">
                      <span className="rv-ov__active-state">
                        <StateDot state="running" pulse size={8} />
                      </span>
                      <span className="rv-ov__active-avatar">
                        <PersonaAvatar
                          role={ravn.role ?? 'build'}
                          letter={ravn.letter ?? '?'}
                          size={20}
                        />
                      </span>
                      <span className="rv-ov__active-name">{ravn.personaName}</span>
                      <span className="rv-ov__active-role">{roleLabel(ravn.role)}</span>
                      <span className="rv-ov__active-loc">@ {titleCase(ravn.location)}</span>
                      <span className="rv-ov__active-sessions">{openCount} open</span>
                      <span className="rv-ov__active-budget">
                        {budgetState ? (
                          <BudgetBar
                            spent={budgetState.spentUsd}
                            cap={budgetState.capUsd}
                            warnAt={Math.round(budgetState.warnAt * 100)}
                            size="sm"
                          />
                        ) : null}
                      </span>
                      <span className="rv-ov__active-time">
                        {relativeAge(latestAt, anchorTime)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section className="rv-ov__section rv-ov__section--location">
            <div className="rv-ov__section-head">
              <h3 className="rv-ov__section-title">By location</h3>
              <span className="rv-ov__section-meta">active ravens</span>
            </div>

            <div className="rv-ov__location-panel" data-testid="location-bars">
              {locationGroups.map(({ name, count }) => (
                <div key={name} className="rv-ov__location-row" data-testid="location-row">
                  <span className="rv-ov__location-name">{name}</span>
                  <span className="rv-ov__location-track">
                    <span
                      className="rv-ov__location-fill"
                      style={{ width: `${(count / maxLocationCount) * 100}%` }}
                    />
                  </span>
                  <span className="rv-ov__location-count">{count}</span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="rv-ov__col">
          <section>
            <div className="rv-ov__section-head">
              <h3 className="rv-ov__section-title">Fleet spend · 24h</h3>
              <span className="rv-ov__section-meta">
                {fleetBudgetData ? `${formatCurrency(fleetBudgetData.spentUsd)} total` : '—'}
              </span>
            </div>

            <div className="rv-ov__chart" data-testid="fleet-sparkline">
              <Sparkline
                values={FLEET_SPEND_SERIES}
                width={FLEET_SPARKLINE_WIDTH}
                height={FLEET_SPARKLINE_HEIGHT}
                fill
              />
              <div className="rv-ov__chart-axis">
                <span>-24h</span>
                <span>-12h</span>
                <span>now</span>
              </div>
            </div>
          </section>

          <section className="rv-ov__section">
            <div className="rv-ov__section-head">
              <h3 className="rv-ov__section-title">Top burners</h3>
              <a className="rv-ov__section-link" href="/ravn/budget">
                budget page →
              </a>
            </div>

            <div className="rv-ov__burners" data-testid="top-spenders-list">
              {topBurners.map((ravn) => {
                const budgetState = budgets[ravn.id];
                const usage = budgetState
                  ? percentOfCap(budgetState.spentUsd, budgetState.capUsd)
                  : 0;

                return (
                  <div key={ravn.id} className="rv-ov__burner-row" data-testid="spender-row">
                    <span className="rv-ov__burner-avatar">
                      <PersonaAvatar
                        role={ravn.role ?? 'build'}
                        letter={ravn.letter ?? '?'}
                        size={18}
                      />
                    </span>
                    <span className="rv-ov__burner-name">{ravn.personaName}</span>
                    <span className="rv-ov__burner-bar">
                      {budgetState ? (
                        <BudgetBar
                          spent={budgetState.spentUsd}
                          cap={budgetState.capUsd}
                          warnAt={Math.round(budgetState.warnAt * 100)}
                          size="sm"
                        />
                      ) : null}
                    </span>
                    <span className="rv-ov__burner-pct">{usage}%</span>
                    <span className="rv-ov__burner-amount">
                      {budgetState ? formatCurrency(budgetState.spentUsd) : '—'}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="rv-ov__section">
            <div className="rv-ov__section-head">
              <h3 className="rv-ov__section-title">Recent activity</h3>
              <span className="rv-ov__section-meta">fleet tail · last 9</span>
            </div>

            <ActivityTail entries={activityLog.data ?? []} />
          </section>
        </div>
      </div>
    </div>
  );
}
