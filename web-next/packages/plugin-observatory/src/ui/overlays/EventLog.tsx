import { useRef, useEffect } from 'react';
import type { ObservatoryEvent, ObservatoryEventType } from '../../domain';
import './EventLog.css';

/** Short display tag for each event type (matches web2 column). */
const TYPE_CLASS: Record<ObservatoryEventType, string> = {
  RAID: 'obs-event-log__type--raid',
  RAVN: 'obs-event-log__type--ravn',
  TYR: 'obs-event-log__type--tyr',
  MIMIR: 'obs-event-log__type--mimir',
  BIFROST: 'obs-event-log__type--bifrost',
};

export interface EventLogProps {
  events: ObservatoryEvent[];
  'data-testid'?: string;
}

export function EventLog({ events, 'data-testid': testId }: EventLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [events]);

  return (
    // pointer-events: none lets canvas clicks pass through the wrapper (set in EventLog.css)
    <div className="obs-event-log" data-testid={testId ?? 'event-log'}>
      <div className="obs-event-log__inner" ref={scrollRef}>
        <div className="obs-event-log__header">Event stream</div>
        {events.length === 0 ? (
          <span className="obs-event-log__empty">no events</span>
        ) : (
          events.map((ev) => (
            <div
              key={ev.id}
              className="obs-event-log__entry"
              data-type={ev.type}
              data-testid={`event-${ev.id}`}
            >
              <span className="obs-event-log__ts">{ev.time}</span>
              <span className={`obs-event-log__type ${TYPE_CLASS[ev.type] ?? ''}`}>{ev.type}</span>
              <span className="obs-event-log__subject">{ev.subject}</span>
              <span className="obs-event-log__body">{ev.body}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
