import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
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
import type { ITingService, Phase } from '../ports';
import type { Saga, RunStatus } from '../domain/saga';
import { useSagas } from './useSagas';
import { useDispatcherState } from './useDispatcherState';
import { RunMeshCanvas } from './RunMeshCanvas';
import './DashboardPage.css';

type PipeCell = { status: 'ok' | 'run' | 'warn' | 'crit' | 'gate' | 'pend'; label: string };

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

/** Derive the dominant badge status for a saga from its runs. */
function deriveSagaBadge(phases: Phase[]): BadgeStatus {
  const runs = phases.flatMap((p) => p.runs);
  if (runs.some((r) => r.status === 'failed')) return 'failed';
  if (runs.some((r) => r.status === 'running')) return 'running';
  if (runs.some((r) => r.status === 'review' || r.status === 'escalated')) return 'review';
  return 'running';
}

/** Deterministic mock throughput data (24 hours). */
function mockThroughput(): number[] {
  return Array.from({ length: 24 }, (_, i) => Math.round(4 + 3 * Math.sin(i / 3) + 2));
}

/** Deterministic mock confidence trend data (24 hours). */
function mockConfidence(): number[] {
  return Array.from({ length: 24 }, (_, i) => 0.6 + 0.3 * Math.sin(i / 5) + 0.02);
}

/** Mock event feed entries — matches web2 spec. */
const FEED = [
  {
    t: '12s ago',
    subject: 'NIU-214.2',
    body: 'coding-agent → code.changed (run-42aa)',
    kind: 'run' as const,
  },
  {
    t: '18s ago',
    subject: 'NIU-199.2',
    body: 'qa-agent → qa.completed verdict=pass',
    kind: 'ok' as const,
  },
  {
    t: '34s ago',
    subject: 'NIU-183.4',
    body: 'reviewer → review.completed needs_changes',
    kind: 'warn' as const,
  },
  {
    t: '1m ago',
    subject: 'NIU-088.1',
    body: 'saga published: saga.completed',
    kind: 'ok' as const,
  },
  {
    t: '2m ago',
    subject: 'NIU-148.2',
    body: 'coding-agent → run.attempted verdict=fail',
    kind: 'crit' as const,
  },
  {
    t: '2m ago',
    subject: 'NIU-214.3',
    body: 'review-arbiter → review.arbitrated pending',
    kind: 'warn' as const,
  },
];

function feedKindToState(kind: string) {
  if (kind === 'ok') return 'merged' as const;
  if (kind === 'run') return 'running' as const;
  if (kind === 'crit') return 'failed' as const;
  return 'review' as const;
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
  const { data: sagas, isLoading, isError, error } = useSagas();
  const { data: dispatcherState } = useDispatcherState();
  const { toast } = useToast();

  const phaseQueries = useQueries({
    queries: (sagas ?? []).map((s) => ({
      queryKey: ['ting', 'phases', s.id],
      queryFn: () => ting.getPhases(s.id),
    })),
  });

  const allRuns = phaseQueries.flatMap((q) => q.data ?? []).flatMap((p) => p.runs);
  const runningRuns = allRuns.filter((r) => r.status === 'running').length;
  const reviewRuns = allRuns.filter((r) => r.status === 'review').length;

  const activeSagas = (sagas ?? []).filter((s) => s.status === 'active');

  const throughput = useMemo(mockThroughput, []);
  const confidence = useMemo(mockConfidence, []);
  const throughputTotal = throughput.reduce((a, b) => a + b, 0);
  const latestConfidence = Math.round((confidence.at(-1) ?? 0) * 100);

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
      {/* ── Dispatcher stats bar ──────────────────── */}
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
            threshold <strong>{(dispatcherState.threshold / 100).toFixed(2)}</strong>
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

      {/* ── KPI cards (4 columns) ─────────────────── */}
      <div className="ting-kpi ting-kpi--accent">
        <div className="ting-kpi__label">Active sagas</div>
        <div className="ting-kpi__val">
          {activeSagas.length}
          <span className="ting-kpi__unit">in flight</span>
        </div>
        <div className="ting-kpi__sub">
          <StateDot state="running" pulse />
          dispatched this hour: <span>{runningRuns + reviewRuns}</span>
        </div>
      </div>

      <div className="ting-kpi">
        <div className="ting-kpi__label">Active runs</div>
        <div className="ting-kpi__val">
          {runningRuns}
          <span className="ting-kpi__unit">running</span>
        </div>
        <Sparkline
          values={throughput.map((v) => v / Math.max(...throughput))}
          id="throughput"
          width={220}
          className="ting-kpi__spark"
        />
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
            {allRuns.filter((r) => r.status === 'escalated').length} escalated · oldest 18m
          </span>
        </div>
      </div>

      <div className="ting-kpi">
        <div className="ting-kpi__label">Merged · 24h</div>
        <div className="ting-kpi__val">{allRuns.filter((r) => r.status === 'merged').length}</div>
        <Sparkline
          values={[2, 3, 3, 4, 5, 6, 7, 9, 10, 11, 11, 12].map((v) => v / 12)}
          id="merged"
          width={220}
          className="ting-kpi__spark"
        />
      </div>

      {/* ── Saga stream ───────────────────────────── */}
      <div className="ting-dash__row-title">
        <h2>Saga stream</h2>
        <button className="ting-btn" type="button" onClick={handleViewAll}>
          View all
        </button>
      </div>

      {activeSagas.slice(0, 4).map((saga, i) => {
        const phases = phaseQueries[i]?.data ?? [];
        const cells = sagaPipe(phases);
        return (
          <div
            key={saga.id}
            className="ting-saga-card ting-dash__wide"
            role="button"
            tabIndex={0}
            onClick={() => openSaga(saga)}
            onKeyDown={(e) => e.key === 'Enter' && openSaga(saga)}
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

      {/* ── Live flock ────────────────────────────── */}
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

      {/* ── Event feed ────────────────────────────── */}
      <div className="ting-dash__wide">
        <div className="ting-sec-head">
          <span className="ting-sec-head__title">Event feed</span>
          <span className="ting-eyebrow" style={{ fontFamily: 'var(--font-mono)' }}>
            sleipnir:*
          </span>
        </div>
        <div className="ting-run-feed">
          {FEED.map((f, i) => {
            const parentSaga = (sagas ?? []).find((s) => f.subject.startsWith(s.trackerId));
            return (
              <div key={i} className="ting-feed-row">
                <StateDot state={feedKindToState(f.kind)} />
                <span className="ting-feed-row__time">{f.t}</span>
                <span>{f.body}</span>
                <span className="ting-feed-row__subject">{f.subject}</span>
                <button
                  className="ting-feed-row__link"
                  type="button"
                  disabled={!parentSaga}
                  title={parentSaga ? `Open ${parentSaga.trackerId}` : 'No linked saga'}
                  onClick={() =>
                    parentSaga &&
                    void navigate({
                      to: '/ting/sagas/$sagaId',
                      params: { sagaId: parentSaga.id },
                    })
                  }
                >
                  ↗
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Throughput ────────────────────────────── */}
      <div className="ting-dash__row-title">
        <h2>Throughput</h2>
      </div>

      <div className="ting-kpi ting-dash__wide">
        <div className="ting-kpi__label">Runs completed / hour</div>
        <div
          className="ting-kpi__val"
          style={{ fontSize: 18, display: 'flex', alignItems: 'baseline', gap: 8 }}
        >
          {throughputTotal}
          <span className="ting-kpi__unit">· 24h</span>
        </div>
        <Sparkline
          values={throughput.map((v) => v / Math.max(...throughput))}
          id="throughput-full"
          width={520}
          height={60}
          className="ting-kpi__spark ting-kpi__spark--wide"
        />
      </div>

      <div className="ting-kpi ting-dash__wide">
        <div className="ting-kpi__label">Saga confidence</div>
        <div className="ting-kpi__val" style={{ fontSize: 18 }}>
          {latestConfidence}%<span className="ting-kpi__unit">· now</span>
        </div>
        <Sparkline
          values={confidence}
          id="confidence-full"
          width={520}
          height={60}
          className="ting-kpi__spark ting-kpi__spark--wide"
        />
      </div>
    </div>
  );
}
