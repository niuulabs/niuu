import { ExternalLink } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { resolveParticipantColor } from '../../utils/participantColor';
import { UserMessage, AssistantMessage, StreamingMessage, SystemMessage } from '../ChatMessages';
import type { ChatMessage } from '../../types';
import './RoomMessage.css';

interface RoomMessageProps {
  message: ChatMessage;
  onSelectAgent?: (peerId: string) => void;
  selectedAgentId?: string | null;
  onCopy?: (text: string) => void;
  onRegenerate?: (messageId: string) => void;
  onBookmark?: (messageId: string, bookmarked: boolean) => void;
  bookmarked?: boolean;
  onShowDetail?: (peerId: string) => void;
}

function ParticipantLabel({
  message,
  onSelectAgent,
  selectedAgentId,
  onShowDetail,
}: Pick<RoomMessageProps, 'message' | 'onSelectAgent' | 'selectedAgentId' | 'onShowDetail'>) {
  const { participant } = message;
  if (!participant) return null;

  const color = resolveParticipantColor(participant.peerId, participant.color);
  const isSelected = selectedAgentId === participant.peerId;

  return (
    <div className="niuu-chat-room-label">
      <button
        type="button"
        className={cn(
          'niuu-chat-room-persona-btn',
          isSelected && 'niuu-chat-room-persona-btn--selected',
        )}
        onClick={() => onSelectAgent?.(participant.peerId)}
        style={{ '--niuu-persona-color': color } as React.CSSProperties}
      >
        <span className="niuu-chat-room-persona-dot" />
        <span className="niuu-chat-room-persona-name">{participant.persona}</span>
      </button>
      {onShowDetail && (
        <button
          type="button"
          className="niuu-chat-room-detail-btn"
          onClick={() => onShowDetail(participant.peerId)}
          aria-label={`View event stream for ${participant.persona}`}
        >
          <ExternalLink className="niuu-chat-room-detail-icon" />
        </button>
      )}
    </div>
  );
}

export function RoomMessage({
  message,
  onSelectAgent,
  selectedAgentId,
  onCopy,
  onRegenerate,
  onBookmark,
  bookmarked,
  onShowDetail,
}: RoomMessageProps) {
  return (
    <div className="niuu-chat-room-message" data-testid="room-message">
      <ParticipantLabel
        message={message}
        onSelectAgent={onSelectAgent}
        selectedAgentId={selectedAgentId}
        onShowDetail={onShowDetail}
      />
      {message.metadata?.messageType === 'system' && <SystemMessage message={message} />}
      {message.metadata?.messageType !== 'system' && message.role === 'user' && (
        <UserMessage message={message} />
      )}
      {message.metadata?.messageType !== 'system' &&
        message.role === 'assistant' &&
        message.status === 'running' && (
          <StreamingMessage content={message.content} parts={message.parts} />
        )}
      {message.metadata?.messageType !== 'system' &&
        message.role === 'assistant' &&
        message.status !== 'running' && (
          <AssistantMessage
            message={message}
            onCopy={onCopy}
            onRegenerate={onRegenerate}
            onBookmark={onBookmark}
            bookmarked={bookmarked}
          />
        )}
    </div>
  );
}
