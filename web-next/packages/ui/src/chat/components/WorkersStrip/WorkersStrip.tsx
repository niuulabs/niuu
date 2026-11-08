import type { CSSProperties } from 'react';
import { cn } from '../../../utils/cn';
import type { Worker, WorkerStatus } from '../../workers';
import {
  sessionOpenHandler,
  useSessionLinks,
  type SessionLink,
  type SessionLinkTone,
} from '../../sessionLinks';
import './WorkersStrip.css';

export const WORKER_STATUS_LABELS: Record<WorkerStatus, string> = {
  working: 'Working',
  idle: 'Idle',
  error: 'Error',
  left: 'Left',
};

/** The one letter a worker's avatar shows. */
export function workerInitial(worker: Pick<Worker, 'name'>): string {
  return worker.name.charAt(0).toUpperCase();
}

const SESSION_STATUS: Record<SessionLinkTone, WorkerStatus | 'waiting'> = {
  ok: 'working',
  warn: 'waiting',
  critical: 'error',
  muted: 'idle',
};

export interface WorkersStripProps {
  workers: readonly Worker[];
  /** Worker sessions the coordinator started; each chip opens that session. */
  sessions?: readonly SessionLink[];
  /** The worker whose sheet is open. */
  activePeerId?: string | null;
  onSelect: (peerId: string) => void;
}

/**
 * Who is working under the coordinator: its worker sessions, each opening that session,
 * then the agents in its room, each opening that worker's sheet.
 */
export function WorkersStrip({
  workers,
  sessions = [],
  activePeerId = null,
  onSelect,
}: WorkersStripProps) {
  const links = useSessionLinks();
  return (
    <div
      className="niuu-chat-workers"
      role="group"
      aria-label="Workers"
      data-testid="workers-strip"
    >
      <span className="niuu-chat-workers-label">Workers</span>
      {links &&
        sessions.map((session) => (
          <a
            key={session.id}
            href={session.href}
            className="niuu-chat-worker-chip niuu-chat-worker-chip--session"
            title={`${session.name}: ${session.stateLabel}`}
            aria-label={`Open worker ${session.name}, ${session.stateLabel}`}
            data-testid={`worker-chip-${session.id}`}
            onClick={sessionOpenHandler(links, session)}
          >
            <span
              className={cn(
                'niuu-chat-worker-status',
                `niuu-chat-worker-status--${SESSION_STATUS[session.tone]}`,
              )}
              aria-hidden="true"
            />
            <span className="niuu-chat-worker-name">{session.name}</span>
            <span className="niuu-chat-worker-state">{session.stateLabel}</span>
          </a>
        ))}
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
