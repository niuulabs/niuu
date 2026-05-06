import { useCallback, useEffect, useRef, useState } from 'react';
import { X, ArrowDownIcon } from 'lucide-react';
import type { RoomParticipant, AgentInternalEvent } from '@/modules/shared/hooks/useSkuldChat';
import { resolveParticipantColor } from '@/modules/shared/utils/participantColor';
import styles from './AgentDetailPanel.module.css';

const SCROLL_THRESHOLD = 150;

interface AgentDetailPanelProps {
  /** The participant whose event stream should be shown */
  participant: RoomParticipant;
  /** Internal events for this agent relayed through the broker */
  events: readonly AgentInternalEvent[];
  /** Called when the close button is pressed or Escape is pressed */
  onClose: () => void;
}

function EventItem({ event }: { event: AgentInternalEvent }) {
  const data = typeof event.data === 'string' ? event.data : JSON.stringify(event.data, null, 2);
  const toolName = event.metadata?.tool_name as string | undefined;

  if (event.frameType === 'thought') {
    return (
      <div className={styles.eventItem} data-type="thought">
        <span className={styles.eventLabel}>thinking</span>
        <pre className={styles.eventContent}>{data}</pre>
      </div>
    );
  }

  if (event.frameType === 'tool_start') {
    const input = event.metadata?.input;
    return (
      <div className={styles.eventItem} data-type="tool_start">
        <span className={styles.eventLabel}>tool: {toolName ?? data}</span>
        {Boolean(input) && (
          <pre className={styles.eventContent}>
            {typeof input === 'string' ? input : JSON.stringify(input, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  if (event.frameType === 'tool_result') {
    return (
      <div className={styles.eventItem} data-type="tool_result">
        <span className={styles.eventLabel}>result: {toolName ?? ''}</span>
        <pre className={styles.eventContent}>{data}</pre>
      </div>
    );
  }

  return (
    <div className={styles.eventItem}>
      <span className={styles.eventLabel}>{event.frameType}</span>
      <pre className={styles.eventContent}>{data}</pre>
    </div>
  );
}

/**
 * Slide-out right panel that displays the internal event stream (thinking blocks,
 * tool calls) for a single Ravn agent, relayed through the Skuld broker.
 */
export function AgentDetailPanel({ participant, events, onClose }: AgentDetailPanelProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
  const prevCountRef = useRef(0);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [newCount, setNewCount] = useState(0);

  const accentColor = resolveParticipantColor(participant.color);
  const statusLabel =
    participant.status === 'idle'
      ? 'idle'
      : participant.status === 'thinking'
        ? 'thinking'
        : participant.status === 'tool_executing'
          ? 'running tool'
          : participant.status;

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    messagesEndRef.current?.scrollIntoView?.({ behavior });
    setNewCount(0);
  }, []);

  // Track scroll position
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;

    const handleScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      isNearBottomRef.current = dist <= SCROLL_THRESHOLD;
      setShowScrollBtn(dist > SCROLL_THRESHOLD * 2);
      if (isNearBottomRef.current) setNewCount(0);
    };

    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // Auto-scroll on new events
  useEffect(() => {
    const count = events.length;
    const delta = count - prevCountRef.current;
    prevCountRef.current = count;

    if (delta === 0) return;

    if (isNearBottomRef.current) {
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
      return;
    }

    setNewCount(prev => prev + delta);
  }, [events.length]);

  // Close on Escape key
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  return (
    <div className={styles.panel} data-testid="agent-detail-panel">
      {/* ── Header ── */}
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span
            className={styles.personaDot}
            style={{ '--agent-color': accentColor } as React.CSSProperties}
          />
          <span
            className={styles.personaName}
            style={{ '--agent-color': accentColor } as React.CSSProperties}
            data-testid="agent-persona-name"
          >
            {participant.displayName
              ? `${participant.displayName} (${participant.persona})`
              : participant.persona}
          </span>
        </div>

        <div className={styles.headerRight}>
          <span
            className={styles.activityBadge}
            data-status={participant.status}
            data-testid="agent-activity-status"
          >
            {statusLabel}
          </span>

          <button
            type="button"
            className={styles.closeBtn}
            onClick={onClose}
            aria-label="Close agent detail panel"
            data-testid="agent-detail-close"
          >
            <X className={styles.closeIcon} />
          </button>
        </div>
      </div>

      {/* ── Event stream ── */}
      <div className={styles.messagesContainer} ref={scrollContainerRef}>
        <div className={styles.messagesInner}>
          {events.length === 0 ? (
            <div className={styles.emptyState} data-testid="agent-detail-empty">
              Waiting for agent activity…
            </div>
          ) : (
            events.map(evt => <EventItem key={evt.id} event={evt} />)
          )}
          <div ref={messagesEndRef} />
        </div>

        {showScrollBtn && (
          <button
            type="button"
            className={styles.scrollToBottom}
            onClick={() => scrollToBottom('smooth')}
            aria-label="Scroll to bottom"
          >
            <ArrowDownIcon className={styles.scrollIcon} />
            {newCount > 0 && (
              <span className={styles.scrollBadge}>{newCount > 99 ? '99+' : newCount}</span>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
