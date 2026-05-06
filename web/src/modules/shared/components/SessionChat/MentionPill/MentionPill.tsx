import { File, Folder, X } from 'lucide-react';
import type { SelectedMention } from '../useMentionMenu';
import styles from './MentionPill.module.css';

interface MentionPillProps {
  mention: SelectedMention;
  onRemove: (id: string) => void;
}

export function MentionPill({ mention, onRemove }: MentionPillProps) {
  if (mention.kind === 'agent') {
    const { participant } = mention;
    return (
      <span
        className={styles.pill}
        data-kind="agent"
        data-testid="mention-pill"
        style={{ '--pill-color': participant.color } as React.CSSProperties}
      >
        <span className={styles.agentDot} />
        <span className={styles.path}>{participant.persona}</span>
        <button
          type="button"
          className={styles.remove}
          onClick={() => onRemove(participant.peerId)}
          aria-label={`Remove @${participant.persona}`}
        >
          <X className={styles.removeIcon} />
        </button>
      </span>
    );
  }

  const { entry } = mention;
  return (
    <span
      className={styles.pill}
      data-kind="file"
      data-type={entry.type}
      data-testid="mention-pill"
    >
      {entry.type === 'directory' ? (
        <Folder className={styles.icon} />
      ) : (
        <File className={styles.icon} />
      )}
      <span className={styles.path}>{entry.path}</span>
      <button
        type="button"
        className={styles.remove}
        onClick={() => onRemove(entry.path)}
        aria-label={`Remove ${entry.path}`}
      >
        <X className={styles.removeIcon} />
      </button>
    </span>
  );
}
