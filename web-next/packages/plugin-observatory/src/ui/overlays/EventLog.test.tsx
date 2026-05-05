import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EventLog } from './EventLog';
import type { ObservatoryEvent } from '../../domain';

const EVENTS: ObservatoryEvent[] = [
  {
    id: 'ev-1',
    time: '00:00:01',
    type: 'TYR',
    subject: 'tyr-0',
    body: 'raid-omega formed: 2 ravens conscripted',
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
    expect(screen.getByText('raid-omega formed: 2 ravens conscripted')).toBeInTheDocument();
    expect(screen.getByText('inference timeout')).toBeInTheDocument();
  });

  it('renders all event subjects', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.getByText('tyr-0')).toBeInTheDocument();
    expect(screen.getByText('mimir-0')).toBeInTheDocument();
  });

  it('renders time strings (HH:MM:SS)', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.getByText('00:00:01')).toBeInTheDocument();
  });

  it('renders event type tags', () => {
    render(<EventLog events={EVENTS} />);
    expect(screen.getByText('TYR')).toBeInTheDocument();
    expect(screen.getByText('MIMIR')).toBeInTheDocument();
    expect(screen.getByText('BIFROST')).toBeInTheDocument();
    expect(screen.getByText('RAVN')).toBeInTheDocument();
  });

  it('sets data-type attribute on each entry', () => {
    render(<EventLog events={EVENTS} />);
    const tyrEntry = screen.getByTestId('event-ev-1');
    expect(tyrEntry).toHaveAttribute('data-type', 'TYR');
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

  it('renders RAID type events', () => {
    const raidEvent: ObservatoryEvent = {
      id: 'ev-raid',
      time: '00:01:00',
      type: 'RAID',
      subject: 'raid-omega',
      body: 'tyr dispatched raid',
    };
    render(<EventLog events={[raidEvent]} />);
    expect(screen.getByText('RAID')).toBeInTheDocument();
    expect(screen.getByText('raid-omega')).toBeInTheDocument();
  });
});
