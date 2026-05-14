import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EventLog } from './EventLog';
import type { ObservatoryEvent } from '../../domain';

const EVENTS: ObservatoryEvent[] = [
  {
    id: 'ev-1',
    time: '00:00:01',
    type: 'TING',
    subject: 'ting-0',
    body: 'run-omega formed: 2 ravens conscripted',
  },
  {
    id: 'ev-2',
    time: '00:00:05',
    type: 'MIMIR',
    subject: 'mimir-0',
    body: 'write queue depth nearing threshold',
  },
  {
    id: 'ev-3',
    time: '00:00:10',
    type: 'BIFROST',
    subject: 'bifrost-0',
    body: 'inference timeout',
  },
  {
    id: 'ev-4',
    time: '00:00:15',
    type: 'RAVN',
    subject: 'huginn',
    body: 'cache hit 94%',
  },
];

describe('EventLog', () => {
  it('renders empty state message when no events', () => {
    render(<EventLog events={[]} />);
    expect(screen.getByText('no events')).toBeInTheDocument();
  });

  it('renders all event body text', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.getByText('run-omega formed: 2 ravens conscripted')).toBeInTheDocument();
    expect(screen.getByText('inference timeout')).toBeInTheDocument();
  });

  it('renders all event subjects', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.getByText('ting-0')).toBeInTheDocument();
    expect(screen.getByText('mimir-0')).toBeInTheDocument();
  });

  it('renders time strings (HH:MM:SS)', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.getByText('00:00:01')).toBeInTheDocument();
  });

  it('renders event type tags', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.getByText('TING')).toBeInTheDocument();
    expect(screen.getByText('MIMIR')).toBeInTheDocument();
    expect(screen.getByText('BIFROST')).toBeInTheDocument();
    expect(screen.getByText('RAVN')).toBeInTheDocument();
  });

  it('sets data-type attribute on each entry', () => {
    render(<EventLog events={EVENTS} />);
    const tingEntry = screen.getByTestId('event-ev-1');
    expect(tingEntry).toHaveAttribute('data-type', 'TING');
    const mimirEntry = screen.getByTestId('event-ev-2');
    expect(mimirEntry).toHaveAttribute('data-type', 'MIMIR');
  });

  it('accepts custom data-testid', () => {
    render(<EventLog events={[]} data-testid="my-log" />);
    expect(screen.getByTestId('my-log')).toBeInTheDocument();
  });

  it('renders event entries in order', () => {
    render(<EventLog events={EVENTS} />);
    const entries = screen.getAllByTestId(/^event-ev/);
    expect(entries[0]).toHaveAttribute('data-testid', 'event-ev-1');
    expect(entries[3]).toHaveAttribute('data-testid', 'event-ev-4');
  });

  it('does not render "no events" when events are present', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.queryByText('no events')).toBeNull();
  });

  it('renders RUN type events', () => {
    const runEvent: ObservatoryEvent = {
      id: 'ev-run',
      time: '00:01:00',
      type: 'RUN',
      subject: 'run-omega',
      body: 'ting dispatched run',
    };
    render(<EventLog events={[runEvent]} />);
    expect(screen.getByText('RUN')).toBeInTheDocument();
    expect(screen.getByText('run-omega')).toBeInTheDocument();
  });
});
