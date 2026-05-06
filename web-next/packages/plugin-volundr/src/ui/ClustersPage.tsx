import { useMemo, useState } from 'react';
import { EmptyState, ErrorState, LoadingState, Meter, StateDot, cn } from '@niuulabs/ui';
import { ConnectionTypeBadge, MiniBar } from './atoms';
import { useSessionList } from './hooks/useSessionStore';
import { useClusters } from './useClusters';
import type { Cluster, ClusterNode } from '../domain/cluster';
import type { Session, SessionState } from '../domain/session';

type ClusterMetricCard = {
  label: string;
  used: number;
  limit: number;
  unitLabel: string;
  footer: string;
};

type ClusterNodeView = {
  id: string;
  status: ClusterNode['status'];
  cpuPct: number;
  memPct: number;
};

type ClusterSessionView = {
  id: string;
  status: SessionState;
  cpuLabel: string;
  memoryLabel: string;
  connectionType: Session['connectionType'] | null;
};

type ClusterPresentation = {
  name?: string;
  realm?: string;
  region?: string;
  readyNodes?: number;
  totalNodes?: number;
  listCount?: number;
  metrics?: ClusterMetricCard[];
  pods?: ClusterSessionView[];
  nodes?: ClusterNodeView[];
};

type DisplayCluster = Cluster & {
  displayName: string;
  displayRealm: string;
  displayRegion: string;
  readyNodes: number;
  totalNodes: number;
  listCount: number;
  metrics: ClusterMetricCard[];
  nodeRows: ClusterNodeView[];
};

const REALM_ORDER = ['asgard', 'midgard', 'svartalfheim', 'jotunheim'];
const CLUSTER_ORDER = [
  'cl-eitri',
  'cl-valhalla',
  'cl-noatun',
  'cl-glitnir',
  'cl-brokkr',
  'cl-jarnvidr',
];
const ACTIVE_SESSION_STATES: SessionState[] = [
  'requested',
  'provisioning',
  'ready',
  'running',
  'idle',
];

const SESSION_STATE_ORDER: Record<SessionState, number> = {
  running: 0,
  idle: 1,
  ready: 2,
  provisioning: 3,
  requested: 4,
  failed: 5,
  terminating: 6,
  terminated: 7,
};

const PANEL_SURFACE_STYLE = {
  backgroundColor: '#1b1b20',
  borderColor: 'rgba(90, 94, 107, 0.72)',
} as const;

const NODE_SURFACE_STYLE = {
  backgroundColor: '#111216',
  borderColor: 'rgba(90, 94, 107, 0.68)',
} as const;

const PRIMARY_BADGE_STYLE = {
  backgroundColor: 'rgba(58, 155, 228, 0.16)',
  color: '#8fd8ff',
} as const;

const STATUS_PILL_STYLE = {
  backgroundColor: '#141922',
} as const;

const FORGE_BUTTON_STYLE = {
  backgroundColor: 'rgba(28, 124, 173, 0.18)',
  borderColor: 'rgba(81, 178, 232, 0.65)',
} as const;

const CLUSTER_PRESENTATION: Record<string, ClusterPresentation> = {
  'cl-eitri': {
    name: 'Valaskjálf',
    realm: 'asgard',
    region: 'ca-hamilton-1',
    readyNodes: 4,
    totalNodes: 4,
    listCount: 12,
    metrics: [
      { label: 'CPU', used: 68, limit: 128, unitLabel: 'cores', footer: '53% used' },
      { label: 'MEMORY', used: 420, limit: 768, unitLabel: 'GiB', footer: '55% used' },
      { label: 'GPU', used: 2, limit: 4, unitLabel: 'H100', footer: '50% used' },
      { label: 'DISK', used: 3.2, limit: 8, unitLabel: 'TiB', footer: '40% used' },
    ],
    pods: [
      {
        id: 'observatory-canvas-perf',
        status: 'running',
        cpuLabel: '2.1c',
        memoryLabel: '5.4Gi',
        connectionType: 'cli',
      },
      {
        id: 'ravn-triggers-ui',
        status: 'running',
        cpuLabel: '1.3c',
        memoryLabel: '4.8Gi',
        connectionType: 'cli',
      },
      {
        id: 'bifrost-ollama-adapter',
        status: 'idle',
        cpuLabel: '0.2c',
        memoryLabel: '1.1Gi',
        connectionType: null,
      },
      {
        id: 'tyr-raid-cohesion',
        status: 'idle',
        cpuLabel: '0.3c',
        memoryLabel: '3.2Gi',
        connectionType: 'cli',
      },
      {
        id: 'aider-css-migration',
        status: 'running',
        cpuLabel: '1.1c',
        memoryLabel: '3.4Gi',
        connectionType: 'ide',
      },
      {
        id: 'docs-release-notes',
        status: 'idle',
        cpuLabel: '0.1c',
        memoryLabel: '0.9Gi',
        connectionType: 'cli',
      },
      {
        id: 'niuu-integration-tests',
        status: 'requested',
        cpuLabel: '—',
        memoryLabel: '—',
        connectionType: 'cli',
      },
    ],
    nodes: [
      { id: 'valaskjalf-01', status: 'ready', cpuPct: 0.56, memPct: 0.22 },
      { id: 'valaskjalf-02', status: 'ready', cpuPct: 0.58, memPct: 0.54 },
      { id: 'valaskjalf-03', status: 'ready', cpuPct: 0.18, memPct: 0.66 },
      { id: 'valaskjalf-04', status: 'ready', cpuPct: 0.12, memPct: 0.72 },
    ],
  },
  'cl-valhalla': {
    listCount: 6,
  },
  'cl-noatun': {
    listCount: 2,
  },
  'cl-glitnir': {
    listCount: 0,
  },
  'cl-brokkr': {
    name: 'Eitri',
    realm: 'svartalfheim',
    listCount: 1,
  },
  'cl-jarnvidr': {
    listCount: 0,
  },
};

function normalizeKey(value: string) {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function realmLabel(realm: string) {
  return realm.toUpperCase();
}

function formatPercent(used: number, limit: number) {
  if (!limit) return '—';
  return `${Math.round((used / limit) * 100)}% used`;
}

function formatDiskUnit(usedGi: number, totalGi: number) {
  if (totalGi >= 1024) {
    const used = Number((usedGi / 1024).toFixed(1));
    const total = Number((totalGi / 1024).toFixed(1));
    return { used, limit: total, unitLabel: 'TiB', footer: formatPercent(usedGi, totalGi) };
  }
  return { used: usedGi, limit: totalGi, unitLabel: 'GiB', footer: formatPercent(usedGi, totalGi) };
}

function slugify(value: string) {
  return normalizeKey(value);
}

function clusterStatusLabel(status: Cluster['status']) {
  if (status === 'healthy') return 'active';
  if (status === 'warning') return 'booting';
  return 'error';
}

function statusToDotState(status: Cluster['status'] | ClusterNode['status'] | SessionState) {
  switch (status) {
    case 'healthy':
    case 'ready':
      return 'healthy';
    case 'running':
      return 'running';
    case 'warning':
    case 'requested':
    case 'provisioning':
    case 'notready':
      return 'attention';
    case 'error':
    case 'failed':
      return 'failed';
    case 'idle':
    case 'cordoned':
      return 'idle';
    case 'terminated':
      return 'archived';
    case 'terminating':
      return 'degraded';
    default:
      return 'unknown';
  }
}

function metricCardsFor(cluster: Cluster): ClusterMetricCard[] {
  const override = CLUSTER_PRESENTATION[cluster.id]?.metrics;
  if (override) return override;

  const disk = formatDiskUnit(cluster.disk.usedGi, cluster.disk.totalGi);
  return [
    {
      label: 'CPU',
      used: cluster.used.cpu,
      limit: cluster.capacity.cpu,
      unitLabel: 'cores',
      footer: formatPercent(cluster.used.cpu, cluster.capacity.cpu),
    },
    {
      label: 'MEMORY',
      used: Math.round(cluster.used.memMi / 1024),
      limit: Math.round(cluster.capacity.memMi / 1024),
      unitLabel: 'GiB',
      footer: formatPercent(cluster.used.memMi, cluster.capacity.memMi),
    },
    {
      label: 'GPU',
      used: cluster.used.gpu,
      limit: cluster.capacity.gpu,
      unitLabel: cluster.capacity.gpu > 0 ? 'GPU' : '—',
      footer:
        cluster.capacity.gpu > 0
          ? formatPercent(cluster.used.gpu, cluster.capacity.gpu)
          : 'not provisioned',
    },
    {
      label: 'DISK',
      used: disk.used,
      limit: disk.limit,
      unitLabel: disk.unitLabel,
      footer: disk.footer,
    },
  ];
}

function nodeRowsFor(cluster: Cluster, displayName: string): ClusterNodeView[] {
  const override = CLUSTER_PRESENTATION[cluster.id]?.nodes;
  if (override) return override;

  return cluster.nodes.map((node, index) => {
    const base = ((index + 2) * 17 + cluster.id.length * 11) % 60;
    return {
      id: `${slugify(displayName)}-${String(index + 1).padStart(2, '0')}`,
      status: node.status,
      cpuPct: Math.min(0.86, 0.14 + base / 100),
      memPct: Math.min(0.9, 0.22 + ((base + 19) % 55) / 100),
    };
  });
}

function displayCluster(cluster: Cluster, sessionCount: number): DisplayCluster {
  const presentation = CLUSTER_PRESENTATION[cluster.id];
  const displayName = presentation?.name ?? cluster.name;
  const readyNodes =
    presentation?.readyNodes ?? cluster.nodes.filter((node) => node.status === 'ready').length;
  const totalNodes = presentation?.totalNodes ?? cluster.nodes.length;

  return {
    ...cluster,
    displayName,
    displayRealm: presentation?.realm ?? cluster.realm,
    displayRegion: presentation?.region ?? cluster.region,
    readyNodes,
    totalNodes,
    listCount: presentation?.listCount ?? sessionCount,
    metrics: metricCardsFor(cluster),
    nodeRows: nodeRowsFor(cluster, displayName),
  };
}

function formatSessionResource(value: number | undefined, unit: string) {
  if (value == null || value <= 0) return '—';
  return `${value.toFixed(1)}${unit}`;
}

function compareSessions(a: Session, b: Session) {
  const stateDelta = SESSION_STATE_ORDER[a.state] - SESSION_STATE_ORDER[b.state];
  if (stateDelta !== 0) return stateDelta;

  const aActivity = new Date(a.lastActivityAt ?? a.readyAt ?? a.startedAt).getTime();
  const bActivity = new Date(b.lastActivityAt ?? b.readyAt ?? b.startedAt).getTime();
  return bActivity - aActivity;
}

function sessionBelongsToCluster(session: Session, cluster: DisplayCluster) {
  const clusterKeys = new Set([
    normalizeKey(cluster.id),
    normalizeKey(cluster.name),
    normalizeKey(cluster.displayName),
  ]);
  return clusterKeys.has(normalizeKey(session.clusterId));
}

function sessionRowsFor(cluster: DisplayCluster, sessions: Session[]): ClusterSessionView[] {
  const override = CLUSTER_PRESENTATION[cluster.id]?.pods;
  if (override) return override;

  return sessions
    .filter((session) => ACTIVE_SESSION_STATES.includes(session.state))
    .filter((session) => sessionBelongsToCluster(session, cluster))
    .sort(compareSessions)
    .map((session) => ({
      id: session.id,
      status: session.state,
      cpuLabel: formatSessionResource(session.resources.cpuUsed, 'c'),
      memoryLabel: formatSessionResource(session.resources.memUsedMi / 1024, 'Gi'),
      connectionType: session.connectionType ?? null,
    }));
}

function MetricCard({ card }: { card: ClusterMetricCard }) {
  return (
    <section className="niuu-rounded-xl niuu-border niuu-p-4" style={PANEL_SURFACE_STYLE}>
      <div className="niuu-flex niuu-items-baseline niuu-justify-between">
        <span className="niuu-font-mono niuu-text-xs niuu-uppercase niuu-tracking-[0.22em] niuu-text-text-faint">
          {card.label}
        </span>
        <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
          {card.unitLabel}
        </span>
      </div>
      <div className="niuu-mt-2 niuu-font-sans niuu-text-[18px] niuu-font-semibold niuu-tracking-tight niuu-text-text-primary">
        {card.used}/{card.limit}
      </div>
      <Meter className="niuu-mt-3" used={card.used} limit={card.limit} />
      <div className="niuu-mt-2 niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
        {card.footer}
      </div>
    </section>
  );
}

function PodsPanel({ pods, isLoading }: { pods: ClusterSessionView[]; isLoading: boolean }) {
  return (
    <section className="niuu-rounded-xl niuu-border" style={PANEL_SURFACE_STYLE}>
      <header className="niuu-flex niuu-items-baseline niuu-gap-3 niuu-border-b niuu-border-border niuu-px-4 niuu-py-3">
        <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary">
          Pods on this forge
        </h3>
        <span className="niuu-font-mono niuu-text-base niuu-text-text-faint">{pods.length}</span>
      </header>
      {isLoading ? (
        <div className="niuu-p-4">
          <LoadingState label="Loading sessions…" />
        </div>
      ) : pods.length === 0 ? (
        <div className="niuu-p-4 niuu-font-mono niuu-text-xs niuu-text-text-faint">
          no niuu sessions active on this forge
        </div>
      ) : (
        <ul className="niuu-divide-y niuu-divide-border">
          {pods.map((pod) => (
            <li
              key={pod.id}
              className="niuu-grid niuu-items-center niuu-gap-3 niuu-px-4 niuu-py-3"
              style={{ gridTemplateColumns: 'auto minmax(0, 1fr) auto auto' }}
            >
              <StateDot
                state={statusToDotState(pod.status)}
                pulse={pod.status === 'running'}
                size={10}
              />
              <span className="niuu-font-mono niuu-text-[14px] niuu-font-medium niuu-text-text-primary">
                {pod.id}
              </span>
              <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-faint">
                {pod.cpuLabel} • {pod.memoryLabel}
              </span>
              <span className="niuu-inline-flex niuu-items-center niuu-justify-end">
                {pod.connectionType ? (
                  <ConnectionTypeBadge connectionType={pod.connectionType} />
                ) : (
                  <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">—</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function NodesPanel({
  nodes,
  readyNodes,
  totalNodes,
}: {
  nodes: ClusterNodeView[];
  readyNodes: number;
  totalNodes: number;
}) {
  return (
    <section className="niuu-rounded-xl niuu-border" style={PANEL_SURFACE_STYLE}>
      <header className="niuu-flex niuu-items-baseline niuu-gap-3 niuu-border-b niuu-border-border niuu-px-4 niuu-py-3">
        <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary">Nodes</h3>
        <span className="niuu-font-mono niuu-text-base niuu-text-text-faint">
          {readyNodes}/{totalNodes}
        </span>
      </header>
      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-p-4">
        {nodes.map((node) => (
          <article
            key={node.id}
            className="niuu-rounded-lg niuu-border niuu-p-3"
            style={NODE_SURFACE_STYLE}
          >
            <div className="niuu-flex niuu-items-center niuu-gap-3">
              <StateDot state={statusToDotState(node.status)} size={9} />
              <span className="niuu-font-mono niuu-text-[13px] niuu-font-medium niuu-text-text-primary">
                {node.id}
              </span>
            </div>
            <div className="niuu-mt-3 niuu-space-y-2">
              <MiniBar label="cpu" value={node.cpuPct} />
              <MiniBar label="mem" value={node.memPct} />
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ClustersPage() {
  const { data, isLoading, isError, error } = useClusters();
  const sessionsQuery = useSessionList();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const sessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);
  const clusters = useMemo(() => {
    const mappedClusters = (data ?? []).map((cluster) => {
      const sessionCount = sessions.filter((session) => {
        const presentation = CLUSTER_PRESENTATION[cluster.id];
        const displayName = presentation?.name ?? cluster.name;
        const keys = new Set([
          normalizeKey(cluster.id),
          normalizeKey(cluster.name),
          normalizeKey(displayName),
        ]);
        return keys.has(normalizeKey(session.clusterId));
      }).length;
      return displayCluster(cluster, sessionCount);
    });

    return mappedClusters.sort((left, right) => {
      const clusterDelta = CLUSTER_ORDER.indexOf(left.id) - CLUSTER_ORDER.indexOf(right.id);
      if (clusterDelta !== 0) return clusterDelta;
      const realmDelta =
        REALM_ORDER.indexOf(left.displayRealm) - REALM_ORDER.indexOf(right.displayRealm);
      if (realmDelta !== 0) return realmDelta;
      return left.displayName.localeCompare(right.displayName);
    });
  }, [data, sessions]);

  const selectedCluster = useMemo(
    () => clusters.find((cluster) => cluster.id === selectedId) ?? clusters[0] ?? null,
    [clusters, selectedId],
  );

  const grouped = useMemo(() => {
    const map = new Map<string, DisplayCluster[]>();
    for (const cluster of clusters) {
      const key = cluster.displayRealm;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(cluster);
    }
    return REALM_ORDER.map((realm) => [realm, map.get(realm) ?? []] as const).filter(
      ([, items]) => items.length > 0,
    );
  }, [clusters]);

  const clusterSessions = useMemo(() => {
    if (!selectedCluster) return [];
    return sessionRowsFor(selectedCluster, sessions);
  }, [selectedCluster, sessions]);

  if (isLoading) {
    return (
      <div
        className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-items-center niuu-justify-center"
        data-testid="clusters-page"
      >
        <LoadingState label="Loading clusters…" />
      </div>
    );
  }

  if (isError) {
    return (
      <div
        className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-items-center niuu-justify-center"
        data-testid="clusters-page"
      >
        <ErrorState
          title="Failed to load clusters"
          message={error instanceof Error ? error.message : 'Unknown error'}
        />
      </div>
    );
  }

  if (!selectedCluster) {
    return (
      <div
        className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-items-center niuu-justify-center"
        data-testid="clusters-page"
      >
        <EmptyState
          title="No clusters registered"
          description="Connect a realm to start placing sessions on a forge."
        />
      </div>
    );
  }

  return (
    <div
      className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-bg-bg-primary"
      data-testid="clusters-page"
    >
      <div className="niuu-relative niuu-flex niuu-min-h-0 niuu-flex-1">
        <aside
          className={cn(
            'niuu-flex niuu-min-h-0 niuu-shrink-0 niuu-flex-col niuu-overflow-hidden niuu-border-r niuu-border-border-subtle niuu-bg-[#0b0c10] niuu-transition-[width] niuu-duration-200',
            sidebarCollapsed ? 'niuu-w-[54px]' : 'niuu-w-[350px]',
          )}
          aria-label="Clusters by realm"
          data-testid="clusters-sidebar"
        >
          {sidebarCollapsed ? (
            <div className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-hidden">
              <div className="niuu-flex niuu-items-center niuu-justify-center niuu-border-b niuu-border-border-subtle niuu-py-3">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(false)}
                  className="niuu-font-mono niuu-text-sm niuu-text-text-muted"
                  aria-label="Expand clusters sidebar"
                >
                  ›
                </button>
              </div>
              <div className="niuu-flex-1 niuu-overflow-y-auto">
                {grouped.map(([realm, items]) => (
                  <div key={realm} className="niuu-mb-5 niuu-flex niuu-flex-col niuu-gap-2">
                    {items.map((cluster) => (
                      <button
                        key={cluster.id}
                        type="button"
                        onClick={() => setSelectedId(cluster.id)}
                        className={cn(
                          'niuu-flex niuu-w-full niuu-items-center niuu-justify-center niuu-border-l-2 niuu-py-2',
                          selectedCluster.id === cluster.id
                            ? 'niuu-border-brand niuu-bg-[#12212b]'
                            : 'niuu-border-transparent hover:niuu-bg-bg-tertiary',
                        )}
                        aria-label={cluster.displayName}
                      >
                        <StateDot state={statusToDotState(cluster.status)} size={9} />
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-hidden">
              <div className="niuu-flex niuu-items-start niuu-justify-between niuu-border-b niuu-border-border-subtle niuu-px-5 niuu-py-4">
                <div className="niuu-flex niuu-items-baseline niuu-gap-2">
                  <div>
                    <h1 className="niuu-text-[15px] niuu-font-semibold niuu-text-text-primary">
                      Clusters
                    </h1>
                    <p className="niuu-mt-1 niuu-font-sans niuu-text-[11px] niuu-text-text-secondary">
                      by realm
                    </p>
                  </div>
                  <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-faint">
                    {clusters.length}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(true)}
                  className="niuu-font-mono niuu-text-lg niuu-text-text-muted"
                  aria-label="Collapse clusters sidebar"
                >
                  ‹
                </button>
              </div>

              <div className="niuu-flex-1 niuu-overflow-y-auto niuu-pb-6">
                {grouped.map(([realm, items]) => (
                  <section key={realm} className="niuu-mt-6">
                    <header className="niuu-mb-2 niuu-flex niuu-items-center niuu-justify-between niuu-px-5">
                      <h2 className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.24em] niuu-text-text-faint">
                        {realmLabel(realm)}
                      </h2>
                      <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-faint">
                        {items.length}
                      </span>
                    </header>
                    <ul className="niuu-space-y-0.5">
                      {items.map((cluster) => (
                        <li key={cluster.id}>
                          <button
                            className={cn(
                              'niuu-grid niuu-w-full niuu-items-center niuu-gap-3 niuu-px-5 niuu-py-2.5 niuu-text-left niuu-transition-colors',
                              selectedCluster.id === cluster.id
                                ? 'niuu-bg-[#12212b] niuu-text-text-primary'
                                : 'niuu-text-text-secondary hover:niuu-bg-bg-panel',
                            )}
                            onClick={() => setSelectedId(cluster.id)}
                            type="button"
                            style={{ gridTemplateColumns: 'auto minmax(0, 1fr) auto' }}
                          >
                            <StateDot state={statusToDotState(cluster.status)} size={8} />
                            <span className="niuu-font-mono niuu-text-[13px] niuu-font-medium">
                              {cluster.displayName}
                            </span>
                            <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-faint">
                              {cluster.listCount}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            </div>
          )}
        </aside>

        <section className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-flex-col niuu-px-8 niuu-py-5">
          <header className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-6 niuu-border-b niuu-border-border-subtle niuu-pb-5">
            <div className="niuu-flex niuu-flex-col niuu-gap-2">
              <div className="niuu-flex niuu-items-center niuu-gap-4">
                <span
                  className="niuu-inline-flex niuu-rounded-md niuu-px-3 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em]"
                  style={PRIMARY_BADGE_STYLE}
                >
                  {selectedCluster.kind === 'primary' ? 'PRIMARY' : selectedCluster.kind}
                </span>
                <h2 className="niuu-text-[17px] niuu-font-semibold niuu-leading-none niuu-tracking-tight niuu-text-text-primary">
                  {selectedCluster.displayName}
                </h2>
                <span className="niuu-font-mono niuu-text-[13px] niuu-text-text-faint">
                  · {selectedCluster.displayRealm}
                </span>
                <span
                  className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-full niuu-px-3 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary"
                  style={STATUS_PILL_STYLE}
                >
                  <StateDot state={statusToDotState(selectedCluster.status)} size={9} />
                  {clusterStatusLabel(selectedCluster.status)}
                </span>
              </div>
              <div className="niuu-font-mono niuu-text-[13px] niuu-text-text-faint">
                {selectedCluster.displayRegion} · {selectedCluster.readyNodes}/
                {selectedCluster.totalNodes} nodes ready
              </div>
            </div>
            <div className="niuu-flex niuu-items-center niuu-gap-8 niuu-pt-1">
              <button
                className="niuu-font-mono niuu-text-[13px] niuu-text-text-secondary hover:niuu-text-text-primary"
                type="button"
              >
                cordon
              </button>
              <button
                className="niuu-font-mono niuu-text-[13px] niuu-text-text-secondary hover:niuu-text-text-primary"
                type="button"
              >
                drain
              </button>
              <button
                className="niuu-inline-flex niuu-items-center niuu-gap-3 niuu-rounded-xl niuu-border niuu-px-5 niuu-py-2.5 niuu-font-mono niuu-text-[13px] niuu-text-text-primary"
                style={FORGE_BUTTON_STYLE}
                type="button"
              >
                <span className="niuu-text-lg">+</span>
                forge here
              </button>
            </div>
          </header>

          <div className="niuu-mt-6 niuu-grid niuu-grid-cols-4 niuu-gap-4">
            {selectedCluster.metrics.map((card) => (
              <MetricCard key={card.label} card={card} />
            ))}
          </div>

          <div
            className="niuu-mt-5 niuu-grid niuu-min-h-0 niuu-flex-1 niuu-gap-5"
            style={{ gridTemplateColumns: 'minmax(0, 1.45fr) minmax(0, 1fr)' }}
          >
            <PodsPanel pods={clusterSessions} isLoading={sessionsQuery.isLoading} />
            <NodesPanel
              nodes={selectedCluster.nodeRows}
              readyNodes={selectedCluster.readyNodes}
              totalNodes={selectedCluster.totalNodes}
            />
          </div>
        </section>
      </div>
    </div>
  );
}
