import { X, File, Folder, Radio } from 'lucide-react';
import { resolveParticipantColor } from '../../utils/participantColor';
import { mentionId } from '../../hooks/useMentionMenu';
import type { SelectedMention } from '../../hooks/useMentionMenu';
import './MentionPill.css';

interface MentionPillProps {
  mention: SelectedMention;
  onRemove: (id: string) => void;
}

export function MentionPill({ mention, onRemove }: MentionPillProps) {
  if (mention.kind === 'agent') {
    const { participant } = mention;
    const participantName = participant.displayName || participant.persona;
    if (mention.eventType) {
      return (
        <span
          className="niuu-chat-mention-pill niuu-chat-mention-pill--event"
          data-testid="mention-pill-event"
        >
          <Radio className="niuu-chat-mention-pill-event-icon" aria-hidden="true" />
          <span className="niuu-chat-mention-pill-text">
            {mention.eventType} · {participantName}
          </span>
          <button
            type="button"
            className="niuu-chat-mention-pill-remove"
            onClick={() => onRemove(mentionId(mention))}
            aria-label={`Remove event ${mention.eventType}`}
          >
            <X className="niuu-chat-mention-pill-remove-icon" />
          </button>
        </span>
      );
    }
    const color = resolveParticipantColor(participant.peerId, participant.color);
    return (
      <span
        className="niuu-chat-mention-pill niuu-chat-mention-pill--agent"
        data-testid="mention-pill-agent"
      >
        <span
          className="niuu-chat-mention-pill-dot"
          style={{ backgroundColor: color }}
          aria-hidden="true"
        />
        <span className="niuu-chat-mention-pill-text">{participantName}</span>
        <button
          type="button"
          className="niuu-chat-mention-pill-remove"
          onClick={() => onRemove(mentionId(mention))}
          aria-label={`Remove mention of ${participant.persona}`}
        >
          <X className="niuu-chat-mention-pill-remove-icon" />
        </button>
      </span>
    );
  }

  const { entry } = mention;
  const Icon = entry.type === 'directory' ? Folder : File;
  return (
    <span
      className="niuu-chat-mention-pill niuu-chat-mention-pill--file"
      data-testid="mention-pill-file"
    >
      <Icon className="niuu-chat-mention-pill-file-icon" aria-hidden="true" />
      <span className="niuu-chat-mention-pill-text niuu-chat-mention-pill-text--path">
        {entry.path}
      </span>
      <button
        type="button"
        className="niuu-chat-mention-pill-remove"
        onClick={() => onRemove(entry.path)}
        aria-label={`Remove mention of ${entry.path}`}
      >
        <X className="niuu-chat-mention-pill-remove-icon" />
      </button>
    </span>
  );
}
