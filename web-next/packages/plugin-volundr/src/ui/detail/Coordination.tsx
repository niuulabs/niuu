import type { Session } from '../../domain/session';
import { stateTone } from './sessionHeaderModel';
import { SESSION_STATE_LABELS } from '../sessions/presentation';
import { sessionTimeFact, useMinuteClock } from '../sessions/timeFact';
import './detail.css';

function sessionLabel(session: Session): string {
  return session.name || session.personaName || session.id;
}

/** Marks a session that leads others. */
export function CoordinatorTag() {
  return (
    <span className="forge-coordinator-tag" data-testid="session-coordinator-tag">
      COORDINATOR
    </span>
  );
}

/** The header's strip of the sessions a coordinator leads; each opens its session. */
export function WorkersStrip({
  workers,
  onOpen,
}: {
  workers: readonly Session[];
  onOpen: (id: string) => void;
}) {
  const now = useMinuteClock();
  if (workers.length === 0) return null;
  return (
    <div
      className="forge-workers-strip"
      role="group"
      aria-label="Workers"
      data-testid="workers-strip"
    >
      <span className="forge-workers-strip__label">Workers</span>
      {workers.map((worker) => {
        const fact = sessionTimeFact(worker, now);
        return (
          <button
            key={worker.id}
            type="button"
            className="forge-workers-strip__chip"
            data-tone={stateTone(worker.state)}
            title={`${sessionLabel(worker)}: ${SESSION_STATE_LABELS[worker.state]}`}
            aria-label={`Open worker ${sessionLabel(worker)}, ${SESSION_STATE_LABELS[worker.state]}`}
            data-testid={`worker-chip-${worker.id}`}
            onClick={() => onOpen(worker.id)}
          >
            <span className="forge-workers-strip__dot" aria-hidden="true" />
            <span className="forge-workers-strip__name">{sessionLabel(worker)}</span>
            {fact ? <span className="forge-workers-strip__time">{fact}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A worker names the coordinator that leads it, with a link back. When the list does not
 * know the coordinator (its host is unreachable) the line says only that this is a worker.
 */
export function WorkerOf({
  coordinator,
  onOpen,
}: {
  coordinator: Session | null;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="forge-worker-of" data-testid="worker-of">
      {coordinator ? (
        <>
          Worker of{' '}
          <button
            type="button"
            className="forge-worker-of__link"
            aria-label={`Open coordinator ${sessionLabel(coordinator)}`}
            onClick={() => onOpen(coordinator.id)}
          >
            {sessionLabel(coordinator)}
          </button>
        </>
      ) : (
        'Worker'
      )}
    </div>
  );
}
