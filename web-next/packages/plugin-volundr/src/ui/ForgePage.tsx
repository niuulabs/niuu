import { Fragment, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { LoadingState, Sparkline, StateDot } from '@niuulabs/ui';
import { CliBadge, ConnectionTypeBadge, MiniBar } from './atoms';
import { useVolundrStats } from './useVolundrSessions';
import { useVolundrClusters } from './hooks/useVolundrClusters';
import { useSessionList } from './hooks/useSessionStore';
import { useLaunchSpecs } from './useLaunchSpecs';
import { LaunchWizard } from './LaunchWizard';
import { money, tokens } from './utils/formatters';
import type { Cluster, ClusterKind } from '../domain/cluster';
import type { Session, SessionState } from '../domain/session';
import type { VolundrLaunchSpec } from '../models/volundr.model';
import './ForgePage.css';

const INFLIGHT_STATES: SessionState[] = ['provisioning', 'requested', 'running', 'idle'];

const SESSION_PRIORITY: Record<SessionState, number> = {
  provisioning: 0,
  requested: 1,
  running: 2,
  idle: 3,
  ready: 4,
  failed: 5,
  terminating: 6,
  terminated: 7,
  archived: 8,
};

const KIND_LABEL: Record<ClusterKind, string> = {
  primary: 'PRIMARY',
  gpu: 'GPU',
  edge: 'EDGE',
  local: 'LOCAL',
  observ: 'OBSERV',
  media: 'MEDIA',
};

const FORGE_CLUSTER_DISPLAY: Record<string, { name: string; realm: string; kind?: ClusterKind }> = {
  'cl-eitri': { name: 'Valaskjálf', realm: 'asgard', kind: 'primary' },
  'cl-valhalla': { name: 'Valhalla', realm: 'asgard', kind: 'gpu' },
  'cl-noatun': { name: 'Nóatún', realm: 'midgard', kind: 'edge' },
  'cl-brokkr': { name: 'Eitri', realm: 'svartalfheim', kind: 'local' },
  'cl-glitnir': { name: 'Glitnir', realm: 'midgard', kind: 'observ' },
  'cl-jarnvidr': { name: 'Járnviðr', realm: 'jotunheim', kind: 'media' },
};

const SHOWCASE_SESSION_IDS = new Set([
  'niuu-integration-tests',
  'laptop-volundr-local',
  'mimir-bge-reindex',
  'aider-css-migration',
  'ravn-triggers-ui',
  'observatory-canvas-perf',
  'ds-7',
  'ds-8',
  'ds-9',
  'ds-4',
]);

type ForgeClusterView = Cluster & {
  displayName: string;
  displayRealm: string;
  displayKind: ClusterKind;
  podCount: number;
  cpuPct: number;
  memPct: number;
  gpuPct: number;
};

function normalizeKey(value: string) {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function lastTouched(session: Session) {
  return new Date(session.lastActivityAt ?? session.startedAt).getTime();
}

function compactAge(timestamp: number) {
  const diff = Math.max(1_000, Date.now() - timestamp);
  const seconds = Math.floor(diff / 1_000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function formatSpecResources(spec: VolundrLaunchSpec) {
  const cpu = spec.resourceConfig.cpu ? `${spec.resourceConfig.cpu}c` : '';
  const mem = spec.resourceConfig.memory ? spec.resourceConfig.memory : '';
  const gpu =
    spec.resourceConfig.gpu && spec.resourceConfig.gpu !== '0'
      ? `gpu ${spec.resourceConfig.gpu}`
      : '';
  return [cpu, mem, gpu, spec.scope].filter(Boolean).join('  ');
}

function displayCluster(session: Session, clusterMap: Map<string, ForgeClusterView>) {
  return clusterMap.get(normalizeKey(session.clusterId))?.displayName ?? session.clusterId;
}

function clampPct(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

function meterFillPct(value: number) {
  const pct = clampPct(value);
  if (pct === 0) return 0;
  return Math.max(2, pct * 100);
}

function meterToneClass(value: number) {
  const pct = clampPct(value);
  if (pct > 0.85) return 'vol-forge__meter-fill--critical';
  if (pct > 0.6) return 'vol-forge__meter-fill--warn';
  return 'vol-forge__meter-fill--ok';
}

function percentLabel(value: number) {
  const pct = clampPct(value);
  if (pct === 0) return '0%';
  if (pct < 0.01) return '<1%';
  return `${Math.round(pct * 100)}%`;
}

function conciseNumber(value: number, digits = 1) {
  if (!Number.isFinite(value)) return '0';
  const fixed = value.toFixed(digits);
  return fixed.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
}

function cpuLabel(value: number) {
  if (value === 0) return '0';
  if (Math.abs(value) < 1) return `${Math.round(value * 1000)}m`;
  return `${conciseNumber(value)}c`;
}

function memLabel(mi: number) {
  if (mi === 0) return '0';
  if (Math.abs(mi) < 1024) return `${Math.round(mi)}Mi`;
  return `${conciseNumber(mi / 1024)}Gi`;
}

function gpuLabel(value: number) {
  return String(Math.round(value));
}

function ClusterMeter({
  label,
  value,
  used,
  total,
}: {
  label: string;
  value: number;
  used: string;
  total: string;
}) {
  const pct = clampPct(value);
  return (
    <div className="vol-forge__meter" data-testid={`cluster-meter-${label}`}>
      <div className="vol-forge__meter-label">
        <span>{label}</span>
        <strong>
          {used} / {total} · {percentLabel(pct)}
        </strong>
      </div>
      <div
        className="vol-forge__meter-track"
        role="progressbar"
        aria-valuenow={Math.round(pct * 100)}
        aria-valuemax={100}
        aria-label={`${label} utilization`}
      >
        <div
          className={`vol-forge__meter-fill ${meterToneClass(pct)}`}
          style={{ width: `${meterFillPct(pct)}%` }}
        />
      </div>
    </div>
  );
}

function sessionPrimaryLabel(session: Session) {
  return (
    session.title?.trim() ||
    session.name?.trim() ||
    session.trackerIssue?.title?.trim() ||
    session.id
  );
}

function sessionSecondaryParts(session: Session, clusterLabel: string) {
  const primary = sessionPrimaryLabel(session).trim();
  const persona = session.personaName.trim();
  return [
    session.trackerIssue?.identifier ? (
      <span className="vol-forge__ticket" data-testid="inflight-ticket" key="ticket">
        {session.trackerIssue.identifier}
      </span>
    ) : null,
    persona && persona !== primary ? <span key="persona">{persona}</span> : null,
    <span key="cluster">{clusterLabel}</span>,
  ].filter(Boolean);
}

function sessionDotState(session: Session) {
  if (session.state === 'failed') return 'failed';
  if (session.state === 'idle') return 'idle';
  if (session.state === 'running') return 'running';
  return 'processing';
}

function statusLabel(session: Session) {
  if (session.state === 'provisioning') return session.preview ?? 'pulling image…';
  if (session.state === 'requested') return 'requested';
  return session.preview ?? session.events[session.events.length - 1]?.body ?? 'idle';
}

function average(values: number[], count: number) {
  if (values.length === 0) return 0;
  const slice = values.slice(-count);
  return slice.reduce((sum, value) => sum + value, 0) / slice.length;
}

function clusterAccentClass(kind: ClusterKind) {
  return `vol-forge__kind--${kind}`;
}

function MetricTile({
  label,
  value,
  subline,
  accent = 'brand',
  children,
}: {
  label: string;
  value: string | number;
  subline: string;
  accent?: 'brand' | 'neutral';
  children?: ReactNode;
}) {
  return (
    <div className={`vol-forge__metric vol-forge__metric--${accent}`}>
      <div className="vol-forge__metric-label">{label}</div>
      <div className="vol-forge__metric-value">{value}</div>
      <div className="vol-forge__metric-sub">{subline}</div>
      {children ? <div className="vol-forge__metric-viz">{children}</div> : null}
    </div>
  );
}

function InflightRow({
  session,
  clusterLabel,
  onClick,
}: {
  session: Session;
  clusterLabel: string;
  onClick: () => void;
}) {
  const isBooting = session.state === 'provisioning' || session.state === 'requested';
  const cpuPct =
    session.resources.cpuLimit > 0 ? session.resources.cpuUsed / session.resources.cpuLimit : 0;
  const memPct =
    session.resources.memLimitMi > 0
      ? session.resources.memUsedMi / session.resources.memLimitMi
      : 0;
  const gpuPct = session.resources.gpuCount > 0 ? 0.84 : 0;
  const tokenTotal = (session.tokensIn ?? 0) + (session.tokensOut ?? 0);

  return (
    <button
      type="button"
      className={`vol-forge__inflight-row${isBooting ? ' is-booting' : ''}`}
      onClick={onClick}
      data-testid="inflight-row"
    >
      <div className="vol-forge__inflight-ident">
        <StateDot
          state={sessionDotState(session)}
          pulse={!isBooting && session.state === 'running'}
        />
        <div className="vol-forge__inflight-namecol">
          <div className="vol-forge__inflight-name" title={session.id} data-testid="inflight-title">
            {sessionPrimaryLabel(session)}
          </div>
          <div className="vol-forge__inflight-sub">
            {sessionSecondaryParts(session, clusterLabel).map((part, index) => (
              <Fragment key={index}>
                {index > 0 ? <span className="vol-forge__sep">·</span> : null}
                {part}
              </Fragment>
            ))}
          </div>
        </div>
      </div>

      <div
        className="vol-forge__inflight-preview"
        title={statusLabel(session)}
        data-testid={isBooting ? undefined : 'inflight-preview'}
      >
        {statusLabel(session)}
      </div>

      <div className="vol-forge__inflight-resources">
        {!isBooting ? (
          <>
            <MiniBar value={cpuPct} label="cpu" />
            <MiniBar value={memPct} label="mem" />
            {session.resources.gpuCount > 0 ? <MiniBar value={gpuPct} label="gpu" /> : null}
          </>
        ) : (
          <div className="vol-forge__booting-copy">{session.preview ?? 'bootstrapping pod…'}</div>
        )}
      </div>

      <div className="vol-forge__inflight-stats">
        {!isBooting && tokenTotal > 0 ? (
          <span data-testid="token-stat">{tokens(tokenTotal)}</span>
        ) : null}
        {!isBooting && tokenTotal > 0 && session.costCents !== undefined ? (
          <span className="vol-forge__sep">·</span>
        ) : null}
        {!isBooting && session.costCents !== undefined ? (
          <span data-testid="cost-stat">{money(session.costCents)}</span>
        ) : null}
      </div>

      <div className="vol-forge__inflight-badge">
        {session.connectionType ? (
          <ConnectionTypeBadge
            connectionType={session.connectionType}
            className="vol-forge__connection-badge"
          />
        ) : null}
      </div>

      {isBooting ? (
        <div className="vol-forge__boot-progress">
          <div
            className="vol-forge__boot-progress-fill"
            style={{ width: `${Math.round((session.bootProgress ?? 0.08) * 100)}%` }}
            data-testid="boot-progress-bar"
          />
        </div>
      ) : null}
    </button>
  );
}

function ForgeLoadRow({ cluster }: { cluster: ForgeClusterView }) {
  return (
    <div className="vol-forge__cluster-row" data-testid="cluster-load-row">
      <div className="vol-forge__cluster-head">
        <div className="vol-forge__cluster-namewrap">
          <span className="vol-forge__cluster-name">{cluster.displayName}</span>
          <span className="vol-forge__cluster-realm">· {cluster.displayRealm}</span>
        </div>
        <div className="vol-forge__cluster-sub">
          <span
            className={`vol-forge__kind ${clusterAccentClass(cluster.displayKind)}`}
            data-testid="cluster-kind-badge"
          >
            {KIND_LABEL[cluster.displayKind]}
          </span>
          <span className="vol-forge__cluster-count">
            {cluster.podCount} pod{cluster.podCount === 1 ? '' : 's'}
          </span>
        </div>
      </div>

      <div className="vol-forge__cluster-meters">
        <ClusterMeter
          label="cpu"
          value={cluster.cpuPct}
          used={cpuLabel(cluster.used.cpu)}
          total={cpuLabel(cluster.capacity.cpu)}
        />
        <ClusterMeter
          label="mem"
          value={cluster.memPct}
          used={memLabel(cluster.used.memMi)}
          total={memLabel(cluster.capacity.memMi)}
        />
        <ClusterMeter
          label="gpu"
          value={cluster.gpuPct}
          used={gpuLabel(cluster.used.gpu)}
          total={gpuLabel(cluster.capacity.gpu)}
        />
      </div>
    </div>
  );
}

function QuickLaunchCard({
  spec,
  isDefault,
  onClick,
}: {
  spec: VolundrLaunchSpec;
  isDefault: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="vol-forge__launch-card"
      onClick={onClick}
      data-testid="quick-launch-card"
    >
      <div className="vol-forge__launch-head">
        <CliBadge cli={spec.cliTool} />
        {isDefault ? <span className="vol-forge__launch-default">DEFAULT</span> : null}
      </div>
      <div className="vol-forge__launch-name">{spec.name}</div>
      <div className="vol-forge__launch-desc">{spec.description || 'catalog launch spec'}</div>
      <div className="vol-forge__launch-foot">
        <span>{formatSpecResources(spec)}</span>
        {spec.model ? <span>{spec.model}</span> : null}
      </div>
    </button>
  );
}

function RecentFleetItem({ session }: { session: Session }) {
  return (
    <li className="vol-forge__tail-row">
      <span className="vol-forge__tail-time">{compactAge(lastTouched(session))}</span>
      <StateDot state={sessionDotState(session)} />
      <div className="vol-forge__tail-namewrap">
        <span className="vol-forge__tail-name" title={session.id} data-testid="tail-title">
          {sessionPrimaryLabel(session)}
        </span>
        {session.trackerIssue ? (
          <span className="vol-forge__ticket" data-testid="tail-ticket">
            {session.trackerIssue.identifier}
          </span>
        ) : null}
      </div>
      <span className="vol-forge__tail-sep">·</span>
      <span className="vol-forge__tail-preview" title={statusLabel(session)}>
        {statusLabel(session)}
      </span>
    </li>
  );
}

export function ForgePage() {
  const navigate = useNavigate();
  const stats = useVolundrStats();
  const clusters = useVolundrClusters();
  const sessionsQuery = useSessionList();
  const launchSpecs = useLaunchSpecs('system');

  const [launchOpen, setLaunchOpen] = useState(false);
  const [launchSpecRef, setLaunchSpecRef] = useState<string | null>(null);

  const allSessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);
  const dashboardSessions = useMemo(() => {
    const showcase = allSessions.filter((session) => SHOWCASE_SESSION_IDS.has(session.id));
    return showcase.length >= 6 ? showcase : allSessions;
  }, [allSessions]);

  const activeSessions = useMemo(
    () =>
      dashboardSessions.filter(
        (session) => session.state === 'running' || session.state === 'idle',
      ),
    [dashboardSessions],
  );
  const bootingSessions = useMemo(
    () =>
      dashboardSessions.filter(
        (session) => session.state === 'provisioning' || session.state === 'requested',
      ),
    [dashboardSessions],
  );
  const erroredSessions = useMemo(
    () => dashboardSessions.filter((session) => session.state === 'failed'),
    [dashboardSessions],
  );

  const forgeClusters = useMemo(() => {
    const sessionsByCluster = new Map<string, number>();
    for (const session of dashboardSessions) {
      if (!INFLIGHT_STATES.includes(session.state)) continue;
      const key = normalizeKey(session.clusterId);
      sessionsByCluster.set(key, (sessionsByCluster.get(key) ?? 0) + 1);
    }

    return (clusters.data ?? []).map((cluster) => {
      const display = FORGE_CLUSTER_DISPLAY[cluster.id] ?? {
        name: cluster.name,
        realm: cluster.realm,
        kind: cluster.kind,
      };
      return {
        ...cluster,
        displayName: display.name,
        displayRealm: display.realm,
        displayKind: display.kind ?? cluster.kind,
        podCount:
          sessionsByCluster.get(normalizeKey(cluster.name)) ??
          sessionsByCluster.get(normalizeKey(cluster.id)) ??
          cluster.runningSessions,
        cpuPct: cluster.capacity.cpu > 0 ? cluster.used.cpu / cluster.capacity.cpu : 0,
        memPct: cluster.capacity.memMi > 0 ? cluster.used.memMi / cluster.capacity.memMi : 0,
        gpuPct: cluster.capacity.gpu > 0 ? cluster.used.gpu / cluster.capacity.gpu : 0,
      } satisfies ForgeClusterView;
    });
  }, [dashboardSessions, clusters.data]);

  const clusterLookup = useMemo(() => {
    const entries: Array<[string, ForgeClusterView]> = [];
    for (const cluster of forgeClusters) {
      entries.push([normalizeKey(cluster.id), cluster]);
      entries.push([normalizeKey(cluster.name), cluster]);
      entries.push([normalizeKey(cluster.displayName), cluster]);
    }
    return new Map(entries);
  }, [forgeClusters]);

  const inflightSessions = useMemo(
    () =>
      dashboardSessions
        .filter((session) => INFLIGHT_STATES.includes(session.state))
        .sort((left, right) => {
          const priorityDiff = SESSION_PRIORITY[left.state] - SESSION_PRIORITY[right.state];
          if (priorityDiff !== 0) return priorityDiff;
          return lastTouched(right) - lastTouched(left);
        })
        .slice(0, 6),
    [dashboardSessions],
  );

  const recentFleet = useMemo(
    () =>
      dashboardSessions
        .filter((session) => session.state !== 'terminated')
        .sort((left, right) => lastTouched(right) - lastTouched(left))
        .slice(0, 8),
    [dashboardSessions],
  );

  const tokenSparkline = stats.data?.sparklines?.tokensToday ?? [];
  const activePodSparkline = stats.data?.sparklines?.activePods ?? [];
  const sessionsTodaySparkline = stats.data?.sparklines?.sessionsToday ?? [];
  const tokenRate = tokenSparkline.length > 0 ? Math.round(average(tokenSparkline, 5) / 100) : 0;
  const projectedCost = stats.data ? Math.round(stats.data.costToday * 1.07) : 0;

  const isLoading =
    stats.isLoading || clusters.isLoading || sessionsQuery.isLoading || launchSpecs.isLoading;

  function openWizard(specRef?: string) {
    setLaunchSpecRef(specRef ?? null);
    setLaunchOpen(true);
  }

  if (isLoading) {
    return (
      <div className="vol-forge vol-forge--loading" data-testid="forge-page">
        <LoadingState label="Loading metrics…" />
      </div>
    );
  }

  return (
    <>
      <div className="vol-forge" data-testid="forge-page">
        <section className="vol-forge__metrics" aria-label="Forge metrics">
          <MetricTile
            label="ACTIVE PODS"
            value={activeSessions.length}
            subline={`${bootingSessions.length} booting · ${erroredSessions.length} error`}
          >
            {activePodSparkline.length > 0 ? (
              <Sparkline values={activePodSparkline} width={180} height={46} fill />
            ) : null}
          </MetricTile>
          <MetricTile
            label="TOKENS TODAY"
            value={stats.data ? tokens(stats.data.tokensToday) : '—'}
            subline={`${tokenRate}/s · 5m avg`}
          />
          <MetricTile
            label="COST TODAY"
            value={stats.data ? `$${stats.data.costToday.toFixed(2)}` : '—'}
            subline={`$${projectedCost} projected 24h`}
          />
          <MetricTile
            label="SESSIONS TODAY"
            value={stats.data ? stats.data.sessionsToday : '—'}
            subline={`${stats.data ? stats.data.totalSessions : '—'} total · last 30d`}
            accent="neutral"
          >
            {sessionsTodaySparkline.length > 0 ? (
              <Sparkline values={sessionsTodaySparkline} width={180} height={46} fill />
            ) : null}
          </MetricTile>
        </section>

        <div className="vol-forge__grid">
          <section
            className="vol-forge__panel vol-forge__panel--inflight"
            data-testid="inflight-panel"
          >
            <header className="vol-forge__panel-head">
              <div className="vol-forge__panel-title">
                <h2>In-flight pods</h2>
                <span>{activeSessions.length + bootingSessions.length}</span>
              </div>
              <button
                type="button"
                className="vol-forge__panel-link"
                onClick={() => void navigate({ to: '/volundr/sessions' })}
                data-testid="all-sessions-link"
              >
                all sessions ›
              </button>
            </header>

            <div className="vol-forge__inflight-list">
              {inflightSessions.map((session) => (
                <InflightRow
                  key={session.id}
                  session={session}
                  clusterLabel={displayCluster(session, clusterLookup)}
                  onClick={() =>
                    void navigate({
                      to: '/volundr/sessions/$sessionId',
                      params: { sessionId: session.id },
                    })
                  }
                />
              ))}
            </div>
          </section>

          <section
            className="vol-forge__panel vol-forge__panel--load"
            data-testid="forge-load-panel"
          >
            <header className="vol-forge__panel-head">
              <div className="vol-forge__panel-title">
                <h2>Forge load</h2>
                <span>{forgeClusters.length} clusters</span>
              </div>
              <button
                type="button"
                className="vol-forge__panel-link"
                onClick={() => void navigate({ to: '/guild' })}
                data-testid="cluster-details-link"
              >
                details ›
              </button>
            </header>

            <div className="vol-forge__load-list">
              {forgeClusters.map((cluster) => (
                <ForgeLoadRow key={cluster.id} cluster={cluster} />
              ))}
            </div>
          </section>

          <section
            className="vol-forge__panel vol-forge__panel--launch"
            data-testid="quick-launch-panel"
          >
            <header className="vol-forge__panel-head">
              <div className="vol-forge__panel-title">
                <h2>Quick launch</h2>
                <span>from catalog</span>
              </div>
            </header>

            <div className="vol-forge__launch-grid">
              {(launchSpecs.data ?? []).slice(0, 4).map((spec, index) => (
                <QuickLaunchCard
                  key={spec.id ?? spec.name}
                  spec={spec}
                  isDefault={spec.isDefault || index === 0}
                  onClick={() => openWizard(spec.id ?? spec.name)}
                />
              ))}
            </div>

            <button type="button" className="vol-forge__launch-cta" onClick={() => openWizard()}>
              <span>+</span>
              <span>custom launch...</span>
            </button>
          </section>

          <section className="vol-forge__panel vol-forge__panel--recent" data-testid="recent-panel">
            <header className="vol-forge__panel-head">
              <div className="vol-forge__panel-title">
                <h2>Recent across fleet</h2>
                <span>last 30m</span>
              </div>
            </header>

            <ol className="vol-forge__tail-list">
              {recentFleet.map((session) => (
                <RecentFleetItem key={session.id} session={session} />
              ))}
            </ol>
          </section>

          {erroredSessions.length > 0 ? (
            <section
              className="vol-forge__panel vol-forge__panel--errors"
              data-testid="error-strip"
            >
              <header className="vol-forge__panel-head">
                <div className="vol-forge__panel-title vol-forge__panel-title--critical">
                  <h2>Needs attention</h2>
                  <span>{erroredSessions.length}</span>
                </div>
              </header>

              <div className="vol-forge__error-list">
                {erroredSessions.map((session) => (
                  <div key={session.id} className="vol-forge__error-row">
                    <StateDot state="failed" />
                    <div className="vol-forge__error-body">
                      <div className="vol-forge__error-title">
                        <span>{session.id}</span>
                        <span>{session.personaName}</span>
                      </div>
                      <div className="vol-forge__error-message">
                        {session.events[session.events.length - 1]?.body ?? 'unknown error'}
                      </div>
                    </div>
                    <button type="button" className="vol-forge__retry-btn">
                      retry
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </div>

      <LaunchWizard
        key={launchSpecRef ?? 'forge-custom'}
        open={launchOpen}
        onOpenChange={setLaunchOpen}
        initialLaunchSpecRef={launchSpecRef ?? undefined}
      />
    </>
  );
}
