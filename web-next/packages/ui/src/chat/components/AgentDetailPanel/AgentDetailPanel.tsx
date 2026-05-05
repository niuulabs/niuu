import { useCallback, useEffect, useRef, useState } from 'react';
import { X, ArrowDownIcon } from 'lucide-react';
import { resolveParticipantColor } from '../../utils/participantColor';
import type { RoomParticipant, AgentInternalEvent } from '../../types';
import './AgentDetailPanel.css';

const SCROLL_THRESHOLD = 150;

interface AgentDetailPanelProps {
  participant: RoomParticipant;
  events: readonly AgentInternalEvent[];
  onClose: () => void;
}

function EventItem({ event }: { event: AgentInternalEvent }) {
  const data = typeof event.data === 'string' ? event.data : JSON.stringify(event.data, null, 2);
  const toolName = event.metadata?.tool_name as string | undefined;

  if (event.frameType === 'thought') {
    return (
      <div className="niuu-chat-agent-event" data-type="thought">
        <span className="niuu-chat-agent-event-label">thinking</span>
        <pre className="niuu-chat-agent-event-content">{data}</pre>
      </div>
    );
  }

  if (event.frameType === 'tool_start') {
    const input = event.metadata?.input;
    return (
      <div className="niuu-chat-agent-event" data-type="tool_start">
        <span className="niuu-chat-agent-event-label">tool: {toolName ?? data}</span>
        {Boolean(input) && (
          <pre className="niuu-chat-agent-event-content">
            {typeof input === 'string' ? input : JSON.stringify(input, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  if (event.frameType === 'tool_result') {
    return (
      <div className="niuu-chat-agent-event" data-type="tool_result">
        <span className="niuu-chat-agent-event-label">result: {toolName ?? ''}</span>
        <pre className="niuu-chat-agent-event-content">{data}</pre>
      </div>
    );
  }

  return (
    <div className="niuu-chat-agent-event">
      <span className="niuu-chat-agent-event-label">{event.frameType}</span>
      <pre className="niuu-chat-agent-event-content">{data}</pre>
    </div>
  );
}

export function AgentDetailPanel({ participant, events, onClose }: AgentDetailPanelProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
  const prevCountRef = useRef(0);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [newCount, setNewCount] = useState(0);

  const accentColor = resolveParticipantColor(participant.peerId, participant.color);

  const statusLabel =
    participant.status === 'idle'
      ? 'idle'
      : participant.status === 'thinking'
        ? 'thinking'
        : participant.status === 'tool_executing'
          ? 'running tool'
          : (participant.status ?? 'unknown');

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    messagesEndRef.current?.scrollIntoView?.({ behavior });
    setNewCount(0);
  }, []);

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

  useEffect(() => {
    const count = events.length;
    const delta = count - prevCountRef.current;
    prevCountRef.current = count;
    if (delta === 0) return;
    if (isNearBottomRef.current) {
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
      return;
    }
    setNewCount((prev) => prev + delta);
  }, [events.length]);

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
    <div className="niuu-chat-agent-panel" data-testid="agent-detail-panel">
      <div className="niuu-chat-agent-panel-header">
        <div className="niuu-chat-agent-panel-header-left">
          <span
            className="niuu-chat-agent-persona-dot"
            style={{ '--niuu-agent-color': accentColor } as React.CSSProperties}
          />
          <span
            className="niuu-chat-agent-persona-name"
            style={{ '--niuu-agent-color': accentColor } as React.CSSProperties}
            data-testid="agent-persona-name"
          >
            {participant.displayName
              ? `${participant.displayName} (${participant.persona})`
              : participant.persona}
          </span>
        </div>
        <div className="niuu-chat-agent-panel-header-right">
          <span
            className="niuu-chat-agent-activity-badge"
            data-status={participant.status}
            data-testid="agent-activity-status"
          >
            {statusLabel}
          </span>
          <button
            type="button"
            className="niuu-chat-agent-close-btn"
            onClick={onClose}
            aria-label="Close agent detail panel"
            data-testid="agent-detail-close"
          >
            <X className="niuu-chat-agent-close-icon" />
          </button>
        </div>
      </div>

      <div className="niuu-chat-agent-events-container" ref={scrollContainerRef}>
        <div className="niuu-chat-agent-events-inner">
          {events.length === 0 ? (
            <div className="niuu-chat-agent-empty" data-testid="agent-detail-empty">
              Waiting for agent activity…
            </div>
          ) : (
            events.map((evt) => <EventItem key={evt.id} event={evt} />)
          )}
          <div ref={messagesEndRef} />
        </div>

        {showScrollBtn && (
          <button
            type="button"
            className="niuu-chat-agent-scroll-btn"
            onClick={() => scrollToBottom('smooth')}
            aria-label="Scroll to bottom"
          >
            <ArrowDownIcon className="niuu-chat-agent-scroll-icon" />
            {newCount > 0 && (
              <span className="niuu-chat-agent-scroll-badge">
                {newCount > 99 ? '99+' : newCount}
              </span>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
