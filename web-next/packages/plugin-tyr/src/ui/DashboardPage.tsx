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
import type { ITyrService, Phase } from '../ports';
import type { Saga, RaidStatus } from '../domain/saga';
import { useSagas } from './useSagas';
import { useDispatcherState } from './useDispatcherState';
import { RaidMeshCanvas } from './RaidMeshCanvas';
import './DashboardPage.css';

type PipeCell = { status: 'ok' | 'run' | 'warn' | 'crit' | 'gate' | 'pend'; label: string };

function raidStatusToCell(s: RaidStatus): PipeCell['status'] {
  const map: Record<RaidStatus, PipeCell['status']> = {
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
    p.raids.map((r) => ({ status: raidStatusToCell(r.status), label: r.name })),
  );
}

/** Derive the dominant badge status for a saga from its raids. */
function deriveSagaBadge(phases: Phase[]): BadgeStatus {
  const raids = phases.flatMap((p) => p.raids);
  if (raids.some((r) => r.status === 'failed')) return 'failed';
  if (raids.some((r) => r.status === 'running')) return 'running';
  if (raids.some((r) => r.status === 'review' || r.status === 'escalated')) return 'review';
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
    body: 'coding-agent → code.changed (raid-42aa)',
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
    body: 'coding-agent → raid.attempted verdict=fail',
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
  const tyr = useService<ITyrService>('tyr');
  const { data: sagas, isLoading, isError, error } = useSagas();
  const { data: dispatcherState } = useDispatcherState();
  const { toast } = useToast();

  const phaseQueries = useQueries({
    queries: (sagas ?? []).map((s) => ({
      queryKey: ['tyr', 'phases', s.id],
      queryFn: () => tyr.getPhases(s.id),
    })),
  });

  const allRaids = phaseQueries.flatMap((q) => q.data ?? []).flatMap((p) => p.raids);
  const runningRaids = allRaids.filter((r) => r.status === 'running').length;
  const reviewRaids = allRaids.filter((r) => r.status === 'review').length;

  const activeSagas = (sagas ?? []).filter((s) => s.status === 'active');

  const throughput = useMemo(mockThroughput, []);
  const confidence = useMemo(mockConfidence, []);
  const throughputTotal = throughput.reduce((a, b) => a + b, 0);
  const latestConfidence = Math.round((confidence.at(-1) ?? 0) * 100);

  if (isLoading) return <LoadingState label="Loading dashboard…" />;
  if (isError)
    return <ErrorState message={error instanceof Error ? error.message : 'Failed to load sagas'} />;

  const openSaga = (saga: Saga) =>
    void navigate({ to: '/tyr/sagas/$sagaId', params: { sagaId: saga.id } });

  const handleViewAll = () => {
    void navigate({ to: '/tyr/sagas' as never });
    toast({ title: 'Navigating to Sagas' });
  };

  return (
    <div className="tyr-dash">
      {/* ── Dispatcher stats bar ──────────────────── */}
      {dispatcherState && (
        <div className="tyr-dash__topbar-stats tyr-dash__full" data-testid="tyr-dispatcher-stats">
          <span className="tyr-dash__stat">
            dispatcher <strong>{dispatcherState.running ? 'on' : 'off'}</strong>
          </span>
          <span className="tyr-dash__stat-sep" aria-hidden="true" />
          <span className="tyr-dash__stat">
            threshold <strong>{(dispatcherState.threshold / 100).toFixed(2)}</strong>
          </span>
          <span className="tyr-dash__stat-sep" aria-hidden="true" />
          <span className="tyr-dash__stat">
            concurrent{' '}
            <strong>
              {runningRaids}/{dispatcherState.maxConcurrentRaids}
            </strong>
          </span>
        </div>
      )}

      {/* ── KPI cards (4 columns) ─────────────────── */}
      <div className="tyr-kpi tyr-kpi--accent">
        <div className="tyr-kpi__label">Active sagas</div>
        <div className="tyr-kpi__val">
          {activeSagas.length}
          <span className="tyr-kpi__unit">in flight</span>
        </div>
        <div className="tyr-kpi__sub">
          <StateDot state="running" pulse />
          dispatched this hour: <span>{runningRaids + reviewRaids}</span>
        </div>
      </div>

      <div className="tyr-kpi">
        <div className="tyr-kpi__label">Active raids</div>
        <div className="tyr-kpi__val">
          {runningRaids}
          <span className="tyr-kpi__unit">running</span>
        </div>
        <Sparkline
          values={throughput.map((v) => v / Math.max(...throughput))}
          id="throughput"
          width={220}
          className="tyr-kpi__spark"
        />
      </div>

      <div
        className="tyr-kpi"
        style={{ cursor: 'pointer' }}
        onClick={() => void navigate({ to: '/tyr/sagas' as never })}
      >
        <div className="tyr-kpi__label">Awaiting review</div>
        <div className="tyr-kpi__val">{reviewRaids}</div>
        <div className="tyr-kpi__sub">
          <span style={{ color: 'var(--brand-300)' }}>
            {allRaids.filter((r) => r.status === 'escalated').length} escalated · oldest 18m
          </span>
        </div>
      </div>

      <div className="tyr-kpi">
        <div className="tyr-kpi__label">Merged · 24h</div>
        <div className="tyr-kpi__val">{allRaids.filter((r) => r.status === 'merged').length}</div>
        <Sparkline
          values={[2, 3, 3, 4, 5, 6, 7, 9, 10, 11, 11, 12].map((v) => v / 12)}
          id="merged"
          width={220}
          className="tyr-kpi__spark"
        />
      </div>

      {/* ── Saga stream ───────────────────────────── */}
      <div className="tyr-dash__row-title">
        <h2>Saga stream</h2>
        <button className="tyr-btn" type="button" onClick={handleViewAll}>
          View all
        </button>
      </div>

      {activeSagas.slice(0, 4).map((saga, i) => {
        const phases = phaseQueries[i]?.data ?? [];
        const cells = sagaPipe(phases);
        return (
          <div
            key={saga.id}
            className="tyr-saga-card tyr-dash__wide"
            role="button"
            tabIndex={0}
            onClick={() => openSaga(saga)}
            onKeyDown={(e) => e.key === 'Enter' && openSaga(saga)}
          >
            <div>
              <div className="tyr-saga-card__name">{saga.name}</div>
              <div className="tyr-saga-card__meta">
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
              <div className="tyr-saga-card__pipe">
                <Pipe cells={cells} />
              </div>
            )}
          </div>
        );
      })}

      {/* ── Live flock ────────────────────────────── */}
      <div className="tyr-dash__row-title">
        <h2>Live flock</h2>
        <span className="tyr-eyebrow">sleipnir events · last 5m</span>
      </div>

      <div className="tyr-flock-viz tyr-dash__wide">
        <div className="tyr-flock-viz__title">
          <StateDot state="running" pulse />
          Raid mesh
        </div>
        <div className="tyr-flock-viz__cnt">{runningRaids} raids active</div>
        <RaidMeshCanvas
          sagas={sagas ?? []}
          phases={phaseQueries.map((q) => q.data ?? [])}
          onClickSaga={(sagaId) => void navigate({ to: '/tyr/sagas/$sagaId', params: { sagaId } })}
        />
      </div>

      {/* ── Event feed ────────────────────────────── */}
      <div className="tyr-dash__wide">
        <div className="tyr-sec-head">
          <span className="tyr-sec-head__title">Event feed</span>
          <span className="tyr-eyebrow" style={{ fontFamily: 'var(--font-mono)' }}>
            sleipnir:*
          </span>
        </div>
        <div className="tyr-raid-feed">
          {FEED.map((f, i) => {
            const parentSaga = (sagas ?? []).find((s) => f.subject.startsWith(s.trackerId));
            return (
              <div key={i} className="tyr-feed-row">
                <StateDot state={feedKindToState(f.kind)} />
                <span className="tyr-feed-row__time">{f.t}</span>
                <span>{f.body}</span>
                <span className="tyr-feed-row__subject">{f.subject}</span>
                <button
                  className="tyr-feed-row__link"
                  type="button"
                  disabled={!parentSaga}
                  title={parentSaga ? `Open ${parentSaga.trackerId}` : 'No linked saga'}
                  onClick={() =>
                    parentSaga &&
                    void navigate({
                      to: '/tyr/sagas/$sagaId',
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
      <div className="tyr-dash__row-title">
        <h2>Throughput</h2>
      </div>

      <div className="tyr-kpi tyr-dash__wide">
        <div className="tyr-kpi__label">Raids completed / hour</div>
        <div
          className="tyr-kpi__val"
          style={{ fontSize: 18, display: 'flex', alignItems: 'baseline', gap: 8 }}
        >
          {throughputTotal}
          <span className="tyr-kpi__unit">· 24h</span>
        </div>
        <Sparkline
          values={throughput.map((v) => v / Math.max(...throughput))}
          id="throughput-full"
          width={520}
          height={60}
          className="tyr-kpi__spark tyr-kpi__spark--wide"
        />
      </div>

      <div className="tyr-kpi tyr-dash__wide">
        <div className="tyr-kpi__label">Saga confidence</div>
        <div className="tyr-kpi__val" style={{ fontSize: 18 }}>
          {latestConfidence}%<span className="tyr-kpi__unit">· now</span>
        </div>
        <Sparkline
          values={confidence}
          id="confidence-full"
          width={520}
          height={60}
          className="tyr-kpi__spark tyr-kpi__spark--wide"
        />
      </div>
    </div>
  );
}
