/**
 * EventsView — produces / consumes graph.
 *
 * Derives the event catalog from personas.
 * Layout: event nodes centred, producer persona nodes on left, consumer persona nodes on right.
 * Click a persona node to highlight its events; click elsewhere to reset.
 */

import { useState, useMemo } from 'react';
import { StateDot } from '@niuulabs/ui';
import { usePersonas } from './usePersonas';
import type { PersonaSummary } from '../ports';

interface EventNode {
  name: string;
  producers: string[];
  consumers: string[];
}

function buildEventGraph(personas: PersonaSummary[]): EventNode[] {
  const map = new Map<string, EventNode>();

  const ensure = (name: string) => {
    if (!map.has(name)) map.set(name, { name, producers: [], consumers: [] });
    return map.get(name)!;
  };

  for (const p of personas) {
    if (p.producesEvent) ensure(p.producesEvent).producers.push(p.name);
    for (const e of p.consumesEvents) ensure(e).consumers.push(p.name);
  }

  return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
}

interface EventCardProps {
  event: EventNode;
  dimmed: boolean;
  selectedPersona: string | null;
  onPersonaClick: (name: string) => void;
}

function PersonaPill({
  name,
  role,
  selected,
  onClick,
}: {
  name: string;
  role: 'producer' | 'consumer';
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`rv-event-persona${selected ? ' rv-event-persona--selected' : ''}`}
      data-role={role}
      onClick={onClick}
      aria-pressed={selected}
    >
      {name}
    </button>
  );
}

function EventCard({ event, dimmed, selectedPersona, onPersonaClick }: EventCardProps) {
  const isHighlighted =
    selectedPersona === null ||
    event.producers.includes(selectedPersona) ||
    event.consumers.includes(selectedPersona);

  return (
    <article
      className={`rv-event-card${dimmed && !isHighlighted ? ' rv-event-card--dimmed' : ''}`}
      aria-label={`event ${event.name}`}
    >
      <div className="rv-event-card__name">{event.name}</div>
      <div className="rv-event-card__edges">
        {event.producers.length > 0 && (
          <div className="rv-event-card__edge-group rv-event-card__edge-group--produces">
            <span className="rv-event-card__edge-label">produces</span>
            <div className="rv-event-card__personas">
              {event.producers.map((name) => (
                <PersonaPill
                  key={name}
                  name={name}
                  role="producer"
                  selected={selectedPersona === name}
                  onClick={() => onPersonaClick(name)}
                />
              ))}
            </div>
          </div>
        )}
        {event.consumers.length > 0 && (
          <div className="rv-event-card__edge-group rv-event-card__edge-group--consumes">
            <span className="rv-event-card__edge-label">consumes</span>
            <div className="rv-event-card__personas">
              {event.consumers.map((name) => (
                <PersonaPill
                  key={name}
                  name={name}
                  role="consumer"
                  selected={selectedPersona === name}
                  onClick={() => onPersonaClick(name)}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

export function EventsView() {
  const { data: personas, isLoading, isError } = usePersonas();
  const [selectedPersona, setSelectedPersona] = useState<string | null>(null);

  const events = useMemo(() => buildEventGraph(personas ?? []), [personas]);

  const handlePersonaClick = (name: string) => {
    setSelectedPersona((prev) => (prev === name ? null : name));
  };

  if (isLoading) {
    return (
      <div className="rv-events-view">
        <div className="rv-events-view__loading">
          <StateDot state="processing" pulse />
          <span>loading event catalog…</span>
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rv-events-view">
        <div className="rv-events-view__error">failed to load personas</div>
      </div>
    );
  }

  return (
    <div
      className="rv-events-view"
      onClick={(e) => {
        if ((e.target as HTMLElement).closest('.rv-event-persona') === null) {
          setSelectedPersona(null);
        }
      }}
    >
      <div className="rv-events-view__header">
        <span className="rv-events-view__count">
          <strong>{events.length}</strong> event{events.length !== 1 ? 's' : ''}
        </span>
        {selectedPersona && (
          <span className="rv-events-view__persona-filter">
            filtering by <strong>{selectedPersona}</strong>
            <button
              type="button"
              className="rv-events-view__clear"
              onClick={() => setSelectedPersona(null)}
              aria-label="clear persona filter"
            >
              ×
            </button>
          </span>
        )}
      </div>

      {events.length === 0 ? (
        <div className="rv-events-view__empty">no events defined</div>
      ) : (
        <div className="rv-events-view__graph" role="region" aria-label="event graph">
          {events.map((event) => (
            <EventCard
              key={event.name}
              event={event}
              dimmed={selectedPersona !== null}
              selectedPersona={selectedPersona}
              onPersonaClick={handlePersonaClick}
            />
          ))}
        </div>
      )}
    </div>
  );
}
