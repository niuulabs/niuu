import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EventsTab } from './EventsTab';

describe('EventsTab', () => {
  it('renders its empty state', () => {
    render(<EventsTab events={[]} />);
    expect(screen.getByTestId('events-empty')).toHaveTextContent('No events recorded.');
  });

  it('renders chronological event rows', () => {
    render(
      <EventsTab
        events={[
          { ts: '2026-07-11T12:00:00Z', kind: 'session.started', body: 'Session started' },
          { ts: '2026-07-11T12:01:00Z', kind: 'tool.called', body: 'Tool called' },
        ]}
      />,
    );
    expect(screen.getAllByTestId('event-row')).toHaveLength(2);
    expect(screen.getByRole('list', { name: 'Session events' })).toBeInTheDocument();
    expect(screen.getByText('session.started')).toBeInTheDocument();
    expect(screen.getByText('Tool called')).toBeInTheDocument();
  });
});
