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
        if (prev.some((e) => e.id === event.id)) return prev;
        const next = [...prev, event];
        return next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next;
      });
    });
  }, [stream]);

  return events;
}
