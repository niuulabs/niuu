import type { CSSProperties } from 'react';
import { cn } from '../../../utils/cn';
import type { Worker, WorkerStatus } from '../../workers';
import './WorkersStrip.css';

export const WORKER_STATUS_LABELS: Record<WorkerStatus, string> = {
  working: 'Working',
  idle: 'Idle',
  error: 'Error',
};

/** The one letter a worker's avatar shows. */
export function workerInitial(worker: Pick<Worker, 'name'>): string {
  return worker.name.charAt(0).toUpperCase();
}

export interface WorkersStripProps {
  workers: readonly Worker[];
  /** The worker whose sheet is open. */
  activePeerId?: string | null;
  onSelect: (peerId: string) => void;
}

/** Who is working under the coordinator: one chip per worker, opening that worker's sheet. */
export function WorkersStrip({ workers, activePeerId = null, onSelect }: WorkersStripProps) {
  return (
    <div
      className="niuu-chat-workers"
      role="group"
      aria-label="Workers"
      data-testid="workers-strip"
    >
      <span className="niuu-chat-workers-label">Workers</span>
      {workers.map((worker) => (
        <button
          key={worker.peerId}
          type="button"
          className={cn('niuu-chat-worker-chip', activePeerId === worker.peerId && 'is-active')}
          style={{ '--niuu-worker-color': worker.color } as CSSProperties}
          aria-pressed={activePeerId === worker.peerId}
          onClick={() => onSelect(worker.peerId)}
        >
          <span className="niuu-chat-worker-avatar" aria-hidden="true">
            {workerInitial(worker)}
          </span>
          <span className="niuu-chat-worker-name">{worker.name}</span>
          <span
            className={cn('niuu-chat-worker-status', `niuu-chat-worker-status--${worker.status}`)}
            role="img"
            aria-label={WORKER_STATUS_LABELS[worker.status]}
            data-status={worker.status}
          />
        </button>
      ))}
    </div>
  );
}
