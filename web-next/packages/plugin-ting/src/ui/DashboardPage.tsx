import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import {
  StatusBadge,
  ConfidenceBadge,
  LoadingState,
  ErrorState,
  Sparkline,
  Pipe,
  StateDot,
  ToastProvider,
  useToast,
} from '@niuulabs/ui';
import type { BadgeStatus } from '@niuulabs/ui';
import type { ITingService, Phase, IDispatcherService, DispatcherActivityEvent } from '../ports';
import type { Saga, Run, RunStatus } from '../domain/saga';
import { useSagas } from './useSagas';
import { useDispatcherState } from './useDispatcherState';
import { RunMeshCanvas } from './RunMeshCanvas';
import './DashboardPage.css';

type PipeCell = { status: 'ok' | 'run' | 'warn' | 'crit' | 'gate' | 'pend'; label: string };
type FeedRow = {
  id: string;
  timestamp: string;
  state: 'merged' | 'failed' | 'running' | 'review';
  summary: string;
  subject: string;
  sagaId?: string;
};

const ACTIVE_RUN_STATUSES = new Set<RunStatus>(['running', 'review', 'escalated']);
const TERMINAL_RUN_STATUSES = new Set<RunStatus>(['merged', 'failed']);
const HOURS_IN_WINDOW = 24;

function runStatusToCell(s: RunStatus): PipeCell['status'] {
  const map: Record<RunStatus, PipeCell['status']> = {
    merged: 'ok',
    running: 'run',
    review: 'warn',
    queued: 'warn',
    pending: 'pend',
    escalated: 'warn',
    failed: 'crit',
  };
  return map[s] ?? 'pend';
}

function sagaPipe(phases: Phase[]): PipeCell[] {
  return phases.flatMap((p) =>
    p.runs.map((r) => ({ status: runStatusToCell(r.status), label: r.name })),
  );
}

function deriveSagaBadge(phases: Phase[]): BadgeStatus {
  const runs = phases.flatMap((p) => p.runs);
  if (runs.some((r) => r.status === 'failed')) return 'failed';
  if (runs.some((r) => r.status === 'running')) return 'running';
  if (runs.some((r) => r.status === 'review' || r.status === 'escalated')) return 'review';
  return 'running';
}

function bucketRunsByHour(runs: Run[], predicate: (run: Run) => boolean): number[] {
  const now = Date.now();
  const start = now - HOURS_IN_WINDOW * 60 * 60 * 1000;
  const series = Array.from({ length: HOURS_IN_WINDOW }, () => 0);

  for (const run of runs) {
    if (!predicate(run)) continue;
    const timestamp = Date.parse(run.updatedAt);
    if (!Number.isFinite(timestamp) || timestamp < start || timestamp > now) continue;
    const hourIndex = Math.min(
      HOURS_IN_WINDOW - 1,
      Math.max(0, Math.floor((timestamp - start) / (60 * 60 * 1000))),
    );
    series[hourIndex] = (series[hourIndex] ?? 0) + 1;
  }

  return series;
}

function formatRelativeTime(timestamp: string): string {
  const ageMs = Date.now() - Date.parse(timestamp);
  const ageSeconds = Math.max(0, Math.floor(ageMs / 1000));
  if (ageSeconds < 60) return `${ageSeconds}s ago`;
  const ageMinutes = Math.floor(ageSeconds / 60);
  if (ageMinutes < 60) return `${ageMinutes}m ago`;
  const ageHours = Math.floor(ageMinutes / 60);
  if (ageHours < 24) return `${ageHours}h ago`;
  const ageDays = Math.floor(ageHours / 24);
  return `${ageDays}d ago`;
}

function activitySubject(event: DispatcherActivityEvent): string {
  const trackerId = event.data.tracker_id;
  if (typeof trackerId === 'string' && trackerId.trim()) return trackerId;
  const sagaName = event.data.name;
  if (typeof sagaName === 'string' && sagaName.trim()) return sagaName;
  const sagaId = event.data.saga_id;
  if (typeof sagaId === 'string' && sagaId.trim()) return sagaId;
  return event.event;
}

function activityState(event: DispatcherActivityEvent) {
  if (event.event === 'saga.completed') return 'merged' as const;
  if (event.event === 'saga.failed') return 'failed' as const;
  if (event.event === 'confidence.updated') return 'review' as const;

  const status = String(event.data.status ?? '').toLowerCase();
  if (status === 'merged') return 'merged' as const;
  if (status === 'failed') return 'failed' as const;
  if (status === 'running') return 'running' as const;
  return 'review' as const;
}

function activitySummary(event: DispatcherActivityEvent): string {
  if (event.event === 'confidence.updated') {
    const eventType = String(event.data.event_type ?? 'confidence.updated').replace(/_/g, ' ');
    const delta = typeof event.data.delta === 'number' ? event.data.delta : null;
    const scoreAfter =
      typeof event.data.score_after === 'number' ? Number(event.data.score_after) : null;
    const parts = [eventType];
    if (delta !== null) parts.push(`${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`);
    if (scoreAfter !== null) parts.push(`→ ${scoreAfter.toFixed(2)}`);
    return parts.join(' · ');
  }

  if (event.event === 'run.state_changed') {
    const status = String(event.data.status ?? 'updated').toLowerCase();
    const action = String(event.data.action ?? '')
      .trim()
      .replace(/_/g, ' ');
    return action ? `${status} · ${action}` : status;
  }

  if (event.event.startsWith('saga.')) {
    return event.event.replace(/^saga\./, '').replace(/\./g, ' ');
  }

  return event.event.replace(/\./g, ' ');
}

function linkedSagaForEvent(sagas: Saga[], event: DispatcherActivityEvent): Saga | undefined {
  const trackerId = typeof event.data.tracker_id === 'string' ? event.data.tracker_id : '';
  if (trackerId) {
    const match = sagas.find(
      (saga) => trackerId === saga.trackerId || trackerId.startsWith(`${saga.trackerId}.`),
    );
    if (match) return match;
  }

  const sagaTrackerId = typeof event.data.saga_id === 'string' ? event.data.saga_id : '';
  if (sagaTrackerId) {
    return sagas.find((saga) => saga.trackerId === sagaTrackerId || saga.id === sagaTrackerId);
  }

  return undefined;
}

function sagaState(saga: Saga, latestRun?: Run): FeedRow['state'] {
  if (latestRun) {
    if (latestRun.status === 'merged') return 'merged';
    if (latestRun.status === 'failed') return 'failed';
    if (latestRun.status === 'running') return 'running';
    return 'review';
  }

  if (saga.status === 'complete') return 'merged';
  if (saga.status === 'failed') return 'failed';
  return 'running';
}

function fallbackFeedRows(sagas: Saga[], phasesBySagaId: Map<string, Phase[]>): FeedRow[] {
  return sagas
    .map((saga) => {
      const phases = phasesBySagaId.get(saga.id) ?? [];
      const latestRun = phases
        .flatMap((phase) => phase.runs)
        .slice()
        .sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt))[0];
      const timestamp = latestRun?.updatedAt ?? saga.createdAt;
      const summary = latestRun
        ? `${latestRun.status} · ${latestRun.name}`
        : saga.phaseSummary.total > 0
          ? `${saga.phaseSummary.completed}/${saga.phaseSummary.total} stages committed`
          : 'created · awaiting decomposition';
      return {
        id: `fallback-${saga.id}`,
        timestamp,
        state: sagaState(saga, latestRun),
        summary,
        subject: saga.trackerId,
        sagaId: saga.id,
      } satisfies FeedRow;
    })
    .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp))
    .slice(0, 6);
}

export function DashboardPage() {
  return (
    <ToastProvider>
      <DashboardContent />
    </ToastProvider>
  );
}

function DashboardContent() {
  const navigate = useNavigate();
  const ting = useService<ITingService>('ting');
  const dispatcher = useService<IDispatcherService>('ting.dispatcher');
  const { data: sagas, isLoading, isError, error } = useSagas();
  const { data: dispatcherState } = useDispatcherState();
  const { data: activityEvents = [] } = useQuery({
    queryKey: ['ting', 'dispatcher', 'activity', 20],
    queryFn: () => dispatcher.getActivityLog(20),
  });
  const { toast } = useToast();

  const phaseQueries = useQueries({
    queries: (sagas ?? []).map((s) => ({
      queryKey: ['ting', 'phases', s.id],
      queryFn: () => ting.getPhases(s.id),
    })),
  });

  const allRuns = useMemo(
    () => phaseQueries.flatMap((q) => q.data ?? []).flatMap((phase) => phase.runs),
    [phaseQueries],
  );
  const phasesBySagaId = useMemo(
    () =>
      new Map(
        phaseQueries
          .flatMap((query) => query.data ?? [])
          .reduce<Array<[string, Phase[]]>>((pairs, phase) => {
            const existing = pairs.find(([sagaId]) => sagaId === phase.sagaId);
            if (existing) {
              existing[1].push(phase);
            } else {
              pairs.push([phase.sagaId, [phase]]);
            }
            return pairs;
          }, []),
      ),
    [phaseQueries],
  );
  const runningRuns = allRuns.filter((r) => r.status === 'running').length;
  const reviewRuns = allRuns.filter((r) => r.status === 'review').length;
  const activeRuns = allRuns.filter((r) => ACTIVE_RUN_STATUSES.has(r.status));
  const activeSagas = (sagas ?? []).filter((s) => s.status === 'active');

  const merged24h = useMemo(
    () => bucketRunsByHour(allRuns, (run) => run.status === 'merged'),
    [allRuns],
  );
  const completed24h = useMemo(
    () => bucketRunsByHour(allRuns, (run) => TERMINAL_RUN_STATUSES.has(run.status)),
    [allRuns],
  );
  const active24h = useMemo(
    () => bucketRunsByHour(allRuns, (run) => ACTIVE_RUN_STATUSES.has(run.status)),
    [allRuns],
  );

  const merged24hCount = merged24h.reduce((sum, value) => sum + value, 0);
  const completed24hCount = completed24h.reduce((sum, value) => sum + value, 0);
  const active24hUpdates = active24h.reduce((sum, value) => sum + value, 0);
  const averageConfidence = activeSagas.length
    ? Math.round(activeSagas.reduce((sum, saga) => sum + saga.confidence, 0) / activeSagas.length)
    : 0;
  const lowConfidenceCount = activeSagas.filter((saga) => saga.confidence < 50).length;
  const escalatedRuns = allRuns.filter((run) => run.status === 'escalated').length;
  const freshestRunUpdate = allRuns
    .map((run) => Date.parse(run.updatedAt))
    .filter(Number.isFinite)
    .sort((left, right) => right - left)[0];

  const feed = useMemo<FeedRow[]>(() => {
    if (activityEvents.length > 0) {
      return [...activityEvents]
        .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp))
        .slice(0, 6)
        .map((event) => ({
          id: event.id,
          timestamp: event.timestamp,
          state: activityState(event),
          summary: activitySummary(event),
          subject: activitySubject(event),
          sagaId: linkedSagaForEvent(sagas ?? [], event)?.id,
        }));
    }
    return fallbackFeedRows(sagas ?? [], phasesBySagaId);
  }, [activityEvents, phasesBySagaId, sagas]);

  if (isLoading) return <LoadingState label="Loading dashboard…" />;
  if (isError)
    return <ErrorState message={error instanceof Error ? error.message : 'Failed to load sagas'} />;

  const openSaga = (saga: Saga) =>
    void navigate({ to: '/ting/sagas/$sagaId', params: { sagaId: saga.id } });

  const handleViewAll = () => {
    void navigate({ to: '/ting/sagas' as never });
    toast({ title: 'Navigating to Sagas' });
  };

  return (
    <div className="ting-dash">
      {dispatcherState && (
        <div
          className="ting-dash__topbar-stats ting-dash__full"
          data-testid="ting-dispatcher-stats"
        >
          <span className="ting-dash__stat">
            dispatcher <strong>{dispatcherState.running ? 'on' : 'off'}</strong>
          </span>
          <span className="ting-dash__stat-sep" aria-hidden="true" />
          <span className="ting-dash__stat">
            threshold <strong>{dispatcherState.threshold.toFixed(2)}</strong>
          </span>
          <span className="ting-dash__stat-sep" aria-hidden="true" />
          <span className="ting-dash__stat">
            concurrent{' '}
            <strong>
              {runningRuns}/{dispatcherState.maxConcurrentRuns}
            </strong>
          </span>
        </div>
      )}

      <div className="ting-kpi ting-kpi--accent">
        <div className="ting-kpi__label">Active sagas</div>
        <div className="ting-kpi__val">
          {activeSagas.length}
          <span className="ting-kpi__unit">in flight</span>
        </div>
        <div className="ting-kpi__sub">
          <StateDot state="running" pulse />
          current runs: <span>{activeRuns.length}</span>
        </div>
      </div>

      <div className="ting-kpi">
        <div className="ting-kpi__label">Active runs</div>
        <div className="ting-kpi__val">
          {runningRuns}
          <span className="ting-kpi__unit">running</span>
        </div>
        <div className="ting-kpi__sub">
          <span>review queue: {reviewRuns}</span>
          <span>24h updates: {active24hUpdates}</span>
        </div>
      </div>

      <div
        className="ting-kpi"
        style={{ cursor: 'pointer' }}
        onClick={() => void navigate({ to: '/ting/sagas' as never })}
      >
        <div className="ting-kpi__label">Awaiting review</div>
        <div className="ting-kpi__val">{reviewRuns}</div>
        <div className="ting-kpi__sub">
          <span style={{ color: 'var(--brand-300)' }}>
            {escalatedRuns} escalated · newest{' '}
            {freshestRunUpdate
              ? formatRelativeTime(new Date(freshestRunUpdate).toISOString())
              : '—'}
          </span>
        </div>
      </div>

      <div className="ting-kpi">
        <div className="ting-kpi__label">Merged · 24h</div>
        <div className="ting-kpi__val">
          {merged24hCount}
          <span className="ting-kpi__unit">runs</span>
        </div>
        <Sparkline values={merged24h} id="merged" width={220} className="ting-kpi__spark" />
      </div>

      <div className="ting-dash__row-title">
        <h2>Saga stream</h2>
        <button className="ting-btn" type="button" onClick={handleViewAll}>
          View all
        </button>
      </div>

      {activeSagas.slice(0, 4).map((saga, index) => {
        const phases = phaseQueries[index]?.data ?? [];
        const cells = sagaPipe(phases);
        return (
          <div
            key={saga.id}
            className="ting-saga-card ting-dash__wide"
            role="button"
            tabIndex={0}
            onClick={() => openSaga(saga)}
            onKeyDown={(event) => event.key === 'Enter' && openSaga(saga)}
          >
            <div>
              <div className="ting-saga-card__name">{saga.name}</div>
              <div className="ting-saga-card__meta">
                <span>{saga.trackerId}</span>
                <StatusBadge status={deriveSagaBadge(phases)} />
                <ConfidenceBadge value={saga.confidence / 100} />
                <span>{saga.repos[0]}</span>
              </div>
            </div>
            <div
              style={{
                textAlign: 'right',
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                color: 'var(--color-text-muted)',
              }}
            >
              {saga.phaseSummary.completed} / {saga.phaseSummary.total}
            </div>
            {cells.length > 0 && (
              <div className="ting-saga-card__pipe">
                <Pipe cells={cells} />
              </div>
            )}
          </div>
        );
      })}

      <div className="ting-dash__row-title">
        <h2>Live flock</h2>
        <span className="ting-eyebrow">sleipnir events · last 5m</span>
      </div>

      <div className="ting-flock-viz ting-dash__wide">
        <div className="ting-flock-viz__title">
          <StateDot state="running" pulse />
          Run mesh
        </div>
        <div className="ting-flock-viz__cnt">{runningRuns} runs active</div>
        <RunMeshCanvas
          sagas={sagas ?? []}
          phases={phaseQueries.map((q) => q.data ?? [])}
          onClickSaga={(sagaId) => void navigate({ to: '/ting/sagas/$sagaId', params: { sagaId } })}
        />
      </div>

      <div className="ting-dash__wide">
        <div className="ting-sec-head">
          <span className="ting-sec-head__title">Event feed</span>
          <span className="ting-eyebrow" style={{ fontFamily: 'var(--font-mono)' }}>
            dispatcher log · run updates
          </span>
        </div>
        {feed.length === 0 ? (
          <div className="ting-run-feed ting-run-feed--empty">
            No recent Ting activity yet. Run state changes and saga events will show up here once
            the dispatcher or review flow emits them.
          </div>
        ) : (
          <div className="ting-run-feed">
            {feed.map((entry) => (
              <div key={entry.id} className="ting-feed-row">
                <StateDot state={entry.state} />
                <span className="ting-feed-row__time">{formatRelativeTime(entry.timestamp)}</span>
                <span>{entry.summary}</span>
                <span className="ting-feed-row__subject">{entry.subject}</span>
                <button
                  className="ting-feed-row__link"
                  type="button"
                  disabled={!entry.sagaId}
                  title={entry.sagaId ? `Open ${entry.subject}` : 'No linked saga'}
                  onClick={() =>
                    entry.sagaId &&
                    void navigate({
                      to: '/ting/sagas/$sagaId',
                      params: { sagaId: entry.sagaId },
                    })
                  }
                >
                  ↗
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="ting-dash__row-title">
        <h2>Throughput</h2>
      </div>

      <div className="ting-kpi ting-dash__wide">
        <div className="ting-kpi__label">Runs completed / hour</div>
        <div
          className="ting-kpi__val"
          style={{ fontSize: 18, display: 'flex', alignItems: 'baseline', gap: 8 }}
        >
          {completed24hCount}
          <span className="ting-kpi__unit">· 24h</span>
        </div>
        <Sparkline
          values={completed24h}
          id="throughput-full"
          width={520}
          height={60}
          className="ting-kpi__spark ting-kpi__spark--wide"
        />
      </div>

      <div className="ting-kpi ting-dash__wide">
        <div className="ting-kpi__label">Confidence overview</div>
        <div
          className="ting-kpi__val"
          style={{ fontSize: 18, display: 'flex', alignItems: 'baseline', gap: 8 }}
        >
          {averageConfidence}%<span className="ting-kpi__unit">average active saga confidence</span>
        </div>
        <div className="ting-kpi__stats-grid">
          <div className="ting-kpi__stat-chip">
            <span className="ting-kpi__stat-chip-label">active sagas</span>
            <strong>{activeSagas.length}</strong>
          </div>
          <div className="ting-kpi__stat-chip">
            <span className="ting-kpi__stat-chip-label">low confidence</span>
            <strong>{lowConfidenceCount}</strong>
          </div>
          <div className="ting-kpi__stat-chip">
            <span className="ting-kpi__stat-chip-label">review queue</span>
            <strong>{reviewRuns}</strong>
          </div>
          <div className="ting-kpi__stat-chip">
            <span className="ting-kpi__stat-chip-label">escalated</span>
            <strong>{escalatedRuns}</strong>
          </div>
        </div>
      </div>
    </div>
  );
}
