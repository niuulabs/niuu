import { useRef, useEffect } from 'react';
import { Workflow } from 'lucide-react';
import type { MeshEvent } from '@/modules/shared/hooks/useSkuldChat';
import { MeshEventCard } from '../MeshEventCard';
import styles from './MeshCascadePanel.module.css';

interface MeshCascadePanelProps {
  events: readonly MeshEvent[];
  onEventClick?: (event: MeshEvent) => void;
  className?: string;
}

/**
 * Right-side panel showing the mesh cascade - all events from persona interactions.
 * Displays outcomes, delegations, and notifications in chronological order.
 */
export function MeshCascadePanel({ events, onEventClick, className }: MeshCascadePanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(events.length);

  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    if (events.length > prevCountRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
    prevCountRef.current = events.length;
  }, [events.length]);

  if (events.length === 0) {
    return null;
  }

  // Count by type for summary
  const outcomes = events.filter(e => e.type === 'outcome').length;
  const delegations = events.filter(e => e.type === 'mesh_message').length;
  const notifications = events.filter(e => e.type === 'notification').length;

  // Get latest verdict for status indicator
  const latestOutcome = [...events].reverse().find(e => e.type === 'outcome');
  const latestVerdict = latestOutcome?.type === 'outcome' ? latestOutcome.verdict : undefined;

  return (
    <div className={`${styles.panel} ${className ?? ''}`} data-expanded>
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <Workflow className={styles.icon} />
          <span className={styles.title}>Mesh Cascade</span>
          <span className={styles.badge}>{events.length}</span>
          {latestVerdict && <span className={styles.statusDot} data-verdict={latestVerdict} />}
        </div>

        <div className={styles.headerRight}>
          <span className={styles.summary}>
            {outcomes > 0 && (
              <span>
                {outcomes} outcome{outcomes !== 1 ? 's' : ''}
              </span>
            )}
            {delegations > 0 && (
              <span>
                {delegations} delegation{delegations !== 1 ? 's' : ''}
              </span>
            )}
            {notifications > 0 && (
              <span>
                {notifications} alert{notifications !== 1 ? 's' : ''}
              </span>
            )}
          </span>
        </div>
      </div>

      <div className={styles.content} ref={scrollRef}>
        <div className={styles.timeline}>
          {events.map(event => (
            <div
              key={event.id}
              className={styles.timelineItem}
              onClick={() => onEventClick?.(event)}
              style={{ cursor: onEventClick ? 'pointer' : undefined }}
            >
              <div className={styles.timelineLine} />
              <MeshEventCard event={event} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
