import { ChevronRight, ChevronDown } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { resolveParticipantColor } from '../../utils/participantColor';
import type { ChatMessage } from '../../types';
import { UserMessage, AssistantMessage, StreamingMessage, SystemMessage } from '../ChatMessages';
import './ThreadGroup.css';

const formatTime = (date: Date): string =>
  date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

function buildTimeRange(messages: readonly ChatMessage[]): string {
  if (messages.length === 0) return '';
  const firstMsg = messages[0];
  if (!firstMsg) return '';
  if (messages.length === 1) return formatTime(firstMsg.createdAt);
  const first = formatTime(firstMsg.createdAt);
  const lastMsg = messages[messages.length - 1];
  const last = lastMsg ? formatTime(lastMsg.createdAt) : '';
  return `${first} – ${last}`;
}

interface ThreadGroupProps {
  messages: readonly ChatMessage[];
  isCollapsed: boolean;
  onToggle: () => void;
}

export function ThreadGroup({ messages, isCollapsed, onToggle }: ThreadGroupProps) {
  const firstMsg = messages[0];
  const color = firstMsg?.participant
    ? resolveParticipantColor(firstMsg.participant.peerId, firstMsg.participant.color)
    : undefined;
  const timeRange = buildTimeRange(messages);
  const persona = firstMsg?.participant?.persona ?? 'Internal';

  return (
    <div
      className="niuu-chat-thread-group"
      style={color ? ({ '--niuu-thread-color': color } as React.CSSProperties) : undefined}
      data-testid="thread-group"
    >
      <button
        type="button"
        className="niuu-chat-thread-header"
        onClick={onToggle}
        aria-expanded={!isCollapsed}
      >
        {isCollapsed ? (
          <ChevronRight className="niuu-chat-thread-chevron" />
        ) : (
          <ChevronDown className="niuu-chat-thread-chevron" />
        )}
        <span className="niuu-chat-thread-label">
          {persona} · {messages.length} msg{messages.length !== 1 ? 's' : ''}
        </span>
        {timeRange && <span className="niuu-chat-thread-time">{timeRange}</span>}
      </button>

      <div
        className={cn('niuu-chat-thread-body', isCollapsed && 'niuu-chat-thread-body--collapsed')}
      >
        {messages.map((msg) => {
          if (msg.metadata?.messageType === 'system')
            return <SystemMessage key={msg.id} message={msg} />;
          if (msg.role === 'user') return <UserMessage key={msg.id} message={msg} />;
          if (msg.status === 'running')
            return <StreamingMessage key={msg.id} content={msg.content} parts={msg.parts} />;
          return <AssistantMessage key={msg.id} message={msg} />;
        })}
      </div>
    </div>
  );
}
