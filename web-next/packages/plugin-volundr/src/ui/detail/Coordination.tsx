import type { Session } from '../../domain/session';
import './detail.css';

function sessionLabel(session: Session): string {
  return session.name || session.personaName || session.id;
}

/** Marks a session that leads others. */
export function CoordinatorTag() {
  return (
    <span className="forge-coordinator-tag" data-testid="session-coordinator-tag">
      Coordinator
    </span>
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
