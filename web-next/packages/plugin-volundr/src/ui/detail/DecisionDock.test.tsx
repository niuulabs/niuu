import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DecisionDock } from './DecisionDock';

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
});
