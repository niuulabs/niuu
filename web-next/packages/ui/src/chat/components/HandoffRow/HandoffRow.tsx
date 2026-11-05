import type { CSSProperties } from 'react';
import { ArrowRight } from 'lucide-react';
import type { MeshDelegationEvent } from '../../types';
import type { HandoffParties } from './handoffParties';
import './HandoffRow.css';

export interface HandoffRowProps {
  event: MeshDelegationEvent;
  parties: HandoffParties;
  /** Open a worker's sheet; a handoff with no receiving worker has nothing to open. */
  onOpenWorker: (peerId: string) => void;
}

/** One line: who handed work to whom, what it was, and the event that carried it. */
export function HandoffRow({ event, parties, onOpenWorker }: HandoffRowProps) {
  const { from, targets } = parties;
  const first = targets[0];
  const content = (
    <>
      <span className="niuu-chat-handoff-who">
        <b style={{ color: from.color }}>{from.name}</b>
        {targets.length > 0 && (
          <>
            <ArrowRight className="niuu-chat-handoff-arrow" aria-label="to" />
            {targets.map((target, index) => (
              <span key={target.peerId}>
                {index > 0 && ' · '}
                <b style={{ color: target.color }}>{target.name}</b>
              </span>
            ))}
          </>
        )}
      </span>
      <span className="niuu-chat-handoff-what">{event.preview}</span>
      <span className="niuu-chat-handoff-event">{event.eventType}</span>
    </>
  );

  if (!first) {
    return (
      <div className="niuu-chat-handoff" data-testid="handoff-row">
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      className="niuu-chat-handoff"
      style={{ '--niuu-worker-color': first.color } as CSSProperties}
      data-testid="handoff-row"
      onClick={() => onOpenWorker(first.peerId)}
    >
      {content}
    </button>
  );
}
