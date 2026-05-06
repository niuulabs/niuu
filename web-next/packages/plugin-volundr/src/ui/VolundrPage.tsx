import { useNavigate } from '@tanstack/react-router';
import { Rune, KpiStrip, KpiCard, Table, LoadingState, ErrorState } from '@niuulabs/ui';
import { useVolundrStats } from './useVolundrSessions';
import { useVolundrClusters } from './hooks/useVolundrClusters';
import { useSessionList } from './hooks/useSessionStore';
import { buildSessionColumns } from './utils/sessionColumns';
import type { Session } from '../domain/session';
import type { Cluster } from '../domain/cluster';

// ---------------------------------------------------------------------------
// Cluster health grid
// ---------------------------------------------------------------------------

function cpuPct(cluster: Cluster): number {
  return cluster.capacity.cpu > 0 ? cluster.used.cpu / cluster.capacity.cpu : 0;
}

function memPct(cluster: Cluster): number {
  return cluster.capacity.memMi > 0 ? cluster.used.memMi / cluster.capacity.memMi : 0;
}

function UsageBar({ value, label }: { value: number; label: string }) {
  const pct = Math.min(1, value) * 100;
  const colorClass =
    value > 0.85 ? 'niuu-bg-critical' : value > 0.6 ? 'niuu-bg-state-warn' : 'niuu-bg-brand';
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-1">
      <div className="niuu-flex niuu-justify-between niuu-text-xs niuu-text-text-muted">
        <span>{label}</span>
        <span>{pct.toFixed(0)}%</span>
      </div>
      <div
        className="niuu-h-1.5 niuu-rounded-full niuu-bg-bg-elevated"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className={`niuu-h-full niuu-rounded-full ${colorClass}`}
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}

function ClusterCard({ cluster }: { cluster: Cluster }) {
  const allReady = cluster.nodes.every((n) => n.status === 'ready');
  return (
    <div
      className="niuu-flex niuu-flex-col niuu-gap-3 niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-4"
      data-testid="cluster-card"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between">
        <span className="niuu-font-medium niuu-text-sm niuu-text-text-primary">{cluster.name}</span>
        <span
          className={`niuu-rounded-full niuu-px-2 niuu-py-0.5 niuu-text-xs ${allReady ? 'niuu-bg-state-ok-bg niuu-text-state-ok' : 'niuu-bg-state-warn-bg niuu-text-state-warn'}`}
        >
          {cluster.nodes.length} nodes
        </span>
      </div>
      <UsageBar value={cpuPct(cluster)} label="CPU" />
      <UsageBar value={memPct(cluster)} label="Mem" />
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-text-xs niuu-text-text-muted">
        <span>{cluster.runningSessions} running</span>
        {cluster.queuedProvisions > 0 && (
          <span className="niuu-text-state-warn">{cluster.queuedProvisions} queued</span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview page
// ---------------------------------------------------------------------------

/** Völundr Overview — KPI strip, active sessions, cluster health, terminations. */
export function VolundrPage() {
  const navigate = useNavigate();

  const stats = useVolundrStats();
  const clusters = useVolundrClusters();
  const domainSessions = useSessionList();

  function handleView(sessionId: string) {
    void navigate({ to: `/volundr/session/$sessionId`, params: { sessionId } });
  }

  const activeCols = buildSessionColumns({
    onView: handleView,
    buttonLabel: 'Open →',
    testIdPrefix: 'open-session',
    columns: ['id', 'persona', 'state', 'cluster', 'actions'],
  });

  // Derive KPI values from domain sessions.
  const runningCount = domainSessions.data?.filter((s) => s.state === 'running').length ?? 0;
  const idleCount = domainSessions.data?.filter((s) => s.state === 'idle').length ?? 0;
  const provisioningCount =
    domainSessions.data?.filter((s) => s.state === 'provisioning' || s.state === 'requested')
      .length ?? 0;

  // Cluster-level aggregates.
  const totalCpuCap = clusters.data?.reduce((s, c) => s + c.capacity.cpu, 0) ?? 0;
  const totalCpuUsed = clusters.data?.reduce((s, c) => s + c.used.cpu, 0) ?? 0;
  const totalMemCapMi = clusters.data?.reduce((s, c) => s + c.capacity.memMi, 0) ?? 0;
  const totalMemUsedMi = clusters.data?.reduce((s, c) => s + c.used.memMi, 0) ?? 0;
  const totalGpuCap = clusters.data?.reduce((s, c) => s + c.capacity.gpu, 0) ?? 0;
  const totalGpuUsed = clusters.data?.reduce((s, c) => s + c.used.gpu, 0) ?? 0;
  const totalQueued = clusters.data?.reduce((s, c) => s + c.queuedProvisions, 0) ?? 0;

  // Active + recently terminated (from domain sessions).
  const activeSessions =
    domainSessions.data?.filter((s) => s.state === 'running' || s.state === 'idle') ?? [];

  const recentTerminations =
    domainSessions.data?.filter((s) => s.state === 'terminated' || s.state === 'failed') ?? [];

  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-8 niuu-p-6" data-testid="volundr-overview">
      {/* Header */}
      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <Rune glyph="ᚲ" size={32} />
        <div>
          <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
            Völundr · session forge
          </h2>
          <p className="niuu-text-sm niuu-text-text-muted">
            Provisions and manages remote dev pods.
          </p>
        </div>
      </div>

      {/* KPI strip */}
      <section aria-label="Session KPIs">
        {(domainSessions.isLoading || clusters.isLoading) && (
          <LoadingState label="Loading metrics…" />
        )}
        {!domainSessions.isLoading && !clusters.isLoading && (
          <KpiStrip>
            <KpiCard label="active" value={runningCount} data-testid="kpi-active" />
            <KpiCard label="idle" value={idleCount} data-testid="kpi-idle" />
            <KpiCard
              label="total CPU"
              value={`${totalCpuUsed} / ${totalCpuCap}`}
              data-testid="kpi-cpu"
            />
            <KpiCard
              label="total mem"
              value={
                totalMemCapMi > 0
                  ? `${(totalMemUsedMi / 1024).toFixed(1)} / ${(totalMemCapMi / 1024).toFixed(0)} GiB`
                  : '—'
              }
              data-testid="kpi-mem"
            />
            <KpiCard label="GPU" value={`${totalGpuUsed} / ${totalGpuCap}`} data-testid="kpi-gpu" />
            <KpiCard
              label="provisioning queue"
              value={totalQueued + provisioningCount}
              data-testid="kpi-queue"
            />
          </KpiStrip>
        )}
      </section>

      {/* Active sessions */}
      <section className="niuu-flex niuu-flex-col niuu-gap-3" aria-label="Active sessions">
        <h3 className="niuu-text-sm niuu-font-medium niuu-text-text-secondary">Active sessions</h3>
        {domainSessions.isLoading && <LoadingState label="Loading sessions…" />}
        {domainSessions.isError && (
          <ErrorState
            title="Failed to load sessions"
            message={
              domainSessions.error instanceof Error ? domainSessions.error.message : 'Unknown error'
            }
          />
        )}
        {domainSessions.data && activeSessions.length === 0 && (
          <p className="niuu-text-sm niuu-text-text-muted" data-testid="no-active-sessions">
            No active sessions. Start one to get going.
          </p>
        )}
        {activeSessions.length > 0 && (
          <Table<Session> columns={activeCols} rows={activeSessions} aria-label="Active sessions" />
        )}
      </section>

      {/* Cluster health grid */}
      <section className="niuu-flex niuu-flex-col niuu-gap-3" aria-label="Cluster health">
        <h3 className="niuu-text-sm niuu-font-medium niuu-text-text-secondary">Cluster health</h3>
        {clusters.isLoading && <LoadingState label="Loading clusters…" />}
        {clusters.isError && (
          <ErrorState title="Failed to load clusters" message="Could not reach the cluster API." />
        )}
        {clusters.data && clusters.data.length === 0 && (
          <p className="niuu-text-sm niuu-text-text-muted">No clusters registered.</p>
        )}
        {clusters.data && clusters.data.length > 0 && (
          <div className="niuu-grid niuu-grid-cols-[repeat(auto-fill,minmax(240px,1fr))] niuu-gap-4">
            {clusters.data.map((c) => (
              <ClusterCard key={c.id} cluster={c} />
            ))}
          </div>
        )}
      </section>

      {/* Recent terminations */}
      {recentTerminations.length > 0 && (
        <section className="niuu-flex niuu-flex-col niuu-gap-3" aria-label="Recent terminations">
          <h3 className="niuu-text-sm niuu-font-medium niuu-text-text-secondary">
            Recent terminations
          </h3>
          <Table<Session>
            columns={activeCols}
            rows={recentTerminations}
            aria-label="Recent terminations"
          />
        </section>
      )}

      {/* Stats footer */}
      {stats.data && (
        <p className="niuu-text-xs niuu-text-text-muted">
          Tokens today:{' '}
          <span className="niuu-font-mono niuu-text-text-secondary">
            {stats.data.tokensToday.toLocaleString()}
          </span>{' '}
          · Cost:{' '}
          <span className="niuu-font-mono niuu-text-text-secondary">
            ${stats.data.costToday.toFixed(2)}
          </span>
        </p>
      )}
    </div>
  );
}
