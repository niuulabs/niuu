import { useState } from 'react';
import { LoadingIndicator } from '@/modules/shared';
import { useTyrSessions } from '../../hooks';
import { useFlockConfig } from '../../hooks/useFlockConfig';
import type { VolundrSession, TimelineResponse, TimelineEvent } from '../../hooks/useTyrSessions';
import { BranchTag } from '../../components/BranchTag';
import { FlockBadge } from '../../components/FlockBadge';
import styles from './SessionsView.module.css';

function statusColor(status: string): string {
  switch (status) {
    case 'running':
      return 'var(--color-accent-emerald)';
    case 'stopped':
    case 'completed':
      return 'var(--color-text-muted)';
    case 'failed':
      return 'var(--color-accent-red)';
    case 'starting':
    case 'creating':
      return 'var(--color-brand)';
    default:
      return 'var(--color-text-secondary)';
  }
}

function eventIcon(type: string): string {
  switch (type) {
    case 'message':
      return '\u2709';
    case 'file':
      return '\u2699';
    case 'git':
      return '\u2B55';
    case 'terminal':
      return '\u25B6';
    case 'error':
      return '\u26A0';
    case 'session':
      return '\u2022';
    default:
      return '\u2022';
  }
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function TimelineView({ timeline }: { timeline: TimelineResponse }) {
  return (
    <div className={styles.timeline}>
      {timeline.commits.length > 0 && (
        <div className={styles.commitList}>
          <span className={styles.sectionLabel}>Commits</span>
          {timeline.commits.map(c => (
            <div key={c.hash} className={styles.commitRow}>
              <span className={styles.commitHash}>{c.hash}</span>
              <span className={styles.commitMsg}>{c.msg}</span>
              <span className={styles.commitTime}>{c.time}</span>
            </div>
          ))}
        </div>
      )}
      {timeline.files.length > 0 && (
        <div className={styles.fileList}>
          <span className={styles.sectionLabel}>Files changed</span>
          {timeline.files.map(f => (
            <div key={f.path} className={styles.fileRow}>
              <span className={styles.fileStatus} data-status={f.status}>
                {f.status}
              </span>
              <span className={styles.filePath}>{f.path}</span>
              <span className={styles.fileDiff}>
                {f.ins > 0 && <span className={styles.ins}>+{f.ins}</span>}
                {f.del > 0 && <span className={styles.del}>-{f.del}</span>}
              </span>
            </div>
          ))}
        </div>
      )}
      {timeline.events.length > 0 && (
        <div className={styles.eventList}>
          <span className={styles.sectionLabel}>Events</span>
          {timeline.events.slice(-30).map((e: TimelineEvent, i: number) => (
            <div key={i} className={styles.eventRow}>
              <span className={styles.eventTime}>{formatTime(e.t)}</span>
              <span className={styles.eventIcon}>{eventIcon(e.type)}</span>
              <span className={styles.eventLabel}>{e.label}</span>
              {e.tokens != null && <span className={styles.eventTokens}>{e.tokens}t</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SessionRow({
  session,
  onExpand,
  expanded,
  timeline,
  timelineLoading,
  flockEnabled,
}: {
  session: VolundrSession;
  onExpand: () => void;
  expanded: boolean;
  timeline: TimelineResponse | null;
  timelineLoading: boolean;
  flockEnabled: boolean;
}) {
  const isFlock = session.workload_type === 'ravn_flock';
  const participantCount = session.mesh_participants?.length;

  return (
    <div className={styles.sessionCard}>
      <button type="button" className={styles.sessionHeader} onClick={onExpand}>
        <span className={styles.sessionName}>{session.name}</span>
        <span className={styles.sessionStatus} style={{ color: statusColor(session.status) }}>
          {session.status}
        </span>
        <span className={styles.sessionModel}>{session.model}</span>
        {session.source?.branch && <BranchTag source={session.source.branch} />}
        {session.tracker_issue_id && (
          <span className={styles.issueTag}>{session.tracker_issue_id}</span>
        )}
        {flockEnabled && isFlock && <FlockBadge participantCount={participantCount} />}
        <span className={styles.sessionTokens}>{session.tokens_used.toLocaleString()} tokens</span>
        <span className={styles.chevron} data-expanded={expanded}>
          {'\u25B6'}
        </span>
      </button>
      {expanded && (
        <div className={styles.sessionDetail}>
          <div className={styles.detailMeta}>
            <span>Repo: {session.source?.repo || 'n/a'}</span>
            <span>Messages: {session.message_count}</span>
            <span>Created: {new Date(session.created_at).toLocaleString()}</span>
            <span>Last active: {new Date(session.last_active).toLocaleString()}</span>
            {session.code_endpoint && (
              <a
                href={session.code_endpoint}
                target="_blank"
                rel="noopener noreferrer"
                className={styles.endpointLink}
              >
                Open Editor
              </a>
            )}
            {session.issue_tracker_url && (
              <a
                href={session.issue_tracker_url}
                target="_blank"
                rel="noopener noreferrer"
                className={styles.endpointLink}
              >
                Tracker Issue
              </a>
            )}
          </div>
          {session.error && <div className={styles.sessionError}>{session.error}</div>}
          {timelineLoading && <div className={styles.timelineLoading}>Loading timeline...</div>}
          {timeline && <TimelineView timeline={timeline} />}
          {!timeline && !timelineLoading && (
            <div className={styles.timelineLoading}>No timeline data</div>
          )}
        </div>
      )}
    </div>
  );
}

export function SessionsView() {
  const { sessions, loading, error, getTimeline } = useTyrSessions();
  const { config: flockConfig } = useFlockConfig();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [timelines, setTimelines] = useState<Record<string, TimelineResponse | null>>({});
  const [loadingTimelines, setLoadingTimelines] = useState<Set<string>>(new Set());

  const handleExpand = async (sessionId: string) => {
    const isExpanding = !expanded.has(sessionId);
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(sessionId)) {
        next.delete(sessionId);
      } else {
        next.add(sessionId);
      }
      return next;
    });

    if (isExpanding && !(sessionId in timelines)) {
      setLoadingTimelines(prev => new Set(prev).add(sessionId));
      const timeline = await getTimeline(sessionId);
      setTimelines(prev => ({ ...prev, [sessionId]: timeline }));
      setLoadingTimelines(prev => {
        const next = new Set(prev);
        next.delete(sessionId);
        return next;
      });
    }
  };

  if (loading) {
    return <LoadingIndicator messages={['Loading sessions...']} />;
  }

  if (error) {
    return <div className={styles.error}>{error}</div>;
  }

  const sorted = [...sessions].sort((a, b) => {
    const statusOrder: Record<string, number> = {
      running: 0,
      starting: 1,
      creating: 2,
      stopped: 3,
      completed: 4,
      failed: 5,
    };
    return (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9);
  });

  const flockEnabled = flockConfig?.flock_enabled ?? false;
  const flockCount = flockEnabled
    ? sessions.filter(s => s.workload_type === 'ravn_flock').length
    : 0;

  return (
    <div className={styles.container}>
      <div className={styles.stats}>
        <span className={styles.statItem}>
          {sessions.filter(s => s.status === 'running').length} running
        </span>
        <span className={styles.statItem}>{sessions.length} total</span>
        {flockEnabled && flockCount > 0 && (
          <span className={styles.statItem}>{flockCount} flock</span>
        )}
      </div>
      <div className={styles.list}>
        {sorted.map(session => (
          <SessionRow
            key={session.id}
            session={session}
            expanded={expanded.has(session.id)}
            onExpand={() => handleExpand(session.id)}
            timeline={timelines[session.id] ?? null}
            timelineLoading={loadingTimelines.has(session.id)}
            flockEnabled={flockEnabled}
          />
        ))}
      </div>
      {sessions.length === 0 && <div className={styles.empty}>No sessions</div>}
    </div>
  );
}
