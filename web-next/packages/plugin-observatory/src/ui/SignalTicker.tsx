import type { ObservatoryEvent } from '../domain';
import './SignalTicker.css';

interface Props {
  events: ObservatoryEvent[];
  /** Messages per minute, or null when no rate is known yet. */
  rate?: number | null;
}

/**
 * Signal ticker — the newest events, most recent first.
 *
 * Warnings keep their level visible: a discovery adapter that cannot reach a
 * service is the difference between "nothing is there" and "we cannot see",
 * and that distinction belongs on screen.
 */
export function SignalTicker({ events, rate = null }: Props) {
  const rows = [...events].slice(-40).reverse();
  return (
    <section className="obs-ticker" data-testid="signal-ticker" aria-label="Signal">
      <div className="obs-ticker__bar">
        <span>Signal</span>
        <span className="obs-ticker__rate">{rate === null ? '—' : `${rate} msg/min`}</span>
      </div>
      <div className="obs-ticker__feed">
        {rows.length === 0 ? (
          <p className="obs-ticker__empty">No signal yet.</p>
        ) : (
          rows.map((event) => (
            <div
              key={event.id}
              className={`obs-ticker__row${
                event.level === 'warning' ? ' obs-ticker__row--warn' : ''
              }`}
              data-testid={`signal-${event.id}`}
            >
              <span className="obs-ticker__time">{event.time}</span>
              <span className="obs-ticker__src">{event.subject}</span>
              <span className="obs-ticker__msg">{event.body}</span>
              {event.type ? <span className="obs-ticker__tag">{event.type}</span> : null}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
