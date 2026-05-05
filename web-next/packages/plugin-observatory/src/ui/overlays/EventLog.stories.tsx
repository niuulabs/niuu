import { useState, useEffect } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { EventLog } from './EventLog';
import { createMockEventStream } from '../../adapters/mock';
import type { ObservatoryEvent } from '../../domain';

const meta: Meta<typeof EventLog> = {
  title: 'Observatory/Overlays/EventLog',
  component: EventLog,
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof EventLog>;

export const Empty: Story = {
  render: () => <EventLog events={[]} />,
};

export const WithSeedEvents: Story = {
  render: () => {
    const events: ObservatoryEvent[] = [];
    createMockEventStream().subscribe((ev) => events.push(ev));
    return <EventLog events={events} />;
  },
};

function LiveStreamDemo() {
  const [events, setEvents] = useState<ObservatoryEvent[]>([]);

  useEffect(() => {
    const stream = createMockEventStream();
    return stream.subscribe((ev) => {
      setEvents((prev) => [...prev, ev]);
    });
  }, []);

  return (
    <div style={{ padding: 'var(--space-6)' }}>
      <p style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--text-sm)' }}>
        Event log overlay (fixed bottom)
      </p>
      <EventLog events={events} />
    </div>
  );
}

export const LiveStream: Story = {
  render: () => <LiveStreamDemo />,
};

export const AllSeverities: Story = {
  render: () => {
    const events: ObservatoryEvent[] = [
      {
        id: '1',
        timestamp: '2026-04-19T00:00:01Z',
        severity: 'debug',
        sourceId: 'svc-a',
        message: 'debug message',
      },
      {
        id: '2',
        timestamp: '2026-04-19T00:00:02Z',
        severity: 'info',
        sourceId: 'svc-b',
        message: 'informational message',
      },
      {
        id: '3',
        timestamp: '2026-04-19T00:00:03Z',
        severity: 'warn',
        sourceId: 'svc-c',
        message: 'warning: queue depth rising',
      },
      {
        id: '4',
        timestamp: '2026-04-19T00:00:04Z',
        severity: 'error',
        sourceId: 'svc-d',
        message: 'error: inference timeout',
      },
    ];
    return <EventLog events={events} />;
  },
};
