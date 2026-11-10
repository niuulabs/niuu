import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DecisionDock, RunPausedStrip } from './DecisionDock';

describe('DecisionDock', () => {
  it('waits as a strip, opens the decision in place and folds back', () => {
    render(
      <DecisionDock title="Delivery blocked" waiting="5h">
        <p>Approve or send it back.</p>
      </DecisionDock>,
    );
    const strip = screen.getByTestId('decision-strip');
    expect(strip).toHaveTextContent('1 decision waiting');
    expect(strip).toHaveTextContent('Delivery blocked');
    expect(screen.queryByText('Approve or send it back.')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show the decision: Delivery blocked' }));
    const card = screen.getByRole('region', { name: 'Your decision' });
    expect(card).toHaveTextContent('The workflow needs your decision');
    expect(card).toHaveTextContent('Waiting 5h');
    expect(screen.getByText('Approve or send it back.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Fold the decision back' }));
    expect(screen.getByTestId('decision-strip')).toBeInTheDocument();
  });

  it('leaves out the waiting time when it is not known', () => {
    render(
      <DecisionDock title="Plan review">
        <p>Decide.</p>
      </DecisionDock>,
    );
    fireEvent.click(screen.getByRole('button', { name: /Show the decision/ }));
    expect(screen.getByTestId('decision-card')).not.toHaveTextContent('Waiting');
  });

  it('says which step a stopped run holds and starts it', () => {
    const onStart = vi.fn();
    render(<RunPausedStrip step="Merge the change" busy={false} onStart={onStart} />);

    const strip = screen.getByTestId('run-paused-strip');
    expect(strip).toHaveTextContent('Run paused');
    expect(strip).toHaveTextContent(
      "Merge the change is ready, but the run's sessions are stopped",
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Start the run to carry on with Merge the change' }),
    );
    expect(onStart).toHaveBeenCalledTimes(1);
  });

  it('cannot be started twice while it starts', () => {
    render(<RunPausedStrip step="Merge the change" busy onStart={vi.fn()} />);

    const start = screen.getByRole('button', {
      name: 'Start the run to carry on with Merge the change',
    });
    expect(start).toBeDisabled();
    expect(start).toHaveTextContent('Starting…');
  });
});
