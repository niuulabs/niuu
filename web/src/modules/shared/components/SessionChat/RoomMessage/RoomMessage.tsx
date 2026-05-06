import { Eye } from 'lucide-react';
import { cn } from '@/utils';
import type { SkuldChatMessage, ParticipantMeta } from '@/modules/shared/hooks/useSkuldChat';
import { resolveParticipantColor } from '@/modules/shared/utils/participantColor';
import { UserMessage, AssistantMessage, StreamingMessage, SystemMessage } from '../ChatMessages';
import styles from './RoomMessage.module.css';

interface RoomMessageProps {
  message: SkuldChatMessage;
  /** Called when the user clicks the participant label or the detail button */
  onSelectAgent?: (peerId: string) => void;
  /** The peerId of the currently selected agent (for active styling) */
  selectedAgentId?: string | null;
  onCopy?: (text: string) => void;
  onRegenerate?: (messageId: string) => void;
  onBookmark?: (messageId: string, bookmarked: boolean) => void;
  bookmarked?: boolean;
}

interface ParticipantLabelProps {
  participant: ParticipantMeta;
  onSelectAgent?: (peerId: string) => void;
  isSelected?: boolean;
}

function ParticipantLabel({ participant, onSelectAgent, isSelected }: ParticipantLabelProps) {
  const color = resolveParticipantColor(participant.color);
  const isRavn = participant.participantType === 'ravn';
  const canSelect = isRavn && participant.gatewayUrl && onSelectAgent;

  const handleClick = () => {
    onSelectAgent!(participant.peerId);
  };

  return (
    <div className={styles.participantRow}>
      <button
        type="button"
        className={cn(styles.participantLabel, canSelect && styles.participantLabelClickable)}
        style={{ '--participant-color': color } as React.CSSProperties}
        onClick={canSelect ? handleClick : undefined}
        title={
          canSelect
            ? `View ${participant.displayName || participant.persona} details`
            : participant.displayName || participant.persona
        }
        data-selected={isSelected || undefined}
        data-testid="participant-label"
      >
        <span className={styles.participantDot} />
        <span className={styles.participantName}>
          {participant.displayName
            ? `${participant.displayName} (${participant.persona})`
            : participant.persona}
        </span>
      </button>

      {canSelect && (
        <button
          type="button"
          className={cn(styles.detailBtn, isSelected && styles.detailBtnActive)}
          onClick={handleClick}
          title={`View ${participant.displayName || participant.persona} event stream`}
          aria-label={`View ${participant.displayName || participant.persona} details`}
          data-testid="view-agent-detail-btn"
        >
          <Eye className={styles.detailIcon} />
        </button>
      )}
    </div>
  );
}

/**
 * Wraps an existing message component with a participant label for room view.
 *
 * Only renders the label when the message carries participant metadata and the
 * participant is a Ravn agent. Non-participant messages fall through to the
 * standard message components directly.
 */
export function RoomMessage({
  message,
  onSelectAgent,
  selectedAgentId,
  onCopy,
  onRegenerate,
  onBookmark,
  bookmarked = false,
}: RoomMessageProps) {
  const participant = message.participant;
  const isSelectedAgent = participant ? selectedAgentId === participant.peerId : false;

  const label =
    participant?.participantType === 'ravn' ? (
      <ParticipantLabel
        participant={participant}
        onSelectAgent={onSelectAgent}
        isSelected={isSelectedAgent}
      />
    ) : null;

  // System messages are always rendered without participant framing
  if (message.metadata?.messageType === 'system') {
    return <SystemMessage message={message} />;
  }

  if (message.role === 'user') {
    return (
      <div className={styles.roomMessageWrapper}>
        {label}
        <UserMessage message={message} />
      </div>
    );
  }

  if (message.status === 'running') {
    return (
      <div className={styles.roomMessageWrapper}>
        {label}
        <StreamingMessage content={message.content} parts={message.parts} />
      </div>
    );
  }

  return (
    <div className={styles.roomMessageWrapper}>
      {label}
      <AssistantMessage
        message={message}
        onCopy={onCopy}
        onRegenerate={onRegenerate}
        onBookmark={onBookmark}
        bookmarked={bookmarked}
      />
    </div>
  );
}
