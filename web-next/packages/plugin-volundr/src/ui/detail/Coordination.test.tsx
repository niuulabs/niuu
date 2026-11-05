import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { Session } from '../../domain/session';
import { CoordinatorTag, WorkerOf, WorkersStrip } from './Coordination';

const session = (id: string, name: string, state: Session['state']): Session =>
  ({
    id,
    name,
    personaName: name,
    state,
    clusterId: 'local',
    startedAt: new Date(Date.now() - 20 * 60_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 5 * 60_000).toISOString(),
    activityStateSince: new Date(Date.now() - 5 * 60_000).toISOString(),
    turnStartedAt: new Date(Date.now() - 5 * 60_000).toISOString(),
  }) as Session;

describe('Coordination header parts', () => {
  it('tags a coordinator', () => {
    render(<CoordinatorTag />);
    expect(screen.getByTestId('session-coordinator-tag')).toHaveTextContent('COORDINATOR');
  });

  it('lists workers with state tone, name and time; each opens its session', () => {
    const onOpen = vi.fn();
    render(
      <WorkersStrip
        workers={[session('w1', 'ticket-1342-gates', 'running'), session('w2', 'docs', 'idle')]}
        onOpen={onOpen}
      />,
    );
    const strip = screen.getByRole('group', { name: 'Workers' });
    expect(strip).toHaveTextContent('ticket-1342-gates');
    expect(screen.getByTestId('worker-chip-w1')).toHaveAttribute('data-tone', 'working');
    expect(screen.getByTestId('worker-chip-w2')).toHaveAttribute('data-tone', 'muted');
    expect(screen.getByTestId('worker-chip-w1')).toHaveTextContent('running 5m');
    fireEvent.click(screen.getByRole('button', { name: /Open worker ticket-1342-gates, Working/ }));
    expect(onOpen).toHaveBeenCalledWith('w1');
  });

  it('draws no strip without workers', () => {
    const { container } = render(<WorkersStrip workers={[]} onOpen={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the coordinator of a worker with a link back, or says only Worker', () => {
    const onOpen = vi.fn();
    const { rerender } = render(
      <WorkerOf
        coordinator={session('lead', 'plan-review-coordinator', 'running')}
        onOpen={onOpen}
      />,
    );
    expect(screen.getByTestId('worker-of')).toHaveTextContent('Worker of plan-review-coordinator');
    fireEvent.click(
      screen.getByRole('button', { name: 'Open coordinator plan-review-coordinator' }),
    );
    expect(onOpen).toHaveBeenCalledWith('lead');
    rerender(<WorkerOf coordinator={null} onOpen={onOpen} />);
    expect(screen.getByTestId('worker-of')).toHaveTextContent(/^Worker$/);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
