import { X, File, Folder } from 'lucide-react';
import { resolveParticipantColor } from '../../utils/participantColor';
import type { SelectedMention } from '../../hooks/useMentionMenu';
import './MentionPill.css';

interface MentionPillProps {
  mention: SelectedMention;
  onRemove: (id: string) => void;
}

export function MentionPill({ mention, onRemove }: MentionPillProps) {
  if (mention.kind === 'agent') {
    const { participant } = mention;
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
        <span className="niuu-chat-mention-pill-text">{participant.persona}</span>
        <button
          type="button"
          className="niuu-chat-mention-pill-remove"
          onClick={() => onRemove(participant.peerId)}
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
