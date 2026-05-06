import { useCallback, useMemo, useState } from 'react';
import { LoadingIndicator } from '@/modules/shared';
import {
  useTyrEvents,
  useActiveRaids,
  useClusters,
  useHealthDetailed,
  useSagas,
} from '../../hooks';
import { useFlockConfig } from '../../hooks/useFlockConfig';
import type { SseEvent } from '../../hooks';
import type { RaidStatus } from '../../models';
import { DashboardTopBar } from '../../components/DashboardTopBar';
import { AttentionBar } from '../../components/AttentionBar';
import { StatsStrip } from '../../components/StatsStrip';
import { RaidsTable } from '../../components/RaidsTable';
import { SagasSidebar } from '../../components/SagasSidebar';
import { SystemsHealth } from '../../components/SystemsHealth';
import { EventLog } from '../../components/EventLog';
import { FlockBadge } from '../../components/FlockBadge';
import styles from './DashboardView.module.css';

export function DashboardView() {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [clusterFilter, setClusterFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<RaidStatus | null>(null);
  const [showCompleted, setShowCompleted] = useState(false);

  const { raids, loading: raidsLoading, refresh: refreshRaids, patchRaid } = useActiveRaids();
  const { config: flockConfig } = useFlockConfig();

  const summary = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of raids) {
      counts[r.status] = (counts[r.status] || 0) + 1;
    }
    return counts;
  }, [raids]);
  const { clusters } = useClusters();
  const { health, loading: healthLoading } = useHealthDetailed();
  const { sagas } = useSagas();

  const handleSseEvent = useCallback(
    (event: SseEvent) => {
      try {
        const data = JSON.parse(event.data);

        if (event.type === 'raid.state_changed' && data.tracker_id) {
          patchRaid(data.tracker_id, {
            status: (data.status as string).toLowerCase() as RaidStatus,
            confidence: data.confidence ?? undefined,
          });
          // summary derived from raids — auto-updates
        }

        if (event.type === 'confidence.updated' && data.tracker_id) {
          patchRaid(data.tracker_id, { confidence: data.confidence });
        }

        if (event.type === 'phase.unlocked') {
          refreshRaids();
          // summary derived from raids — auto-updates
        }
      } catch {
        // non-JSON event data, ignore
      }
    },
    [patchRaid, refreshRaids]
  );

  const { events, connected } = useTyrEvents(handleSseEvent);

  const filteredRaids = useMemo(() => {
    const terminal: RaidStatus[] = ['merged', 'failed'];
    let result = raids;
    if (statusFilter) {
      result = result.filter(r => r.status === statusFilter);
    } else if (!showCompleted) {
      result = result.filter(r => !terminal.includes(r.status));
    }
    return result;
  }, [raids, statusFilter, showCompleted]);

  const handleToggle = (id: string) => {
    setExpandedId(prev => (prev === id ? null : id));
  };

  const handleAction = () => {
    refreshRaids();
    setExpandedId(null);
  };

  const handleStatusClick = (status: string) => {
    setStatusFilter(prev => (prev === status ? null : status) as RaidStatus | null);
  };

  if (raidsLoading) {
    return (
      <div className={styles.layout}>
        <LoadingIndicator messages={['Loading dashboard...']} />
      </div>
    );
  }

  return (
    <div className={styles.layout}>
      <DashboardTopBar
        sagaCount={sagas.length}
        raidCount={raids.length}
        clusterCount={clusters.length}
        connected={connected}
        clusters={clusters}
        selectedCluster={clusterFilter}
        onClusterChange={setClusterFilter}
      />
      <AttentionBar raids={raids} />
      <StatsStrip
        summary={summary}
        activeFilter={statusFilter}
        onStatusClick={handleStatusClick}
        showCompleted={showCompleted}
        onToggleCompleted={() => setShowCompleted(v => !v)}
      />
      <div className={styles.body}>
        <div className={styles.left}>
          <RaidsTable
            raids={filteredRaids}
            expandedId={expandedId}
            onToggle={handleToggle}
            onAction={handleAction}
          />
        </div>
        <div className={styles.right}>
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <span className={styles.panelTitle}>Sagas</span>
              <span className={styles.panelCount}>{sagas.length}</span>
            </div>
            <SagasSidebar sagas={sagas} />
          </div>
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <span className={styles.panelTitle}>Systems</span>
            </div>
            <SystemsHealth health={health} loading={healthLoading} />
          </div>
          {flockConfig?.flock_enabled && (
            <div className={styles.panel}>
              <div className={styles.panelHeader}>
                <span className={styles.panelTitle}>Flock</span>
                <FlockBadge />
              </div>
              <div className={styles.flockStats}>
                <span className={styles.flockStat}>
                  <span className={styles.flockStatLabel}>Personas</span>
                  <span className={styles.flockStatValue}>
                    {flockConfig.flock_default_personas.map(p => p.name).join(', ') || '—'}
                  </span>
                </span>
                {flockConfig.flock_llm_config &&
                  Object.keys(flockConfig.flock_llm_config).length > 0 && (
                    <span className={styles.flockStat}>
                      <span className={styles.flockStatLabel}>Model</span>
                      <span className={styles.flockStatValue}>
                        {String(flockConfig.flock_llm_config.model ?? '—')}
                      </span>
                    </span>
                  )}
              </div>
            </div>
          )}
          <div className={styles.eventPanel}>
            <div className={styles.panelHeader}>
              <span className={styles.panelTitle}>Event Log</span>
              <span className={styles.panelBadge}>live</span>
            </div>
            <EventLog events={events} raids={raids} />
          </div>
        </div>
      </div>
    </div>
  );
}
