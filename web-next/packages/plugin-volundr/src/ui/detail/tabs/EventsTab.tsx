import type { SessionEvent } from '../../../domain/session';

interface EventsTabProps {
  events: SessionEvent[];
}

/** Events tab — chronological session lifecycle events. */
export function EventsTab({ events }: EventsTabProps) {
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-2 niuu-p-4" data-testid="events-tab">
      {events.length === 0 ? (
        <p
          className="niuu-py-8 niuu-text-center niuu-text-sm niuu-text-text-muted"
          data-testid="events-empty"
        >
          No events recorded.
        </p>
      ) : (
        <ol className="niuu-flex niuu-flex-col niuu-gap-2" aria-label="Session events">
          {events.map((ev, idx) => (
            <li
              key={`${ev.ts}-${idx}`}
              className="niuu-flex niuu-items-start niuu-gap-3 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-3"
              data-testid="event-row"
            >
              <div className="niuu-flex niuu-flex-col niuu-items-start niuu-gap-0.5">
                <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
                  {new Date(ev.ts).toLocaleTimeString()}
                </span>
                <span className="niuu-rounded niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-xs niuu-text-brand">
                  {ev.kind}
                </span>
              </div>
              <p className="niuu-flex-1 niuu-text-sm niuu-text-text-primary">{ev.body}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
