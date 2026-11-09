import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { Session } from '../../domain/session';
import { CoordinatorTag, WorkerOf, WorkerTopics } from './Coordination';

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
    expect(screen.getByTestId('session-coordinator-tag')).toHaveTextContent('Coordinator');
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

  it("lists a focused worker's topics, and nothing for a worker with none", () => {
    const { rerender } = render(
      <WorkerTopics
        worker={{ name: 'wf-run-coder', consumes: ['code.requested'], emits: ['code.changed'] }}
      />,
    );
    const topics = screen.getByTestId('worker-topics');
    expect(topics).toHaveTextContent('wf-run-coder');
    expect(topics).toHaveTextContent('Listens for');
    expect(topics).toHaveTextContent('code.requested');
    expect(topics).toHaveTextContent('Emits');
    expect(topics).toHaveTextContent('code.changed');

    rerender(<WorkerTopics worker={{ name: 'idle', consumes: [], emits: [] }} />);
    expect(screen.queryByTestId('worker-topics')).toBeNull();
  });
});
