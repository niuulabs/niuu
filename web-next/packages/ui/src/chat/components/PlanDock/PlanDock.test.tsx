import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PlanDock } from './PlanDock';
import type { SessionAgent, SessionPlan } from '../../types';

const plan: SessionPlan = {
  tasks: [
    { content: 'Read the gate code', status: 'completed' },
    { content: 'Write the refusal', status: 'completed' },
    { content: 'Run the tests', status: 'in_progress', activeForm: 'Running the tests' },
    { content: 'Open the PR', status: 'pending' },
    { content: 'Tell the operator', status: 'pending' },
  ],
  counts: { total: 5, pending: 2, in_progress: 1, completed: 2 },
};

const agent = (id: string, status: string): SessionAgent => ({
  id,
  kind: 'subagent',
  name: id,
  status,
});

describe('PlanDock', () => {
  it('shows nothing without a plan or without tasks', () => {
    const { container, rerender } = render(<PlanDock plan={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<PlanDock plan={undefined} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<PlanDock plan={{ tasks: [], counts: { total: 0 } }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('summarises progress and the active task by its present-tense form', () => {
    render(<PlanDock plan={plan} />);
    const cap = screen.getByRole('button', { expanded: false });
    expect(cap).toHaveTextContent('2/5 · Running the tests');
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  it('falls to the task content when the active task has no present-tense form', () => {
    render(
      <PlanDock
        plan={{
          tasks: [{ content: 'Write the fix', status: 'in_progress' }],
          counts: { total: 1 },
        }}
      />,
    );
    expect(screen.getByRole('button')).toHaveTextContent('0/1 · Write the fix');
  });

  it('names the next task when none is running, and says when the plan is complete', () => {
    const { rerender } = render(
      <PlanDock
        plan={{
          tasks: [
            { content: 'Done one', status: 'completed' },
            { content: 'Up next', status: 'pending' },
          ],
          counts: { total: 2 },
        }}
      />,
    );
    expect(screen.getByRole('button')).toHaveTextContent('1/2 · Up next');
    rerender(
      <PlanDock
        plan={{ tasks: [{ content: 'Done one', status: 'completed' }], counts: { total: 1 } }}
      />,
    );
    expect(screen.getByRole('button')).toHaveTextContent('1/1 · Plan complete');
  });

  it('counts only running agents, and says nothing when none are running', () => {
    const { rerender } = render(
      <PlanDock
        plan={plan}
        agents={[agent('a', 'running'), agent('b', 'done'), agent('c', 'running')]}
      />,
    );
    expect(screen.getByRole('button')).toHaveTextContent('2 agents running');
    rerender(<PlanDock plan={plan} agents={[agent('a', 'running')]} />);
    expect(screen.getByRole('button')).toHaveTextContent('1 agent running');
    rerender(<PlanDock plan={plan} agents={[agent('a', 'failed')]} />);
    expect(screen.getByRole('button')).not.toHaveTextContent('running');
  });

  it('expands to the checklist with done struck through and the active task marked', () => {
    render(<PlanDock plan={plan} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
    const items = within(screen.getByRole('list', { name: 'Plan tasks' })).getAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual(plan.tasks.map((task) => task.content));
    expect(items[0]).toHaveClass('niuu-plan-dock__item--done');
    expect(items[2]).toHaveClass('niuu-plan-dock__item--active');
    expect(items[2]).toHaveAttribute('aria-current', 'step');
    expect(items[3]).toHaveClass('niuu-plan-dock__item--pending');

    fireEvent.click(screen.getByRole('button'));
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });
});
