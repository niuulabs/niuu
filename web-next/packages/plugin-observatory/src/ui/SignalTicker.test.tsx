import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SignalTicker } from './SignalTicker';
import type { ObservatoryEvent } from '../domain';

function event(id: string, over: Partial<ObservatoryEvent> = {}): ObservatoryEvent {
  return { id, time: '00:25:02', type: 'RUN', subject: 'bifrost', body: 'catalogue read', ...over };
}

describe('SignalTicker', () => {
  it('lists the newest signal first', () => {
    render(<SignalTicker events={[event('a'), event('b')]} />);

    // Anchored: the section itself is data-testid="signal-ticker".
    const rows = screen.getAllByTestId(/^signal-[ab]$/);
    expect(rows[0]).toHaveAttribute('data-testid', 'signal-b');
  });

  it('marks warnings so a failure is not read as normal traffic', () => {
    render(<SignalTicker events={[event('w', { level: 'warning', body: '401 Unauthorized' })]} />);

    expect(screen.getByTestId('signal-w').className).toContain('warn');
  });

  it('says the feed is empty rather than rendering nothing', () => {
    render(<SignalTicker events={[]} />);

    expect(screen.getByText('No signal yet.')).toBeInTheDocument();
  });

  it('shows a dash until a rate is known', () => {
    render(<SignalTicker events={[]} />);

    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('shows the rate once known', () => {
    render(<SignalTicker events={[]} rate={138} />);

    expect(screen.getByText('138 msg/min')).toBeInTheDocument();
  });
});
