import { useState, useEffect } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import type { IEventStream } from '../ports';
import type { ObservatoryEvent } from '../domain';

const MAX_EVENTS = 100;

export function useEvents(): ObservatoryEvent[] {
  const stream = useService<IEventStream>('observatory.events');
  const [events, setEvents] = useState<ObservatoryEvent[]>([]);

  useEffect(() => {
    return stream.subscribe((event) => {
      setEvents((prev) => {
        // A retraction means the condition cleared: drop the entry rather
        // than adding one. Without this a warning stayed on the log until a
        // hundred newer events pushed it off, so a fault fixed an hour ago
        // was indistinguishable from one still happening.
        if (event.resolved) {
          const remaining = prev.filter((e) => e.id !== event.id);
          return remaining.length === prev.length ? prev : remaining;
        }
        if (prev.some((e) => e.id === event.id)) return prev;
        const next = [...prev, event];
        return next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next;
      });
    });
  }, [stream]);

  return events;
}
