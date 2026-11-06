import type { CSSProperties } from 'react';
import { AlertTriangle } from 'lucide-react';
import type { MeshNotificationEvent } from '../../types';
import './AttentionRow.css';

export interface AttentionRowProps {
  event: MeshNotificationEvent;
  /** The worker it came from, when the room lists one; its sheet is what the row opens. */
  worker?: { peerId: string; name: string; color: string };
  onOpenWorker: (peerId: string) => void;
}

function kindLabel(notificationType: string): string {
  const words = notificationType.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * A worker raising something for attention (`help_needed`, a warning). One compact line:
 * the page raises the question itself where the user can answer it, so this only marks where
 * in the conversation it happened.
 */
export function AttentionRow({ event, worker, onOpenWorker }: AttentionRowProps) {
  const name = worker?.name ?? event.persona;
  const content = (
    <>
      <AlertTriangle className="niuu-chat-attention-icon" aria-hidden="true" />
      <b style={worker ? { color: worker.color } : undefined}>{name}</b>
      <span className="niuu-chat-attention-summary">{event.summary}</span>
      <span className="niuu-chat-attention-kind">{kindLabel(event.notificationType)}</span>
    </>
  );

  if (!worker) {
    return (
      <div className="niuu-chat-attention" data-testid="attention-row">
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      className="niuu-chat-attention"
      style={{ '--niuu-worker-color': worker.color } as CSSProperties}
      data-testid="attention-row"
      onClick={() => onOpenWorker(worker.peerId)}
    >
      {content}
    </button>
  );
}
