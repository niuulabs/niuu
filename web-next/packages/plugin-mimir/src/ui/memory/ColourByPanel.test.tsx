import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ColourByPanel } from './ColourByPanel';
import { FAKE_GRAPH } from '../../testing/fakeMimirService';
import type { KindGroup } from '../../domain/memoryKinds';

function setup(overrides: Partial<React.ComponentProps<typeof ColourByPanel>> = {}) {
  const props: React.ComponentProps<typeof ColourByPanel> = {
    graph: FAKE_GRAPH,
    colour: 'type',
    onColourChange: vi.fn(),
    hiddenGroups: new Set<KindGroup>(),
    onToggleGroup: vi.fn(),
    showQuestions: true,
    onToggleQuestions: vi.fn(),
    questionCount: 2,
    ...overrides,
  };
  render(<ColourByPanel {...props} />);
  return props;
}

describe('ColourByPanel', () => {
  it('renders a checkbox with count per kind group present in the graph', () => {
    setup();
    expect(screen.getByLabelText('Topics')).toBeInTheDocument();
    expect(screen.getByLabelText('Entities')).toBeInTheDocument();
    expect(screen.getByLabelText('Decisions')).toBeInTheDocument();
    expect(screen.getByLabelText('Directives')).toBeInTheDocument();
    expect(screen.queryByLabelText('Preferences')).not.toBeInTheDocument();
  });

  it('shows the unanswered-questions checkbox with its count', () => {
    setup();
    const checkbox = screen.getByLabelText('Unanswered questions');
    expect(checkbox).toBeChecked();
    expect(checkbox.closest('label')).toHaveTextContent('2');
  });

  it('toggles a kind group on click', async () => {
    const props = setup();
    await userEvent.click(screen.getByLabelText('Topics'));
    expect(props.onToggleGroup).toHaveBeenCalledWith('topic');
  });

  it('toggles unanswered questions on click', async () => {
    const props = setup();
    await userEvent.click(screen.getByLabelText('Unanswered questions'));
    expect(props.onToggleQuestions).toHaveBeenCalled();
  });

  it('unchecks a hidden group', () => {
    setup({ hiddenGroups: new Set<KindGroup>(['topic']) });
    expect(screen.getByLabelText('Topics')).not.toBeChecked();
  });

  it('switches to the Proof tab', async () => {
    const onColourChange = vi.fn();
    setup({ onColourChange });
    await userEvent.click(screen.getByRole('button', { name: 'Proof' }));
    expect(onColourChange).toHaveBeenCalledWith('proof');
  });

  it('renders proof counts when colour=proof', () => {
    setup({ colour: 'proof' });
    expect(screen.getByText('High')).toBeInTheDocument();
    expect(screen.getByText('Medium')).toBeInTheDocument();
    expect(screen.getByText('Low')).toBeInTheDocument();
  });

  it('renders age bucket counts when colour=age', () => {
    setup({ colour: 'age' });
    expect(screen.getByText('Today')).toBeInTheDocument();
    expect(screen.getByText('This week')).toBeInTheDocument();
    expect(screen.getByText('This month')).toBeInTheDocument();
    expect(screen.getByText('Older')).toBeInTheDocument();
  });

  it('shows an empty message when the graph has no nodes', () => {
    setup({ graph: { nodes: [], edges: [] } });
    expect(screen.getByText('No pages yet')).toBeInTheDocument();
  });
});
