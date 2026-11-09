import type { Worker } from '@niuulabs/ui';
import type { Session } from '../../domain/session';
import './detail.css';

function sessionLabel(session: Session): string {
  return session.name || session.personaName || session.id;
}

/** Marks a session that leads others. */
/** A session that leads others: a coordinator, or named by its role when it only nests them. */
export function CoordinatorTag({ label = 'Coordinator' }: { label?: string }) {
  return (
    <span className="forge-coordinator-tag" data-testid="session-coordinator-tag">
      {label}
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
  coordinated = true,
}: {
  coordinator: Session | null;
  onOpen: (id: string) => void;
  /** False for a session only nested under its parent (a workflow run's member). */
  coordinated?: boolean;
}) {
  const relation = coordinated ? 'Worker of' : 'Nested under';
  return (
    <div className="forge-worker-of" data-testid="worker-of">
      {coordinator ? (
        <>
          {relation}{' '}
          <button
            type="button"
            className="forge-worker-of__link"
            aria-label={`Open ${coordinated ? 'coordinator' : 'parent session'} ${sessionLabel(coordinator)}`}
            onClick={() => onOpen(coordinator.id)}
          >
            {sessionLabel(coordinator)}
          </button>
        </>
      ) : coordinated ? (
        'Worker'
      ) : (
        'Nested'
      )}
    </div>
  );
}

/** What a room worker listens for and emits, shown with the session's facts when it is focused. */
export function WorkerTopics({ worker }: { worker: Pick<Worker, 'name' | 'consumes' | 'emits'> }) {
  if (worker.consumes.length === 0 && worker.emits.length === 0) return null;
  return (
    <div className="forge-worker-topics" data-testid="worker-topics">
      <span className="forge-worker-topics__who">{worker.name}</span>
      {worker.consumes.length > 0 && (
        <span className="forge-worker-topics__group">
          Listens for
          {worker.consumes.map((type) => (
            <code key={type}>{type}</code>
          ))}
        </span>
      )}
      {worker.emits.length > 0 && (
        <span className="forge-worker-topics__group">
          Emits
          {worker.emits.map((type) => (
            <code key={type}>{type}</code>
          ))}
        </span>
      )}
    </div>
  );
}
